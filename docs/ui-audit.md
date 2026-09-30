# Desktop UI 审计（Issue #71, Phase A）

日期：2026-10-01  
范围：`apps/desktop-web`（React 19 + TypeScript + Vite，无组件库、无路由库）  
方法：阅读全部前端源码与既有测试、运行 `npm test`、`npm run build`，并用合成 API 的浏览器夹具（`tests/shell-fixture.html`）在 1920×1080 / 1440×900 / 1366×768 / 1024×768 / 390×844 下截图检查。本文档只描述事实，不声称任何未运行的验证。

## Existing Components

| 区域 | 位置 | 现状 |
|---|---|---|
| 桌面根组件 | `src/main.tsx` | 认证状态机与桌面 shell 混在一个组件；窗口、Dock、LaunchPad、iframe 消息、主题全部内联 |
| 窗口管理 | `src/main.tsx` | 绝对定位 + CSS `resize:both`；拖动、最小化、最大化；几何状态仅在手动“保存布局”时写入后端 |
| 窗口外壳 | `src/main.tsx` `.bar` | 每个窗口共用一段内联 JSX；红黄绿圆点按钮；无统一 actions 扩展位 |
| Dock | `src/main.tsx` `<nav className="dock">` | 固定 + 运行中合并显示；无 pin/unpin、排序、badge、自动隐藏、右键菜单 |
| LaunchPad | `src/main.tsx` | 覆盖层 + 搜索框；无键盘导航、无最近使用、无权限外的 unavailable 说明 |
| 内置应用 | `Dashboard`、`Knowledge`、`Work`、`Settings`（+`Accounts`/`Permissions`/`ACLManager`/`AppRegistry`/`Attachments`/`SchemaEditor`） | 逻辑完整、可复用；视觉全部依赖 `style.css` |
| 外部应用 | `iframe` + sandbox + postMessage | 有消息白名单；加载失败/超时只留空白 |
| 主题 | `data-theme`/`data-accent`/`data-wallpaper` + `Widgets` | 已被偏好 API 持久化；颜色硬编码在 `style.css` |
| 会话 | `src/auth.ts`、`session-state.js` | 持久会话、失败保留工作区、显式登出；有独立单元测试 |
| 测试 | `tests/*.test.mjs` | Node test；Work 分页与账号树浏览器夹具；`scripts/browser_acceptance.mjs` 是生产 QA harness |

## Reusable Components

- `Dashboard` 的图表与卡片（`MacCharts.tsx`）在暗/亮主题下都可用，只依赖 CSS 变量。
- `Knowledge`/`Attachments`/`ACLManager`/`SchemaEditor`/`Accounts`/`AppRegistry` 的表单与请求逻辑可保留，只需换壳与状态组件。
- `Work.tsx` 的分页、游标去重、陈旧响应丢弃逻辑经过浏览器测试，应原样保留。
- `auth.ts` + `session-state.js` 的会话状态机必须保留，UI 只消费其状态。
- `AppRegistry` API（`GET/POST/PATCH/DELETE /api/v1/apps`）与 `users/me/layout`、`users/me/preferences` 已满足大部分桌面设置需求。

## Components To Replace

- `main.tsx` 中内联的 shell：拆为 `shell/DesktopShell.tsx`、`MenuBar`、`Dock`、`LaunchPad`、`WindowFrame`、`IFrameApp`、`CommandPalette`（本次已完成）。
- 全局 CSS：原 `style.css` 是暗色硬编码 + 少量 `[data-theme=light]` 覆盖，改为 `design-system/tokens.css` 单一 token 来源 + `primitives.css` + `shell.css`（本次已完成）。
- 裸 `input/button/select`：改为 `design-system/primitives.tsx` 的 `Button/IconButton/Input/Textarea/Select/Checkbox/Switch/Tabs/Badge/StatusDot/Skeleton/Spinner/Progress/EmptyState/ErrorState/Modal/Panel/Sidebar/SplitView/Menu/Popover/ContextMenu/Toast`（本次已完成）。
- 窗口最大化：原实现硬编码 `innerWidth-40 / innerHeight-140`，改为可测试的 `shell/window-manager.js`（本次已完成）。
- 窗口布局持久化：原为手动保存，改为 debounce 自动保存 + 启动恢复（本次已完成）。

## Missing States

