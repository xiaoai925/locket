# Locket Web（P3 前端）

Vue 3 + TypeScript + Vite 单页应用，Locket 记忆管理界面。**同源**调用后端：`/auth/*` 登录、`/api/*` 记忆 API，均由 `web/server`（Go）反代到 Python 的 `locket serve-api`。

## 开发

```sh
npm install
npm run dev        # Vite dev server :5173，自动代理 /api 和 /auth → http://localhost:8080（Go 服务器）
```

先在另一端起好：Python API（`locket serve-api --gitea`，默认 :8723）与 Go 服务器（`web/server`，:8080，反代到 API）。

## 构建

```sh
npm run build      # 产物在 dist/，由 Go 服务器作为静态资源托管
npm run typecheck  # 可选：vue-tsc 类型检查
```

## 结构

- `src/api.ts` / `src/types.ts` — API 封装与类型（401 自动跳登录）
- `src/router.ts` / `src/auth.ts` — 路由与登录态
- `src/ns.ts` — 记忆命名空间分组（self/people/projects/world/procedures/episodes）
- `src/views/` — Login / Memories / Recall / MemoryDetail / Conflicts
- `src/components/` — MemoryCard / MetaBadges

## 页面

| 路由 | 页面 | 用到的接口 |
|---|---|---|
| `/login` | 登录（Gitea OAuth 或粘贴 PAT） | `/auth/login`、`/auth/token` |
| `/` | 记忆列表，按命名空间分组、按类型筛选 | `/api/memories` |
| `/recall` | 关键词检索，带相关度 | `/api/recall` |
| `/m/:id` | 记忆详情：正文 markdown + 来源链 + 认知演变时间线 + 遗忘 | `/api/memory/:id/why`、`/api/memory/:id/forget` |
| `/conflicts` | 待确认冲突，标记解决 | `/api/conflicts`、`/api/conflict/:id/resolve` |
