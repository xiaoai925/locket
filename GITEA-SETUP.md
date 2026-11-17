# 以 Gitea 多租户模式运行 Locket 云服务

让很多人共用一套云上记忆存储：**Gitea 管账号与托管，每个用户拥有自己的私有记忆仓**，
Locket 服务层（`service.py` + `webapi.py`）在其上提供记忆语义。本文讲怎么把 API 跑成多租户。
架构背景见 [ARCHITECTURE.md](ARCHITECTURE.md) §2/§3；租户实现是 `src/locket/tenancy.py`。

## 1. 租户模型

- 一个 Gitea 用户 ↔ 一个私有仓 `<user>/memory`（仓名默认 `memory`，可配）。
- 隔离直接用 Gitea 的 repo 级权限：A 读不到 B 的仓。
- 服务器为每个用户维护一份**工作副本**（在数据目录 `<DATA_DIR>/<user>`）：首次请求 `clone`，
  之后请求 `pull` 保持新鲜，写入后由引擎 `push` 回 Gitea。

## 2. 认证（初期：Gitea 个人令牌 PAT）

API 用 **Bearer token** 认证，token 就是调用者的 Gitea 令牌：

```
Authorization: Bearer <你的 Gitea 令牌>
```

`GiteaAuthenticator` 拿到 token 后调 `GET {GITEA_URL}/api/v1/user` 验明身份（先按 PAT 的
`Authorization: token <t>`，失败再按 OAuth2 的 `Bearer <t>`），取 `login` 作为租户。
令牌只在向 Gitea 发起 git/API 请求时临时作为凭据使用，**不会写进 `.git/config`、也不会记日志**。

用户自助获取令牌：Gitea → 设置 → 应用 → 生成令牌，勾选 `read:user` 与 `write:repository`。

## 3. 启动

环境变量：

| 变量 | 含义 | 示例 |
|---|---|---|
| `GITEA_URL` | 你的自托管 Gitea 地址 | `http://localhost:3000` |
| `LOCKET_DATA_DIR` | 服务端每用户工作副本的根目录 | `/var/lib/locket` |
| `LOCKET_API_HOST` / `LOCKET_API_PORT` | API 监听地址/端口 | `0.0.0.0` / `8723` |

```sh
GITEA_URL=http://localhost:3000 LOCKET_DATA_DIR=/var/lib/locket \
  locket serve-api --gitea
```

`--gitea` 开启多租户：`authenticate = GiteaAuthenticator(GITEA_URL)`，
`resolve = GiteaResolver(GITEA_URL, LOCKET_DATA_DIR)`。不加 `--gitea` 则是单仓本地模式（开发用）。

调用示例：

```sh
curl -s http://localhost:8723/healthz
curl -s -H "Authorization: Bearer <PAT>" http://localhost:8723/memories
curl -s -H "Authorization: Bearer <PAT>" -H "Content-Type: application/json" \
  -d '{"text":"用户偏好中文交流","type":"preference","title":"交流语言"}' \
  http://localhost:8723/remember
```

## 4. 用户仓的开通

- 若用户已有 `<user>/memory` 私有仓：首次请求自动 `clone`。
- 若该仓是**空仓**：服务会 `locket init` 播种 PMF 结构并 `push`。
- 若该仓**尚不存在**：当前版本会报错提示先创建。**TODO（provisioning）**：首登时用 Gitea API
  （`POST /api/v1/user/repos`，`private=true`）自动建仓。

## 5. 面向 Web UI 的 OAuth2（TODO，P3）

初期直接用 PAT 作 bearer 最省事。等做 Go+Vue 前端（P3）时：
1. 在 Gitea → 设置 → 应用 → 创建 OAuth2 应用，拿 `client_id`/`client_secret`、配好回调地址。
2. 前端走浏览器**授权码流**拿到用户的 OAuth2 access token。
3. 前端把该 token 当作 `Authorization: Bearer` 转发给本 API —— 认证层无需改动，
   因为 `GiteaAuthenticator` 的默认校验已兼容 OAuth2 的 `Bearer` 方案。

## 6. 注意事项

- **并发**：同一用户的工作副本在高并发写入下会竞争 `origin` 临时改址与 git 锁；生产环境应按用户串行化写操作（服务层后续补）。
- **令牌安全**：令牌仅作 git 凭据临时注入 URL，不落盘；请让 Gitea 走 HTTPS 以免 token 明文过网。
- **可带走**：每个用户的记忆始终是一个标准 PMF git 仓，用户可随时 `git clone` 带走——云端只是托管方。
