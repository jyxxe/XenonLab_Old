# Issue #76：桌面会话恢复验收

本地合成浏览器验收通过；它没有连接生产环境、使用真实账号或部署代码。Issue 中报告的五分钟限流与重新登录现象尚未在生产复现。一次只读登录探针中，session exchange、token refresh 与 `/users/me` 均返回 200；这不足以判断原报告的成因。

## 修复范围

- 客户端缓存短期 access token，并发刷新合并为一次请求；只有接近过期才刷新。
- 429、5xx、非 JSON gateway 响应与网络失败保留工作区并进入可恢复状态。明确的授权撤销仍要求用户重新授权。
- GET（包括 `apiRaw` 附件下载）与已列出的查询/搜索 POST 在 Retry 或等待 `Retry-After` 后自动重放；原调用会收到重放结果。上传与其他写请求不自动重放。
- `Retry-After` 的秒数与 HTTP 日期都会限制重试时间，冷却期间不会由手动或后台重试提前发送请求。
- 登出立即递增会话代次并拒绝待处理请求；迟到的 token refresh 或 API 响应不能恢复会话。
- 固定桌面 API 函数身份，并保护一次性 bootstrap，避免会话状态更新重置已打开的窗口。

## 验收

在 `apps/desktop-web` 执行：

| 命令 | 结果 |
|---|---|
| `npm test` | 48 passed，0 failed；包括 session policy、浏览器恢复、外壳与可用性测试 |
| `npm run build` | `tsc --noEmit` 与 Vite production build 通过 |
| `git diff --check` | 通过 |

CDP 浏览器夹具覆盖 token 缓存、近过期并发请求 single-flight、429 与两秒 `Retry-After`、冷却期间请求排队、恢复后更新原始 Dashboard 请求结果、500 手动重放、HTML 502、网络失败、附件原始 GET 的重放、上传不重放、手动 bearer token 的冷却重放、refresh 授权撤销和无会话令牌时清空待处理请求、首个重放 401 后停止队列，以及登出后迟到 token/raw 响应不恢复桌面。策略单测确认一次性 FormData/stream body 不会重放。无后端改动，因此本轮未运行后端 pytest 或重新生成 OpenAPI。

## 遗留项

生产问题的触发条件与身份服务当时返回的状态码、限流窗口及 `Retry-After` 仍无证据。发布后应由协调器安排真实浏览与限流恢复验收；本地夹具结果不能替代该验收。

## 生产部署验收

2026-10-01 已通过 Deploy Gateway 发布 commit `24ee54e965455827779b5341d62900d880f47079`；release
`2514a42d-fcc0-4736-9366-581712785557` 和 deployment `c7c5014f-1976-4ea5-aeed-0c2ee9f0feac`
均为 `completed`，租约已释放。发布前桌面数据库使用 SQLite backup API 快照到
`runtime/backups/2026-10-01-issue76/predeploy-desktop.sqlite`；`PRAGMA integrity_check` 为 `ok`。

部署后，LAN 五个服务入口 readiness 全为 200；VM 101、104、106、107、111 的
`/opt/xeonolab/current` 均指向本次 commit。LAN 与公网桌面首页及 JS/CSS 均为 200，静态资源 SHA-256
均与本机构建一致。认证配置返回 200；带公网 Origin 且无 cookie 的 session token 请求返回预期的
`401 NO_SESSION`。没有真实用户会话下的五分钟复现或生产限流注入；Issue #76 保持打开，等待真实浏览场景验收。
