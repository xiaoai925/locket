# Locket Cloud 架构：多租户的"云上记忆存储系统"

> 本文描述**参考实现 Locket 的产品架构**，区别于 `SPEC.md`（跨实现的 PMF 格式标准）。
> 目标：让很多人都能用的云上 AI 记忆存储——每个用户拥有自己的记忆仓，服务器托管一堆用户仓，
> 网页登录即用、零安装，Agent 通过 MCP 接入；同时保留 PMF"用户拥有、可带走"的内核。

## 1. 一句话架构

**Gitea 提供"多账号 + 每人私有 git 仓 + 权限 + 传输"，Locket 在其上提供"记忆语义层"（检索/合并/冲突/墓碑/视图/读范围），Go+Vue 前端与 MCP 让人和 Agent 都能用。**

```
          用户（浏览器）          Agent（Claude Code 等）        高级用户
             │ Web(Go+Vue+TS)        │ MCP                        │ CLI(locket)
             ▼                       ▼                            ▼
        ┌─────────────────────────────────────────────┐     本地记忆仓
        │ Locket 服务层（Python）                       │     （push/pull 同步，
        │  HTTP/JSON API  +  MCP 适配器                 │◄──────到自己的 Gitea 仓）
        │  PMF 引擎：recall/remember/merge/conflict/    │
        │           tombstone/view/scope/sensitivity    │
        │  多租户：每请求定位到"该用户的仓"             │
        └───────────────────────┬───────────────────────┘
                                 │ 服务端每用户工作副本 clone/fetch/push
                                 ▼
        ┌─────────────────────────────────────────────┐
        │ Gitea（自托管）                               │
        │  账号 / OAuth2 / 每人私有记忆仓 / 访问控制     │
        │  git 传输 / 网页 git 浏览                      │
        └─────────────────────────────────────────────┘
```

设计原则：**Locket 不重造账号、存储、权限、git 传输——这些 Gitea 已经久经考验**；
Locket 只做 Gitea 不懂的那一层"记忆语义"。

## 2. 租户模型

- 一个 Gitea 用户 ↔ 一个私有记忆仓（建议固定名，如 `<user>/memory`）。仓即 PMF 格式的普通 git 仓。
- **隔离靠 Gitea 的 repo 级权限**：A 读不到 B 的仓。服务层对每个请求只打开请求者的那个仓，绝不跨租户。
- 共享/分享走 PMF **视图**（`SPEC §9.2`）：从 main 按规则生成只读子集，映射到另一个仓或 Gitea 协作者授权
  （例如只把 `projects/**` 且 `public` 的记忆分享给某人/某 Agent）。
- `secret` 级记忆用 age 加密（`SPEC §9.3`）：即便仓托管在服务器/ Gitea 上，平台也读不到明文——这是云存储的卖点。

## 3. 组件

### 3.1 Gitea（身份 + 托管）
已自托管。充当 OAuth2 身份提供方、git 托管、per-repo ACL、网页 git。记忆仓就是这里的私有仓。

### 3.2 Locket 服务层（Python，新增）
多租户 HTTP/JSON API + MCP 端点。每个请求：
1. 用 Gitea OAuth 令牌验明用户身份；
2. 定位该用户的记忆仓，在**服务端维护的每用户工作副本**上执行引擎操作；
3. 提交到该副本并 `push` 回 Gitea（复用已完成的 `sync/push/pull`）。
引擎已是"给定 `root` 路径操作一个仓"，多租户 = 把 `root` 换成"该用户的副本路径"，改动集中、风险可控。
写操作按仓串行化；`commit_changes` 的 compare-and-swap 已能挡并发丢更新。

### 3.3 Web 应用（Go + Vue + TS，新增）
Gitea OAuth 登录，调 Locket JSON API。页面：
- 按命名空间浏览（self/people/projects/world/procedures/episodes）；
- recall 搜索框；
- 记忆页：渲染正文 + 元数据 + **来源链**（why）+ **版本时间线**（diff＝认知演变）；
- 冲突队列 / 解决；会话分支的**合并审批**（见 §4）；redact 删除。
实现时读 Gitea 源码（`modules/markup` 渲染、`routers/web/repo` 的文件树/commit/diff/blame）借鉴 UX 与做法。

### 3.4 MCP 服务器（新增）
`SPEC §8` 的工具（recall/remember/update/forget/why/history/conflicts/end_session）薄封装在服务层之上，
按用户鉴权。任意支持 MCP 的 Agent 接入自己的记忆仓。

### 3.5 CLI（已有，保留）
高级用户继续在本地跑 `locket`，把本地仓 `push/pull` 到自己的 Gitea 仓。不变。

## 4. 多用户带来的新引擎需求（当前尚缺）

