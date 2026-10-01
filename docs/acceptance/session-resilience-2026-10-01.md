# Issue #76：桌面会话恢复验收

本地合成浏览器验收通过；它没有连接生产环境、使用真实账号或部署代码。Issue 中报告的五分钟限流与重新登录现象尚未在生产复现。一次只读登录探针中，session exchange、token refresh 与 `/users/me` 均返回 200；这不足以判断原报告的成因。

## 修复范围

- 客户端缓存短期 access token，并发刷新合并为一次请求；只有接近过期才刷新。
- 429、5xx、非 JSON gateway 响应与网络失败保留工作区并进入可恢复状态。明确的授权撤销仍要求用户重新授权。
- GET 与已列出的查询/搜索 POST 在 Retry 或等待 `Retry-After` 后自动重放；原调用会收到重放结果。写请求不自动重放。
- `Retry-After` 的秒数与 HTTP 日期都会限制重试时间，冷却期间不会由手动或后台重试提前发送请求。
- 登出立即递增会话代次并拒绝待处理请求；迟到的 token refresh 或 API 响应不能恢复会话。
- 固定桌面 API 函数身份，并保护一次性 bootstrap，避免会话状态更新重置已打开的窗口。

## 验收

在 `apps/desktop-web` 执行：

| 命令 | 结果 |
|---|---|
| `npm test` | 47 passed，0 failed；包括 session policy、浏览器恢复、外壳与可用性测试 |
| `npm run build` | `tsc --noEmit` 与 Vite production build 通过 |
| `git diff --check` | 通过 |

CDP 浏览器夹具覆盖 token 缓存、429 与两秒 `Retry-After`、冷却期间请求排队、恢复后更新原始 Dashboard 请求结果、500 手动重放、HTML 502、网络失败、授权撤销，以及登出后迟到 refresh 不恢复桌面。无后端改动，因此本轮未运行后端 pytest 或重新生成 OpenAPI。

## 遗留项

生产问题的触发条件与身份服务当时返回的状态码、限流窗口及 `Retry-After` 仍无证据。发布后应由协调器安排真实浏览与限流恢复验收；本地夹具结果不能替代该验收。
