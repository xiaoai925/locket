# phone-ai ↔ Locket 对接

把 **phone-ai**（虚拟主播「大肥鱼」的认知大脑）和 **Locket**（本项目，PMF 记忆系统）接起来，
让大脑的长期记忆由 Locket 承载。两边**互不改源码**：Locket 只通过 **MCP** 或 **HTTP（serve-api）**
对外提供记忆能力，phone-ai 作为客户端调用。这正是 phone-ai 文档里既定的方向——它的大脑实现计划
明确写着「SI_Ag 继续保存长期记忆」「长期记忆适配层直接调用 live_memory 及 SI_Ag，不另建向量库」
（`docs/brain-implementation-plan-v1.md` B1/B6），SI_Ag 就是 Locket。

> 本目录只是**对接样例与落地脚本**，不属于 Locket 引擎本身，也不触碰 phone-ai 源码。

## 1. 职责边界（谁管什么）

| 能力 | 归谁 | 说明 |
|---|---|---|
| 记忆本体（情景/语义/偏好）的存储 | **Locket** | git 仓里的 markdown，每条带来源/时间/人物归属，可更正、可遗忘、可带走 |
| 检索（关键词 + 本地向量语义） | **Locket** | `recall` 混合打分；本地离线可用 |
| 同步 / 墓碑传播 / 冲突记录 | **Locket** | `pull/push/gc`；矛盾按 §6.2 记为待确认冲突 |
| 注意 / 工作记忆 / 情绪评价 / 目标决策 / 表达 | **phone-ai 大脑** | B0–B4，留在 `live/brain/*`，不进 Locket |
| 把值得记住的经历提炼成摘要写回记忆 | **phone-ai 发起，Locket 落库** | B6 回顾：大脑决定「记什么」，Locket 负责「怎么存/怎么追溯」 |

一句话：**大脑负责「想」，Locket 负责「记」**。两者通过下面任一通道对接。

## 2. 两条对接路径

### (A) MCP：大脑作为 MCP 客户端调 `locket mcp`
Locket 以 stdio 方式暴露记忆工具（`memory_recall/remember/update/supersede/forget/why/...`）。
适合「大脑本身就是一个 MCP-capable agent / 接了 MCP 的模型」。配置见 `mcp-client-config.md`。

```
大脑(MCP client) ──stdio──► locket mcp ──► Locket 引擎 ──► git 记忆仓
```

### (B) HTTP：大脑用 `locket serve-api`
Locket 跑一个本地 HTTP/JSON 服务，大脑用普通 HTTP 调。适合现有 Python 大脑（`live/brain/*`）
直接 import 一个薄适配层。样例见 `brain_memory_adapter.py`。

```
大脑(Python) ──HTTP──► locket serve-api :18874 ──► Locket 引擎 ──► git 记忆仓
```

两条路底层是同一个 `service.py`，语义完全一致，按大脑的接入形态二选一即可。

## 3. 记忆跑在哪 / 数据流

Locket 引擎是纯标准库 Python，哪台机器跑都行：

- **大脑在电脑跑（现状）**：Locket 也在同一台电脑跑，大脑走 `localhost`，无需隧道。
  这是最简单、已被 phone-ai 采用的形态（`live/package_locket.py` 已在打包 Locket）。
- **记忆存进手机、断网也能用**：手机侧用 **Termux** 跑 Locket（`termux-setup.sh`），
  记忆仓是手机本地的 git 克隆 → 离线可存取、可本地向量检索；联网时 `push` 到 Gitea，
  其它设备 `pull` 同步。大脑经 adb 隧道（`连接手机大脑.ps1` 用的 `reverse tcp:18874`）
  访问手机上的 `locket serve-api`。

```
┌ 手机(Termux, 离线可用) ┐      adb 隧道       ┌ 电脑：大脑 ┐
│ locket serve-api :18874│◄────reverse 18874──┤ 调记忆 API │
│  本地 git 记忆仓        │                    └───────────┘
│  本地向量索引(.git/pmf) │──push/pull──► Gitea(云端权威副本/跨设备)
└────────────────────────┘
```

> 用户在手机上的「设置」里选 本地 / 云端 / 混合 模式（见 Locket `locket settings` 与 Web 设置页），
> 决定记忆从本地读还是直连云端、要不要自动同步。对接层不关心模式，只管调 API。

## 4. 记忆类型对齐（脑区 → PMF）

phone-ai 按脑区把记忆分类，映射到 PMF 的 `type` + `about`：

| 大脑里的东西 | PMF `type` | `about` | 备注 |
|---|---|---|---|
| 情景经历「谁在何时说过/做过什么」（海马体） | `event` | `person:<观众ID>` | 带 `quote` 原文、来源会话；落到 `episodes/` |
| 稳定人设/角色设定 | `fact` | `self` | 很少变；变了用 `correct` |
| 观众偏好「别总反问」「短一点」 | `preference` | `person:<观众ID>` | B6 表达偏好；两个来源才归纳 |
| 关系摘要 | `summary` | `person:<观众ID>` | `derived_from` 指向原经历；摘要不覆盖原记忆 |
| 通用知识 | `fact` | `world` | — |
| 做某事的流程/技能 | `procedure` | `self` | — |

对齐要点（phone-ai 文档 B6 的规则，Locket 天然支持）：
- 「两个独立来源才归纳偏好」→ Locket `summary` + `derived_from`（多来源 id）。
- 「相反陈述标记待确认冲突」→ Locket 语义冲突检测 §6.2（同 `about` 下矛盾记为 `contradiction`）。
- 「喜欢两种饮料可共存，不自动制造冲突」→ 冲突检测按 `about` 域、保守阈值，不会把并存偏好误判。
- 「明确更正保留有时间的更新证据」→ `correct`（原地改、留历史）/ `supersede`（旧的加 `valid_to`、新建一条）。
- 「退出记忆 / 忘掉我」→ `forget`（软遗忘，历史可回溯）或 `redact`（硬删 + 墓碑随同步传播）。

## 5. 快速开始

- 电脑同机：`pip install -e .[mcp]`（在 Locket 仓）→ `locket init` → 选 (A) 配 MCP，或 (B) `locket serve-api --port 18874`。
- 手机离线：把 `termux-setup.sh` 拷到手机 Termux 跑一遍（见脚本头部注释）。
- 大脑接 HTTP：参考 `brain_memory_adapter.py`，`LocketMemory(base_url=..., token=...)`。
- 大脑接 MCP：参考 `mcp-client-config.md`。

鉴权：直连本机 `serve-api`（单仓模式）可不带 token；走多租户/云端时按 `GITEA-SETUP.md` 带
`Authorization: Bearer <Gitea 令牌>`。