| 需求 | 规范依据 | 现状 |
|---|---|---|
| **读范围 / 敏感级别强制**：谁能看到哪些记忆 | §8 scope、§9 sensitivity | ✅ `scope.py`：每次读按 `Scope` 过滤、越权 get 不泄露存在；写按 `can_write`/敏感级守卫 |
| **合并审批**：Agent 写会话分支＝PR，所有者在 Web 审批合并 | §6.1 `main_requires_approval` | ✅ 引擎 `approve/pending`；开启时 agent 会话待批、所有者直写不受限；Web 审批 UI 待补 |
| **视图 / 加密 / 签名校验** | §9.2 / §9.3 / §5.2 | ✅ `views.py`（确定性只读分享、secret 永不入视图）/ `crypto.py`（X25519+ChaCha20 端到端，服务器不持明文）/ `signing.py`（检测校验，gpgsign 时提交带 -S） |
| **语义冲突检测**（不同路径的矛盾） | §6 | ✅ `semantic.py`：启发式（token 重叠）默认关，`policy.semantic_conflicts="heuristic"` 开启；合并时对新记忆查同主题近似并记 §6.2 冲突；LLM 可插拔；`detect-conflicts` 只读预览 |
| 每用户工作副本的生命周期、并发与配额 | — | 服务层新增 |

## 5. 分阶段建造计划

| 阶段 | 产出 | 状态 |
|---|---|---|
| **P1a** | 同步 + 墓碑传播（`remote/pull/push/sync/gc`） | ✅ 已完成 |
| **P1b** | 引擎服务边界：`locket.service` + HTTP/JSON API（`locket serve-api`，recall/get/remember/update/supersede/forget/why/history/list/conflicts/resolve），按仓定位 | ✅ 已完成（stdlib http.server，实时验证） |
| **P1c** | MCP 服务器（服务层之上的薄适配，`locket mcp`，官方 mcp SDK 可选依赖） | ✅ 已完成 |
| **P1d** | 存储读路径优化：派生 SQLite 快查索引（方案 A，见 §8），修 O(N) 全量解析 | ✅ 已完成（读 ~65× 加速） |
| **P2** | Gitea 集成：`tenancy.py`（token 鉴权 → 每用户仓解析、服务端副本 clone/pull/push）、`serve-api --gitea` | ✅ 核心完成；OAuth Web 授权码流留给 P3 前端 |
| **P2b** | 读范围 + 敏感级别强制（§8/§9）、写并发串行化与配额 | ✅ 核心完成（scope.py 强制 + 每仓写锁；配额待补） |
| **P3** | Web 应用（Go+Vue+TS，`web/`）：Go 服务器（OAuth+静态+`/api` 反代）+ Vue SPA（浏览/recall/记忆页：来源链+认知演变时间线/冲突/redact） | ✅ 首版完成（合并审批待补） |
| **P4** | L3：视图分享、加密（secret/age）、签名与校验、审批工作流 | ✅ 引擎+CLI 完成（views/crypto/signing/approve）；Web 审批 UI 与 age 互通待补 |
| **P5** | 语义冲突检测（启发式+可插拔 LLM）、从其它系统导入、一致性测试套件 | ✅ 语义冲突完成（semantic.py，默认关，policy 开启）；导入/一致性套件待补 |
| **P6** | **认知层（大脑）**：本地向量索引做语义召回（见 §7/§7b），与 BM25 混合打分，派生可重建、离线可用 | ✅ 引擎完成（embed.py/vector.py，默认关，policy 开启）；真·语义需可选本地模型 |
| **P7** | **设备设置**：用户在手机上选记忆模式（本地/云端/混合）、连哪个云、用哪种 embedding、隐私与同步 | ✅ settings.py（设备本地、不同步、token 脱敏）+ API /settings + Vue 设置页 + CLI settings |
| **P8** | auto_sync 闭环、本地模型 embedding、Web 审批/视图/冲突 UI、phone-ai 对接 | ✅ cloud/hybrid 自动 pull/push；embed_model（可选 [model]）；/pending·/approve·/views·/detect-conflicts + Vue 页面；integrations/phone-ai（MCP/HTTP 适配 + Termux 脚本） |

## 6. 关键取舍记录

- **为什么架在 Gitea 上而不自研后端**：账号/OAuth/私有仓/权限/git 传输全现成且久经考验；自研多租户后端工作量和安全风险都最大。Locket 专注记忆语义层。
- **引擎 Python、前端 Go+Vue**：引擎沿用现有 Python 实现，经 HTTP/JSON API 解耦；前端与 Gitea 同栈（Go+Vue），便于借鉴与将来可能的深度集成。
- **一人一仓而非共享大仓**：天然契合"用户拥有"，隔离直接复用 Gitea repo 权限；共享走视图而非共享写。
- **隐私**：`secret` 端到端加密，服务器/ Gitea 不持明文。
- **向量库在认知层（大脑），不在存储层**：见 §7、§8。记忆本体由存储层持久化（源真相、可带走），
  向量库是大脑召回用的派生索引、可随时重建。两层分开，才能同时拿到"向量检索体验"和"用户拥有"。

## 7. 认知层（大脑）：向量数据库用在这里

系统分成两层，各管一件事：

