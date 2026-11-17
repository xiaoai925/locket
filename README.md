# Locket

Locket 是 [Portable Memory Format（PMF）](SPEC.md) 的参考实现。它把 AI 的长期记忆存在一个归你所有的 Git 仓库里：每条记忆是一个 Markdown 文件，可以追溯来源、更正、删除，也可以随仓库带到另一个 AI。

当前版本 0.1 是最小可用版本（规范 L1 级加合并）：

- 每次对话写入自己的会话分支，结束时自动合并进 `main`，不需要人工审批
- 检索默认可离线运行，支持 BM25 和可选向量混合检索
- 通过 Claude Code 钩子自动注入记忆、自动合并
- 多设备同步：把记忆仓托管在任意 Git 远程（如自建 Gitea），用 `locket pull/push/sync` 对齐；
  删除（redact）写入墓碑并随同步传播到其它克隆，`locket gc` 在本地物理抹除内容
- 提供视图、加密、签名校验、语义冲突检测及 MCP 接口；部署范围见对应文档
- MCP 支持本机 stdio 与鉴权的多用户 Streamable HTTP，接入方式见 [多用户 MCP 文档](docs/MCP_MULTIUSER.md)

## 安装

需要 Python 3.10+ 和 git。

```sh
py -3 -m pip install -e .        # 之后可以直接用 locket 命令
locket init                      # 默认仓库在 ~/.locket，可用 LOCKET_HOME 或 --repo 指定
```

## 常用命令

| 命令 | 作用 |
|---|---|
| `locket remember "内容" --title 标题 --type preference --about self` | 新增记忆（type：fact / preference / event / procedure / summary） |
| `locket update <id> --text "正确内容"` | 记错了：同一个 id 原地更正，旧版本留在历史中 |
| `locket supersede <id> "新内容" --effective 2026-11-01` | 情况变了：旧记忆写上截止日期，新建一条 |
| `locket forget <id>` | 软遗忘：从当前记忆中移除，历史中仍可找回 |
| `locket redact <id> --yes` | 永久删除并写入墓碑 |
| `locket recall "关键词"` / `locket list` / `locket show <id>` | 查找和查看 |
| `locket why <id>` | 这条记忆从哪来、改过几次 |
| `locket log` / `locket conflicts` / `locket resolve <id>` | 历史与冲突 |
| `locket save` | 提交你在仓库里手动改过的文件 |
| `locket remote <url>` | 设置同步远程（指向你的 Git 仓库，如 Gitea 私有仓） |
| `locket pull` / `locket push` / `locket sync` | 在多台设备间同步长期记忆（sync = 先拉后推） |
| `locket gc --yes` | 改写历史、物理清除已 redact 的记忆（会使下次 push 变强制推送） |
| `locket reindex` | 重建派生检索索引（故障或迁移后用） |
| `locket serve-api [--gitea]` | 启动 HTTP/JSON API；`--gitea` 开多租户（Gitea 账号 + 每人一个仓） |
| `locket mcp` | 启动 MCP 服务器，供 Agent 接入（需 `pip install 'locket-memory[mcp]'`） |

## 性能与服务层

- 读路径由派生 SQLite 索引（`.git/pmf/index.sqlite`，不进仓库、可 `locket reindex` 重建）加速，`recall`/`list`/`get` 不再每次解析全部文件。
- `locket serve-api` 把引擎暴露为 HTTP/JSON API，是 Web 前端和 MCP 的共同后端；`locket mcp` 走 MCP 协议供 Agent 直接用。
- 面向"很多人用的云上记忆存储"的多租户架构（Gitea 做账号/托管、每用户一个私有记忆仓）见 [ARCHITECTURE.md](ARCHITECTURE.md) 与 [GITEA-SETUP.md](GITEA-SETUP.md)。

`<id>` 用命令输出里方括号中的 8 位短 id 即可。不带 `--session` 时，写入会立即合并进长期记忆。

## 接入 Claude Code

```sh
locket install-claude-hooks --print     # 先看要写入的配置
locket install-claude-hooks             # 写入 ~/.claude/settings.json
```

三个钩子：

- **SessionStart**：开会话分支，把关于你和当前项目的记忆、待确认的冲突以及写入方法放进上下文
- **UserPromptSubmit**：按你这条消息检索相关记忆并附上
- **SessionEnd**：把会话分支合并进长期记忆

钩子出错时只写 stderr，不会打断 Claude Code。

## 开发

```sh
py -3 -m pytest
```
