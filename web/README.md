# Locket Web（P3 前端）

记忆管理系统的 Web 界面。三层：

```
浏览器  ──►  Go 服务器 (web/server, :8080)  ──►  Python 记忆 API (locket serve-api, :8723)  ──►  Gitea
  │            · 服务 Vue SPA                       · HTTP/JSON（见 webapi.py）              · 账号/每用户仓
  └─ Vue SPA   · Gitea OAuth 登录 + 会话 cookie       · --gitea 多租户：token → 该用户的仓
   (web/app)   · /api/* 反代并注入 Bearer token
```

- **Go 服务器**只用标准库：服务前端静态文件、处理 Gitea 登录、把 `/api/*` 带着用户 token 反代给 Python API。
- **Vue SPA**同源调用 `/auth/*`（登录）和 `/api/*`（记忆）。
- **Python API** 以 `--gitea` 多租户模式跑：拿到的 Bearer token 决定操作哪个用户的仓（见 `../GITEA-SETUP.md`）。

## 本地开发（三个终端）

```sh
# 1) Python 记忆 API（多租户，指向你的 Gitea）
cd D:\my\SI_Ag
py -3 -m pip install -e .
set GITEA_URL=http://localhost:3000
set LOCKET_DATA_DIR=%CD%\.tenants
py -3 -m locket serve-api --gitea --port 8723

# 2) Go 服务器（读 web/.env.example → 自己的 .env 或环境变量；DEV=1 可用 PAT 登录）
cd D:\my\SI_Ag\web\server
go run .

# 3) Vue 前端（dev server，代理 /api 和 /auth 到 :8080）
cd D:\my\SI_Ag\web\app
npm install
npm run dev        # 打开 http://localhost:5173
```

开发登录：在登录页粘贴一个 Gitea 个人令牌（PAT，Gitea → 设置 → 应用 → 生成令牌，勾 `read/write repository`）。免注册 OAuth 应用即可自测。

## 生产构建

```sh
cd web/app && npm run build          # 产出 web/app/dist
cd ../server && go build -o locket-web .
# 启动：Go 服务器把 dist 当静态站点服务，并反代 /api；浏览器只访问 :8080
GITEA_URL=... OAUTH_CLIENT_ID=... OAUTH_CLIENT_SECRET=... SESSION_SECRET=... LOCKET_API_URL=... ./locket-web
```

OAuth 登录（生产）：在 Gitea 注册一个 OAuth2 应用，重定向 URI 填 `http(s)://<你的域名>/auth/callback`，把 client id/secret 配进环境变量。登录页的"用 Gitea 登录"按钮会走授权码流。

## 说明

- 这是为"记忆"定制的轻界面：按命名空间浏览、recall 搜索、记忆详情（正文 + 来源链 + 版本时间线＝认知演变）、冲突队列。
- 配置项见 `web/.env.example`。
