# Issue #71 Dashboard 首次加载状态（2026-10-01）

Dashboard 首次请求知识表目录时，旧界面会暂时把尚未返回的数据展示成“0 张表”和空图表。用户容易把加载中的状态误认为知识库为空。现在请求开始时立即显示读取状态；首次加载、手动重试和定时刷新均持续显示加载状态，接口完成后再展示实际数量、图表或错误状态。

实现位于 `apps/desktop-web/src/Dashboard.tsx`，延迟响应与刷新过程的回归覆盖位于 `apps/desktop-web/tests/browser-acceptance-shell.test.mjs` 和 `apps/desktop-web/tests/shell-fixture.tsx`。本次没有 API、数据库 schema 或生产数据变更。

## 发布

- 源码与部署提交：`51a859c2e349c51e14a4a9d50bfec6de3b2db797`
- Dashboard 功能提交：`9565f03dc0b5a865ffa7b13ef51c1f7b8e5ff84c`（前置状态改进 `d526bd2`）
- Deploy Gateway release：`da941881-eba2-4d91-aa34-74baff428470`
- deployment：`c27a59c5-8b32-4a4c-a5d0-b03630b94bb3`，状态 `completed`，发布租约已释放
- Web、XeLibrary、Tool/LLM/Deploy Gateway、Embedding 与 DevAgent 五台 VM 的 `/opt/xeonolab/current` 均指向该提交。

## 验收证据

- 前端测试 39/39 通过；后端测试 279 项通过；TypeScript/Vite production build 通过；浏览器验收脚本 `node --check` 通过。
- 部署后使用公网 Chrome 登录态流程运行 44 项检查，44 项通过。Knowledge 目录 API 为 HTTP 200 且返回 1 张表；行列表 API 为 HTTP 200；System Dashboard 最终显示的表数和图表与同次 API 响应一致。
- 账号树首次启用说明、ACL 与权限设置、桌面组件、系统面板和 LaunchPad 搜索均可见。没有控制台错误、页面异常、意外 HTTP 错误或横向溢出。
- 桌面、XeLibrary、Tool、Deploy Gateway、LLM Gateway 五个 `/health/ready` 均为 HTTP 200。公网主页、JS、CSS 为 HTTP 200，两个静态资源 SHA-256 均与本机构建产物一致。
- 公网浏览器验收的临时 Keycloak 用户及本地临时凭据已删除。截图和机器可读报告仅保存在当前操作机的权限受限目录 `runtime/verification/plan-webui-browser-2026-10-01-postdeploy/`。

Issue #71 仍保持打开：本次只处理 Dashboard 知识表目录加载状态，不代表整个 Issue 或 `plan.md` 已完成。XeLibrary 与桌面仍有 Issue 描述中的其他功能、视觉和集成验收工作。
