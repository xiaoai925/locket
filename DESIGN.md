# Locket：以 Git 存储模型为基础的 AI 记忆系统 — 设计稿 v0.1（已被 SPEC.md 取代）

## 1. 核心思路

Git 解决的问题是：**内容如何不可变地存储、如何追踪变化、如何在多条历史线之间合并**。
AI 记忆面临的问题几乎一一对应：

| AI 记忆的需求 | Git 的对应机制 |
|---|---|
| 记住一条事实 / 经历 | blob（内容寻址，不可变） |
| 把记忆按主题组织 | tree（目录树） |
| 一次对话学到了什么 | commit（快照 + 说明 + 父提交） |
| 当前会话的临时记忆 vs 长期记忆 | branch（`session/*` vs `main`） |
| 还没确认要不要记的内容 | index / staging area |
| 把会话记忆沉淀为长期记忆 | merge |
| 新旧认知矛盾 | merge conflict |
| 发现某条记忆是错的，要纠正 | revert（纠正但保留历史） |
| 这条记忆从哪来的？ | log / blame（溯源） |
| 认知随时间怎么变化 | diff |
| 遗忘 | 从 tree 移除（软遗忘）+ gc（硬遗忘） |
| 把很多琐碎经历压缩成结论 | squash / rebase |
| 重要节点（如"项目上线"） | tag |
| 被打断的任务上下文 | stash |
| 把 A 项目的经验迁移到 B 项目 | cherry-pick |

关键设计原则：**路径 = 一条记忆的身份；blob = 这条记忆在某个版本的内容。**
例如 `user/preferences/language.md` 这个路径始终代表"用户的语言偏好"，
内容从"中文"变成"中英混合"时，路径不变、blob 变化 —— 于是 `diff` / `log` 天然就能回答
"我对用户语言偏好的认知是怎么演变的"。

## 2. 存储布局

```
.mem/
├── HEAD                     # 当前所在分支，如 "ref: refs/heads/main"
├── objects/ab/cdef...       # 对象库：zlib("<type> <len>\0<content>")，SHA-1 寻址
├── refs/
│   ├── heads/main           # 长期记忆
│   ├── heads/session/<id>   # 每次对话的工作记忆
│   └── tags/<name>
├── index                    # 暂存区（JSON），待提交的候选记忆
├── logs/                    # reflog，记录 ref 的每次移动（可恢复误操作）
├── stats/access.jsonl       # 访问统计（不进对象库，见 §4）
└── cache/
    └── recall.sqlite        # 检索索引：blob_hash → embedding / 关键词（可随时重建）
```

**对象格式与 Git 完全兼容**（同样的 header、SHA-1、zlib、tree/commit 文本格式）。
好处：`git --git-dir=.mem log --stat`、`git diff`、`gitk` 等现成工具可以直接查看记忆库，
调试与可视化成本几乎为零。上层语义（检索、合并策略、遗忘）由 Locket 自己实现。

## 3. 对象模型

### 3.1 Memory blob（记忆原子）

```markdown
---
type: fact            # fact | preference | episode | procedure | summary
confidence: 0.9       # 0~1
importance: 0.7       # 0~1，影响遗忘
source: session/2026-10-30-a1b2   # 产生它的会话
created: 2026-10-30T17:30:00+08:00
---
用户的主力开发环境是 Windows 11，Python 使用 `py -3` 调用。
```

- 内容寻址 ⇒ 完全相同的记忆自动去重；embedding 按 blob hash 缓存，只算一次。
- 修改 confidence 也会产生新 blob —— 这是刻意的："信念强度变化"本身也是值得追溯的历史。

### 3.2 Tree（记忆的组织结构）

```
/
├── user/            # 关于用户：身份、偏好、习惯
├── world/           # 通用知识、外部事实
├── projects/<name>/ # 项目相关
├── procedures/      # 怎么做某件事（技能、流程）
└── episodes/2026/10/30-xxx.md   # 情景记忆（原始经历，日后会被 squash）
```

路径由 LLM 在 `add` 时生成（给出现有目录结构作为参考，倾向复用已有路径，从而形成"更新"而非"新增"）。

### 3.3 Commit（一次学习）

```
tree   <hash>
parent <hash>
author agent <agent@locket> 1761816600 +0800
committer agent <agent@locket> 1761816600 +0800

learn: 用户在 SI_Ag 项目中想做基于 git 模型的记忆系统

X-Session: 2026-10-30-a1b2
X-Trigger: session-end
```

commit message 由 LLM 总结生成；附加元数据用 trailer 形式写在 message 末尾（保持 git 兼容）。

## 4. 可变元数据放哪里？

"访问次数 / 最近访问时间"变化极其频繁，若每次都 commit 会让历史爆炸。
因此借鉴 git index 里的 stat cache：**这类数据不进对象库**，写在 `stats/access.jsonl`，
以 `path + blob hash` 为键。它只服务于检索排序与遗忘决策，丢了可以接受。

## 5. 生命周期（一次对话的完整流程）

```
            ┌──────────── 会话开始 ────────────┐
main ──●────┤ branch session/<id> from main    │
            │ recall(query) 从 HEAD tree 检索  │
            └──────────────┬───────────────────┘
                           │ 对话中：agent 调用 mem.add() → 进入 index
                           ▼
                 会话结束：mem.commit(LLM 生成 message)
                           │
                           ▼ （异步 "睡眠巩固"，consolidation）
main ──●─────────────────●  merge session/<id> → main
                          │  - 三方合并（按路径）
                          │  - 语义冲突检测 + LLM 裁决
                          │  - episode squash 为 summary
                          ▼
                 定期 gc：衰减 → 软遗忘 → 硬遗忘
```

