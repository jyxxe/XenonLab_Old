# Issue #71：知识库错误关联编号增量（本地验收）

基线 `feature/platform-v1` 的 `904ae0c`；独立分支 `sol/issue-71-request-id`。协调器持有 #71 Work epoch 5，本轮仅在隔离 worktree 实现，不推送、部署或修改 Forgejo。

## 根因和行为

Issue §23 要求错误展示 request id。已有服务在 `error.request_id` 和 `X-Request-ID` 发送关联编号，但桌面的 JSON API 和原始响应 API 均在构造 `APIRequestError` 时丢弃它；知识库的 ErrorState 将错误代码误当请求编号。

现在两个请求边界共用错误构造函数，保留真实正文编号；正文编号不存在时使用响应头，不生成替代编号。错误代码独立显示。知识库目录、记录、搜索、查询、定理、Inspector、ACL/索引状态与创建/移动/schema/删除对话框的共享错误页使用相应元数据。编号和错误代码允许换行，窄屏可读。原有 status/code、权限、账号首次初始化、会话恢复和请求重放规则保留。

普通网络异常没有服务器编号，页面不会把 NETWORK_ERROR 或其他代码冒充请求编号。HTML 网关错误使用 HTTP 状态和响应头，不在页面渲染原始 HTML。

## 实际验证

- `uv run pytest`（共享虚拟环境）：279 passed，19 个既有依赖及临时目录清理警告。
- `npm test`：52 passed，0 skipped，包含新增 3 项错误传输单测和 1 项 Chrome 测试；既有 Library/Shell/Work/账号/会话/Playwright 回归均通过。
- `npm run build`：TypeScript 与 Vite production build 通过。
- `uv run python scripts/export_openapi.py`：完成；生成文件无 diff。本轮没有后端/API/schema 变更。
- `git diff --check`：通过。
- 真实匿名 LAN `GET /api/v1/tables` 返回 UNAUTHENTICATED，响应正文和 X-Request-ID 均提供编号；此调用为只读，不能替代登录态生产验收。
- 新 Chrome 测试运行完整 main.tsx → API 边界 → Library 页面，覆盖正文优先、非 JSON/header fallback、缺失编号及错误代码独立展示；确认 403 展示不调用 logout。
- 截图目录 `runtime/verification/issue-71-request-id/`：dark/light 1440×900、light 390×844。截图为真实 Chrome 渲染的合成身份/API，已查看深色桌面与浅色移动端；移动文档无横向溢出。

## 剩余范围

这是一项错误 UX 修复，不代表 #71 的完整 DoD。OpenWebUI 正式服务/注册/认证、原生 Forgejo Dashboard 受控数据源、System 观测 API、通用组件完整化和真实生产核心路径/人工验收仍需完成。其他旧页面以纯字符串保存错误（例如 Dashboard notes、部分附件/账号表单），尚未全面迁移为结构化诊断；本轮只保证共享 API 元数据和上述知识库 ErrorState。

无数据迁移。回滚此增量的前端提交即可恢复旧 UI；后端契约没有变化。提交是否发布及发布后真实账号验收由协调器决定；#71 应保持开放。