- iframe：加载、超时、认证失败、重载、外部打开（本次已加超时/失败/重载/外开；认证失败仍依赖服务端跳转，未单独建模）。
- 应用列表：无 `unavailable/未配置` 展示（外部 iframe 应用由管理员注册，未配置就不显示）。
- Dashboard：单卡片错误只显示一行文字，缺少统一 `ErrorState`/重试（部分保留）。
- Knowledge：加载/空/错误有文字提示，未统一为 Skeleton/EmptyState/ErrorState（未完成）。
- 命令面板异步 provider 的 loading 态（当前 provider 均为同步，未出现）。

## Known UX Problems

1. 窗口不能移出屏幕外后找回（已由 `clampRect` 修复），但最大化仍不平滑（无过渡）。
2. 双击标题栏、Cmd/Ctrl+W/M/`、Cmd/Ctrl+K、Ctrl+Space 原缺失（本次已加；浏览器保留的快捷键在帮助菜单说明并可用命令面板替代）。
3. Dock 无固定/排序/角标/自动隐藏（本次已加）。
4. Settings 没有分类侧栏，外观/桌面/Dock 混在一个表单（本次已重组为分类，并为 Dock 大小/自动隐藏、窗口恢复、减少动态效果新增服务端偏好字段，向后兼容）。
5. Knowledge 仍是“管理后台”式两栏，尚未达到 Issue §13 的 Sidebar / Content / Inspector 结构与表格能力（未完成）。
6. OpenWebUI / Development / Issues 原来没有桌面入口（本次新增 `AI 对话`、`开发`、`任务` 内置应用；Development/Issues 为原生只读视图 + Forgejo 外链，Forgejo API 未集成）。
7. 手机视口下 Menu Bar 会换行（本次已简化：隐藏中间菜单与日期，保留品牌/时钟/用户）。
8. `Work` 与新的 `Issues` 应用功能重叠：`Work` 保留领取/续约/释放操作，`Issues` 是只读状态视图。

## API Dependencies

| UI 功能 | 依赖 | 状态 |
|---|---|---|
| 登录/会话 | OIDC PKCE + `browser_sessions` cookie | 已存在，未改动 |
| 应用注册 | `GET/POST/PATCH/DELETE /api/v1/apps` | 已存在 |
| 窗口/Dock 布局 | `GET/PUT /api/v1/users/me/layout` | 已存在，前端改为 debounce 自动保存 |
| 偏好 | `GET/PATCH /api/v1/users/me/preferences` | 本次向后兼容新增 `dock_size`、`dock_autohide`、`restore_windows`、`animations`、`reduced_motion` |
| 任务队列 | `GET /api/v1/work`，`POST /work/{id}/claim\|renew\|release` | 已存在 |
| 知识库 | `GET /api/v1/tables` 等 XeLibrary API | 已存在，UI 未重构 |
| 服务状态 | `/health/live`、`/health/ready`、`GET /api/v1/jobs` | 已存在 |
| Forgejo Dashboard | 无平台侧 Issue/PR 列表 API | 未实现；Development/Issues 只链接到 Forgejo |

## Migration Order

1. ~~Design System（tokens + primitives）~~（本次完成）
2. ~~Desktop Shell（MenuBar / Dock / Window Chrome / LaunchPad / Command Palette）~~（本次完成）
3. ~~Settings 分类与持久化扩展~~（本次完成）
4. ~~窗口状态自动保存/恢复、快捷键、iframe 失败态~~（本次完成）
5. XeLibrary 布局（Sidebar / Content / Inspector）、表格 sticky header/排序/筛选/多选/上下文菜单、Search Fast/Quality/Structured、Query Console（未完成）
6. System App 分页（Services / Storage / Embedding / HF Cache / Agents / Deployments / Network / Audit；无真实数据的页签显示 Unavailable）（未完成）
7. Development 原生 Dashboard 数据源（需要独立 Issue 新增只读 Forgejo 代理 API）（未完成）
8. 响应式与无障碍打磨（移动端 App Switcher、对比度/焦点审计、视觉回归）（部分完成）

## 本次未改动

- `xeonolab/*` 除 `desktop.py` 偏好字段外的任何后端代码；未连接生产；未部署。
- `Knowledge.tsx`、`Work.tsx`、`Dashboard.tsx`、`Accounts.tsx` 等应用的业务逻辑。
- 生产 `scripts/browser_acceptance.mjs` 依赖的 DOM 契约（`nav.dock`、`.window`、`.bar .close`、`.launchpad`、`.knowledge`、`.settings-panel`、`.settings-tabs`、`[data-testid=session-*]`）全部保留。