### 5.1 检索 recall
1. 只在 **HEAD 的 tree** 中检索 —— 保证看到的是一个一致版本的记忆（可 checkout 历史版本"回到过去的自己"）。
2. 混合打分：`score = α·语义相似度 + β·BM25 + γ·importance + δ·recency(访问时间衰减)`。
3. 返回结果附带 path 与 blob hash，便于 agent 后续引用或修正。

### 5.2 合并 merge（系统的核心难点）
Git 的冲突只能检测"同一路径两边都改了"，但记忆的矛盾经常发生在**不同路径**之间。所以分两层：

| 层次 | 检测方式 | 处理 |
|---|---|---|
| 路径冲突 | 标准三方合并：base / ours / theirs 同一路径都改了 | LLM 裁决 |
| 语义冲突 | 对每个新增/修改的 blob，在目标分支检索 top-k 相似记忆，让 LLM 判断是否矛盾 | LLM 裁决 |

裁决策略（可配置，按顺序尝试）：
1. **明确更新**：新信息是对旧信息的修正（"用户搬家了"）→ 取新的，旧的留在历史里。
2. **置信度优先**：两者都不确定 → 取 confidence 高者。
3. **并存**：两者在不同条件下都成立 → 合并成一条带条件的记忆。
4. **无法判断**：保留冲突标记，写入 `conflicts/` 目录，下次对话时提示 agent 向用户确认。

合并提交拥有两个 parent，所以 `log --graph` 可以清楚看到每段长期记忆是从哪次对话沉淀来的。

### 5.3 遗忘 forget / gc
- **衰减**：`retention = importance × f(访问频率) × e^(-λ·距上次访问时间)`
- **软遗忘**：retention 低于阈值 → 从 main 的 tree 中删除（提交 `forget: ...`）。
  记忆仍在历史中，可通过 `log` 找回，类似"想不起来但并没真的消失"。
- **压缩**：大量同主题 episode → LLM 总结为一条 summary，提交 `squash: ...`。
- **硬遗忘**：`gc` 删除不可达且超过保留期的对象（隐私删除请求也走这条路：
  需要改写历史 —— 类似 `git filter-repo`，这是唯一允许改写历史的操作）。

## 6. 接口设计

### 6.1 Python API
```python
mem = MemRepo.open(".mem")
mem.checkout_session("2026-10-30-a1b2")          # 从 main 开分支
hits = mem.recall("用户的开发环境", k=5)          # -> [Memory(path, hash, content, score)]
mem.add("用户偏好中文交流", type="preference")      # LLM 决定路径，进入 index
mem.commit()                                      # LLM 写 message
mem.consolidate()                                 # 合并到 main + squash
mem.log(path="user/preferences/language.md")      # 某条记忆的演变史
mem.blame("user/preferences/language.md")         # 来自哪次会话
mem.revert(commit_hash)                           # 撤销一次错误学习
mem.gc()
```

### 6.2 CLI
```
locket init | add | commit | log | diff | show | blame
locket branch | checkout | merge | revert | tag | stash
locket recall "<query>" | forget <path> | consolidate | gc
```

### 6.3 MCP Server
把 `recall / add / commit / log / revert` 暴露为 MCP 工具，即可接入 Claude Code 或任意支持 MCP 的 agent。

## 7. 模块划分

```
locket/
├── objects.py     # blob/tree/commit 读写、hash、zlib —— 纯标准库
├── refs.py        # HEAD、分支、tag、reflog
├── index.py       # 暂存区
├── repo.py        # 高层操作：commit / checkout / log / diff / merge 机械部分
├── recall.py      # 检索索引（sqlite + 可插拔 embedding）
├── llm.py         # 可插拔 LLM 接口：路径生成、commit message、冲突裁决、摘要
├── consolidate.py # 合并策略、squash、语义冲突检测
├── forget.py      # 衰减、软遗忘、gc
├── cli.py
└── mcp_server.py
```

`objects / refs / index / repo` 不依赖任何 LLM，可独立测试；LLM 只出现在决策点上，且都有无 LLM 的降级策略
（如路径用 slug、message 用模板、冲突默认"新者胜"）。

## 8. 实施路线

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| M1 | 对象库 + refs + index + commit/log/diff | `git --git-dir=.mem log -p` 能正确显示 |
| M2 | recall（关键词 + embedding 混合检索） | 给定 query 能返回相关记忆 |
| M3 | 会话分支 + merge（路径冲突）+ LLM 接入 | 两个会话矛盾的记忆被正确裁决 |
| M4 | 语义冲突检测、squash、遗忘与 gc | 长期运行后记忆规模稳定 |
| M5 | CLI 完善 + MCP Server | Claude Code 可直接使用 |

## 9. 待定问题

1. 实现语言：Python（推荐，标准库即可完成 M1）还是 TypeScript？
2. LLM 与 embedding 提供方：Claude API？本地模型？
3. 使用场景：单用户个人助理 / 多 agent 共享记忆（多 agent 时可借鉴 git remote：每个 agent 一个仓库，push/pull 同步）？
4. 是否需要多用户隔离（每个用户一个仓库 vs 一个仓库多个顶层目录）？