- **存储层（记忆本体）**：durable、可审计、可带走的**源真相**。追求持久与性能，不负责"智能"。见 §8。
- **认知层（大脑）**：用记忆来"思考"的那层——语义召回、巩固压缩、冲突裁决、遗忘决策（对应 `SPEC` 第 4 层策略）。
  **向量数据库属于这里**，服务于大脑的语义召回，而不是记忆的存储。

大脑的语义召回：
- **每设备一个本地向量索引**（派生 SQLite，存在 `.git/pmf/`），对该设备本地记忆做离线语义检索。
  不强制外部向量服务；规模增长后可换 pgvector/Qdrant（§8 同理）。
- 对话时 query embedding + 余弦最近邻取回相关记忆，**与 BM25 混合打分**，受读范围/敏感级别约束（§4、P2b）。
- 向量索引**派生于存储层、可随时重建**：遍历仓库重算 embedding（按内容 hash 缓存，只算一次）；
  `forget`/`redact` 时一并清除。不是源真相，换机/损坏都能重建。
- **可插拔 embedding**：默认纯本地零依赖 embedder（离线、任何环境可跑）；可选本地模型或 Claude/云 embedding
  作更高质量后端；都不可用时降级回纯 BM25，保持"低采用成本"。

> 向量库在**大脑**（召回），**不**在存储层。记忆本体仍由存储层持久化，于是"向量检索体验"与
> "用户拥有/可审计/可带走"两者兼得。

## 7b. 本地优先 + 手机离线 + 与 phone-ai 结合

目标（用户确认）：记忆**真的存进手机、断网也能用**；手机能**本地向量检索本地记忆**，也能**直接调云上记忆**；
本地记忆**可提交上传**。这正是 git 的本地优先模型，Locket 的同步/墓碑/索引全部适用。

```
┌─ 手机（离线可用）──────────────┐        在线时
│ 本地 Locket 仓（git 克隆）     │   ┌──────────────────┐
│  · 记忆本体（离线存取）        │   │ 云端 serve-api     │
│  · 本地向量索引（.git/pmf）    │◄──┤  （Gitea 多租户）  │
│  · 本地提交                    │push│  权威副本/跨设备   │
└───────────────┬────────────────┘pull└──────────────────┘
                │ 也可在线直连云端 API 查“云上记忆”
                ▼
        大脑（phone-ai「大肥鱼」）：记住/检索/巩固 → 调 Locket（MCP 或 serve-api）
```

- **双路召回**：本地（离线、向量+BM25 混合）优先；在线时可向云端 API 补充查询权威/跨设备记忆，结果合并。
- **可提交上传**：本地写入先进本地仓（离线也成立），联网 `push` 到 Gitea；云端 `pull` 下发到其它设备。
  冲突、墓碑传播、gc 全走已完成的同步机制。
- **手机如何跑引擎**：Locket 引擎是纯标准库 Python，手机侧用 Termux 跑（会话最早已验证该手机可装 git/python）；
  phone-ai 的大脑经 **MCP（`locket mcp`）或 serve-api** 存取记忆，不需把引擎逻辑塞进 Android 应用。
- **与 phone-ai 的边界**：Locket 只做"记忆本体 + 检索 + 同步"（海马体/情景+语义记忆）；大脑的注意/情绪/决策/表达仍在 phone-ai。
  两者通过 MCP/API 对接，互不侵入对方源码（符合 phone-ai 文档"不改 SI_Ag 源码"的意图）。
- **embedding 的离线现实**：纯本地零依赖 embedder 保证流程与离线可用，但真正的语义质量（"交通工具"↔"特斯拉"）
  需要一个能在设备上跑的小模型（可选后端）；接口可插拔，质量与依赖按设备能力取舍。

## 8. 存储层：性能与底座（待定）

诉求是**提升记忆存储本身的性能、可能换一种存储方式**。这与"认 Gitea 做后端"耦合，要一起定。
先厘清一个事实：当前读路径每次 `recall/list` 都 `ls-tree` + 读全部 blob + 解析 YAML，是 **O(N) 全量解析**，
这是现在最直接的瓶颈，而且**不换底座也能修**。候选：

| 方案 | 存储底座 | 性能 | 代价 |
|---|---|---|---|
| **A. git + 派生快查索引（SQLite）** | 仍是 git markdown（源真相） | 读快（索引化 list/get/filter）；写仍走 git 提交 | 几乎不丢 PMF/Gitea 后端；索引派生、可重建。**推荐起点** |
| **B. 数据库为主 + git 导出** | 每用户一个 DB（SQLite/PG） | 读写都快、并发好、可建索引 | git 退化为导出/备份，"存储即 git 仓/Gitea 实时后端"被削弱 |
| **C. 只优化 git**（分片目录、打包 episodes） | git | 中等，拖后天花板 | 改动最小，规模上限低 |

建议：先做 A（给读路径加派生索引），解决"很多人用"初期的性能问题且不动架构；
若预期单用户数十万条或高写入吞吐，再评估 B。
