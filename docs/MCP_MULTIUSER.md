# MCP 与多用户接入

下文的 `memory.example.com` 是示例域名，不能直接访问。

原来的 PMF Markdown/Git 仓仍是记忆来源；SQLite 和向量索引只是本机派生缓存。
此次增加传输、鉴权与范围检查，不迁移正式记忆、不改聊天模型。

## 单用户本机（stdio）

```powershell
cd D:/my/SI_Ag
py -3 -m pip install -e '.[mcp]'
$env:LOCKET_AGENT = 'my-brain'
$env:LOCKET_SCOPE_JSON = '{"about":["person:qq-12345"],"max_sensitivity":"personal","can_write":false}'
py -3 -m locket --repo D:/memory/alice mcp
```

客户端通过 stdin/stdout 发送 MCP，不能在这个窗口再手工输入普通聊天。
`--repo` 优先于 `LOCKET_HOME`。范围由宿主环境绑定，不能通过工具参数切换。
未设置范围时，stdio 为兼容原 CLI 保留仓主权限；不应把这个进程直接共享给群聊。
只读 stdio 不公布写入工具；后端仍逐次检查权限。

## 多用户服务（Streamable HTTP）

每个记忆所有者是一个 tenant；记忆中提到的 QQ 联系人是 `about=person:qq-...`，二者不是同一概念。
一个租户可给自己的私聊、某个群和直播分别授权，使用不同令牌及精确 about 范围。
不同租户使用独立私有仓。跨端仍同步对应 Git 仓，而非复制另一个租户的数据。

同一租户增加第二台设备属于**多设备同步**，不是新建一个多用户租户：两台设备各有本地工作副本和派生索引，但配置相同主人授权的记忆频道仓库，各自使用设备本地凭据同步。实例来源 ID 和本地大脑状态必须独立；不要共享 SQLite、访问令牌或工作目录。若同一设备承载多个主人的数据，则才是多租户，必须为每位主人分别授权和解析仓库。

MCP 只定义模型如何调用有范围约束的记忆操作，不取代 Git/Gitea 的跨设备同步，也不要求把 MCP 服务暴露给公网。电脑和手机可以各自用本机适配器调用本地记忆；只有确实需要远程 Agent 直接调用记忆时，才部署鉴权的 HTTP MCP。应按设备和渠道签发不同的最小权限令牌，支持独立撤销。

提供两种部署模式，必须二选一：

- `--grants`：管理员配置令牌摘要、现有仓位置和范围。适合本机/自有网关。
- `--gitea-url`：Gitea 验证调用者账号，映射到 `<login>/memory.git` 私有仓；账号持本人令牌拥有自己的仓。需要先创建该私有仓。

```powershell
py -3 -m locket mcp --transport streamable-http --grants D:/private/mcp-grants.json
# 或
py -3 -m locket mcp --transport streamable-http --gitea-url https://git.example.com --data-dir D:/private/mcp-tenants
```

默认只监听 `127.0.0.1:8724/mcp`，每次请求必须带 `Authorization: Bearer <token>`。
无令牌/无效令牌返回 401。使用无状态 HTTP，不复用一个特权身份处理其他用户请求。
对外部署应由 HTTPS 反向代理转发本机端口，并指定 `--resource-url https://memory.example.com/mcp`。
不提供 OAuth 登录/发令牌页面，客户端需要管理员预配 Bearer 令牌或 Gitea 令牌。

授权文件格式（示意路径和摘要不可直接使用）：

```json
{"version":1,"grants":[{
  "sha256":"<高强度随机令牌的SHA256十六进制摘要>",
  "user":"alice",
  "repo":"D:/private/memory-alice",
  "agent":"qq-owner",
  "scope":{"about":["person:qq-12345"],"max_sensitivity":"personal","can_write":true},
  "expires_at":1900000000
}]}
```

用 Python `secrets.token_urlsafe(32)` 生成令牌，`hashlib.sha256(token.encode()).hexdigest()` 生成摘要。
明文令牌只交给对应客户端，授权文件和记忆仓目录由服务账号与管理员控制。
`scope` 必须明确提供；未知字段或敏感级别会拒绝授权。
撤销时设 `disabled:true` 或移除授权项，原子替换文件即可；下一请求重新验证，正在进行的操作不会被追溯撤销。
可选 `session` 为宿主固定会话标签；服务按授权/权限范围做散列隔离，模型不能自行选择其他会话。

## 工具与完成语义

12 个工具覆盖 recall/get/list/remember/update/supersede/forget/why/history/conflicts/resolve/end_session。
`memory_list` 默认每页 50，最大 200，使用 `next_offset` 翻页；recall 的 k 最大 50。
工具失败使用 MCP `isError=true`；调用方不能把失败结果当作已完成回执。
记忆写入的 `merged=true` 仅代表本机长期记忆已合并，**不代表云端推送或另一端在线**。
非仓主遵守仓库审核要求，不能通过即时写入绕过审批；冲突解决、设置、视图管理需仓主权限。
受限调用者看不到原始 Git 提交历史，防止提交标题包含其他范围的信息。

HTTP/MCP 服务共用线程及跨进程仓锁；临时 Gitea/授权仓句柄在请求后释放。
旧版 CLI、第三方直接写 Locket 的适配器没有自动获得服务锁：运行时仓不要同时挂给独立 MCP 写入进程。
大肥鱼接入采用既有适配器的只读工具，共用原锁和权限，不创建并行原始写入路径。

## DSH 接入

配置样例在 `integrations/dsh/locket.stdio.yaml` 和 `locket.http.yaml`。
按官方架构/插件文档的提交 `fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159` 编写。
宿主设置环境变量，再把样例作为当前 DSH profile 的配置 patch 加入。
模型看到工具名 `mcp__locket__memory_recall` 等；每个用户/Agent 使用独立授权上下文。
先验证 tools/list，再验证模型能检索合成测试记忆；不要用正式私聊记录做演示。

本仓测试覆盖 Python MCP SDK stdio/HTTP 的真实协商、工具调用、越权、撤销和跨会话持久化。
DSH 的配置按官方文档核对；未声称已在用户的 DSH 运行时或云端上线。
接入 DSH 时还需检查当前 profile 的文件/终端和日志插件，MCP 权限无法约束宿主另行提供的文件工具。
私有记忆不得默认交给远程会话日志插件上传。
