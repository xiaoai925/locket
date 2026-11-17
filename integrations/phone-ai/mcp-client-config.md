# 把 Locket 记忆接成大脑的 MCP 工具

路径 (A)：phone-ai 的大脑（或它接的模型/agent）作为 **MCP 客户端**，把 Locket 的
记忆能力当作一组工具调用。Locket 以 stdio 方式跑 `locket mcp`，暴露 spec §8 的工具。

## 1. 前置

```sh
# 在 Locket 仓里安装（含 MCP 可选依赖）
pip install -e '.[mcp]'      # 或：pip install 'locket-memory[mcp]'
locket init                  # 若还没有记忆仓
```

`locket mcp` 读环境变量决定「以谁的身份、操作哪个仓、属于哪次会话」：

| 变量 | 含义 | 示例 |
|---|---|---|
| `LOCKET_HOME` | 记忆仓位置 | `/data/data/com.termux/files/home/.locket`（手机）或 `~/.locket` |
| `LOCKET_USER` | 租户标识（单仓模式填 local） | `local` |
| `LOCKET_AGENT` | 写入者标签（记进 commit） | `brain` |
| `LOCKET_SESSION` | 本次会话 id（设了就写会话分支，需显式 end_session） | 大脑当前场次 id，如 `live-2026-11-03` |

> 不设 `LOCKET_SESSION` 时，每次写入即时合并进长期记忆（适合「大脑想记就记」）。
> 设了则本场记忆先进会话分支，场次结束调 `memory_end_session` 再并入——便于「一场直播作为一次 PR」审阅。

## 2. 暴露的工具

`locket mcp` 注册这些工具（名字即工具名）：

- `memory_recall(query, k=5, about=None, include_inactive=False)` — 检索相关记忆 + 相关未解决冲突
- `memory_get(id)` — 读一条
- `memory_list(about=None, type=None)` — 概览
- `memory_remember(text, type="fact", about="self", title=None, tags=None, importance=None, sensitivity=None, quote=None)` — 新增
- `memory_update(id, text=None, reason=None)` — 认知更正（原地改）
- `memory_supersede(id, text, effective=None, title=None)` — 世界变化（旧的留痕、新建取代）
- `memory_forget(id, reason=None)` — 软遗忘（「忘掉我」）
- `memory_why(id)` / `memory_history(id)` — 来源链 / 版本史
- `memory_conflicts(status="open")` / `memory_resolve(id, note=None)` — 冲突列出 / 解决
- `memory_end_session()` — 合并本场会话分支进长期记忆

大脑按脑区选 `type`/`about`（见 `README.md` 的类型对齐表）。

## 3. 客户端配置样例

多数 MCP 客户端用一段 JSON 声明「怎么启动这个 server」。通用形态：

```json
{
  "mcpServers": {
    "locket": {
      "command": "locket",
      "args": ["mcp"],
      "env": {
        "LOCKET_HOME": "/data/data/com.termux/files/home/.locket",
        "LOCKET_USER": "local",
        "LOCKET_AGENT": "brain",
        "LOCKET_SESSION": "live-2026-11-03"
      }
    }
  }
}
```

- 若 `locket` 不在 PATH，把 `command` 换成解释器 + 模块：
  ```json
  { "command": "python", "args": ["-m", "locket", "mcp"], "env": { ... } }
  ```
- 每场直播换 `LOCKET_SESSION`，让「这一场的记忆」成为一次可并入/可审阅的会话分支。
- 手机（Termux）里路径用 `$HOME/.locket`；电脑同机用 `~/.locket`。

## 4. 典型调用流（大脑一轮互动）

1. 来观众消息 → `memory_recall("<观众ID> <话题>", about="person:<观众ID>")` 取回相关经历，
   同时拿到 `open_conflicts`（有矛盾就知道这里不确定）。
2. 大脑决策、表达（这步在 phone-ai，不经 Locket）。
3. 回复实际播放后，若值得记 → `memory_remember(text="...", type="event",
   about="person:<观众ID>", quote="<原话>")`。
4. 观众明确说偏好（「别总反问」）→ `memory_remember(type="preference", about="person:<观众ID>")`；
   若与旧偏好矛盾，Locket 会按 §6.2 记成待确认冲突，大脑下次 recall 能看到。
5. 记错了 → `memory_update(id, text="<更正>")`；情况变了 → `memory_supersede(id, "<新>")`；
   「忘掉我」→ `memory_forget(id)`。
6. 场次结束（若用了会话分支）→ `memory_end_session()`。

## 5. 何时用 MCP、何时用 HTTP

- **MCP (本文)**：大脑本身是 MCP-capable / 接了支持 MCP 的模型时最自然。
- **HTTP (`brain_memory_adapter.py`)**：现有 Python 大脑（`live/brain/*`）直接 import 一个客户端更省事。

两者底层同一个 `service.py`，语义一致，按接入形态二选一。
