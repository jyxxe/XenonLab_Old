# 本地浏览器验收 — Issue #71 UI/UX 重构增量（2026-10-01）

本记录前半部分记录本地实现与合成数据验收；末尾的“生产发布后验收”补充了实际部署结果和仍未完成的检查。

## 范围

- 分支：`opencode/issue-71-desktop-ui`（worktree `runtime/agent-worktrees/issue-71-desktop-ui`）
- 交付：
  - `apps/desktop-web/src/design-system/`（tokens + primitives + menu-logic）
  - `apps/desktop-web/src/shell/`（DesktopShell、MenuBar、Dock、LaunchPad、CommandPalette、WindowFrame、IFrameApp、window-manager、apps、Development、Issues）
  - `xeonolab/desktop.py` 偏好字段向后兼容扩展（`dock_size`、`dock_autohide`、`restore_windows`、`animations`、`reduced_motion`）
  - 测试：`tests/window-manager.test.mjs`、`tests/menu-logic.test.mjs`、`tests/browser-acceptance-shell.test.mjs`（合成 API 夹具 `tests/shell-fixture.html`）

## 自动化结果（实际运行）

| 命令 | 结果 |
|---|---|
| `cd apps/desktop-web && npm run build` | `tsc --noEmit` 与 Vite production build 通过 |
| `cd apps/desktop-web && npm test` | 25 项通过（含 Shell、Work 分页浏览器夹具） |
| `uv run pytest -q` | 235 项通过 |
| `uv run ruff check xeonolab/desktop.py tests/test_desktop.py` | 通过 |
| `uv run python scripts/export_openapi.py --check` | 通过 |
| `cd apps/desktop-web && npm run build` | `tsc --noEmit` 与 Vite production build 通过 |

浏览器验收（headless Chrome + CDP，`tests/browser-acceptance-shell.test.mjs`，无未捕获异常断言）：

- 启动/加载：Menu Bar、Dock、内置应用列表按 `desktop:read`/`dev:read`/`xelibrary:read` 过滤。
- LaunchPad：搜索“知识”、方向键选择、Enter 启动知识库窗口。
- 窗口：标题栏拖动、右下角 resize、最小化（iframe 不销毁，Dock 出现恢复入口）、恢复、最大化（不覆盖 Menu Bar、为 Dock 留出空间）、还原、双击标题栏最大化、关闭。
- 命令面板：Ctrl+K 打开、过滤“Harness”、键盘选择、Enter 启动任务应用；任务列表分页（进行中 4 行 / 全部 5 行）。
- 设置：减少动态效果、Dock 自动隐藏、界面动画开关立即生效并写入 `PATCH /users/me/preferences`；布局 debounce 自动保存 `PUT /users/me/layout`。
- 刷新恢复：`Page.reload` 后设置窗口几何与减少动态效果偏好恢复。
- iframe 失败态：对 600ms 无响应的 origin 显示“无法加载 + 重新加载 + 在浏览器中打开”。
- 响应式：1920×1080 / 1440×900 / 1366×768 / 1024×768 / 390×844 均无横向溢出，Menu Bar 与 Dock 可见；移动端保留品牌、命令面板、时钟与用户菜单。

## 截图（本机证据）

由 `UI_SCREENSHOT_DIR=<dir> node --test tests/browser-acceptance-shell.test.mjs` 生成，已复制到 `runtime/verification/issue-71-ui/`：

- `desktop-dark-1920x1080.png`、`desktop-dark-1440x900.png`、`desktop-dark-1366x768.png`、`desktop-dark-1024x768.png`、`desktop-dark-390x844-mobile.png`
- `desktop-light-1440x900.png`
- `desktop-launchpad-dark-1440x900.png`、`desktop-command-palette-dark-1440x900.png`

截图使用合成数据：窗口中的“AI 对话”是对不会响应的本地端口的超时演示，任务数据为 fixture 生成的合成 Issue。

## 本地提交时未验证 / 未完成

- 未使用真实后端与真实账号登录，未在生产环境运行；`scripts/browser_acceptance.mjs` 生产 QA 需在部署后由协调器执行。
- OpenWebUI（chat.xenon.lan）真实连通性未验证；Forgejo 内嵌未实现，Development/Issues 只提供外链。
- XeLibrary 应用仍为旧两栏布局，Issue §13–§16（Sidebar/Content/Inspector、表格排序筛选多选、Search Fast/Quality/Structured、Query Console）未完成。
- System App 分页（Storage/Network/HF Cache/Agents/Audit）与原生 Forgejo Dashboard 数据源未实现（后者需要独立的后端只读代理 Issue）。
- 注意：本机 inotify 实例接近上限时 Vite watcher 会 EMFILE；既有 Work 浏览器夹具已改为 `watch:null`，新夹具同样禁用 watcher。


## GPT-6.1 Sol 接管增量（Issue #71 epoch 2）

基线 `cc2f546` 加 OpenCode `eef287d`。在独立 worktree 修复 Settings 的布局保存与应用注册表刷新回调；窗口恢复开关不再禁用 Dock 保存；新增 `dock_initialized` 保留完全取消固定的 Dock；布局与偏好写入按顺序发送、失败可见；最大化窗口保存原尺寸并在重新载入或视口变化后正确还原；Menu Bar 选择具体窗口；移动端一次显示一个活跃应用；LaunchPad 与命令面板加入焦点陷阱；窗口控制按钮不再从无障碍树隐藏；陈旧 Issues 分页响应被丢弃。

Development 使用可达的 Forgejo 外链，不再启动不存在的内置 Forgejo id。AI 对话优先使用应用注册表或明确的配置；OpenWebUI 生产依赖未部署时展示“尚未配置”，不再尝试不存在的 `chat.xenon.lan`。iframe 的页面 load 不被宣称为认证/业务连接成功；只有经过 origin 与 source 校验的 `app.ready` 确认连接，并持续提供重载/外开入口。

实际验证：235 后端测试通过（19 个已有依赖/临时目录清理警告）；前端 25 项测试通过；TypeScript/Vite production build、Scoped Ruff、OpenAPI 导出通过。Chrome 合成 API 核心路径新增显式保存、保存失败/恢复、注册应用后保持桌面、禁用窗口恢复时仍保存布局、空 Dock、System unavailable/permission 状态。截图在 `runtime/verification/issue-71-takeover/`，已查看明暗 1440x900 与 390x844；也生成 1920x1080、1366x768、1024x768、LaunchPad 与命令面板截图。这些是真实 Chrome 渲染的合成夹具，不能替代部署后的真实账号与 API 验收。

仍待：#72 Library 交付整合与独立回归；实际部署后的浏览器核心路径；OpenWebUI 实际服务上线；原生 Forgejo dashboard API。未关闭 #71，未推送或部署。


## System 应用增量

新增概览、服务、存储、Embedding、HF Cache、Agents、部署、网络、审计分类。概览复用既有 Dashboard；Embedding 使用 `/jobs`，Agents 使用 `/work`，审计使用 `/audit`，分页按游标读取并丢弃跨分类/刷新后的陈旧响应。服务健康只确认桌面 `/health/ready`，不把桌面健康推断为全部服务健康；磁盘、HF Cache、部署历史等无受控桌面 API 的分类明确显示未接入。网络显示浏览器连接提示及当前入口，不伪造 Tunnel 状态。审计兼容 RFC3339 时间。所有视图只读，保留现有 scope 检查，未添加生产依赖。

## 生产发布后验收（2026-10-01）

- 发布 commit：`5a33d9342f9d65b737d16b64943ffb3cc58d6434`
- Deploy Gateway release：`b42c8abe-81be-4024-9fe1-9fafdbc0f186`
- Deployment：`578b46c7-4f05-4eae-8b56-a1490e07e502`，状态 `completed`，发布租约已释放。
- 激活前对 Web 101 的 `desktop.sqlite` 使用 SQLite backup API 创建快照：`runtime/backups/2026-10-01-issue71/predeploy-desktop.sqlite`；`PRAGMA integrity_check` 返回 `ok`。
- 公网桌面、JS/CSS、认证配置和 OIDC discovery 返回 200；JS/CSS SHA-256 与发布构建一致。匿名 `/api/v1/users/me` 返回预期的 401。
- 桌面、XeLibrary、Tool Gateway、Deploy Gateway、LLM Gateway、Embedding、DevAgent 七个 readiness 检查均返回 200。
- 直接 IP 入口首页和认证配置返回 200；通过 `https://192.168.0.101:9443` 完成真实 PKCE、服务端 session exchange、cookie 恢复、授权 API 读取与 logout 撤销。
- 公网配置与 discovery 返回正确 issuer，但本次自动化公网 PKCE 检查未完成：本机 HTTPX 代理在 Keycloak 登录表单 POST 时超时，直连公网的 TLS 握手也超时。此结果不能证明公网登录失败，也不能作为公网登录通过证据；需在真实浏览器网络路径复测。
- Issue #71 保持打开。XeLibrary 三栏知识管理、表格/搜索/Query Console、原生 Forgejo Dashboard 和完整公网登录后的浏览器验收尚未完成；本次上线是桌面 Shell 与系统页面的增量发布。

## System 概览错误状态增量（2026-10-01）

- 当索引队列、Harness 工作队列、知识库概览或桌面 readiness 请求失败时，System 概览使用统一错误提示与键盘可操作的“重试”按钮；重试重新读取现有数据源。
- 合成浏览器验收将 Harness 工作队列请求切换为失败，检查 alert 和重试按钮，再恢复 API 并确认错误消失。
- 生产 commit `44e5d1a2b2875b122f70eb27daa9afbe6d83361f`；Deploy Gateway release `85b605ea-46ba-4a26-bcd5-26d1caa79d75`；deployment `33a0af38-94e2-4a60-8d1a-3b6248c281e4` completed，租约已释放。
- 235 项后端测试、38 项前端测试、TypeScript/Vite build、OpenAPI 导出与 diff check 通过。LAN 五个入口 readiness 为 200，桌面 JS/CSS 哈希与构建一致，未认证 Work API 返回 401。
- 此验收没有使用真实登录态浏览器，不能替代完整桌面视觉和跨服务用户流程；Issue #71 仍开放。

## Sol 完成后续前端阶段（基线 ce2ba01）

已从公开 Forgejo API 读取 #71 完整描述和全部两条评论；独立分支 `sol/issue-71-takeover` 从干净的 `5a33d93` 快进到 `ce2ba01` 后实施。本轮只改桌面前端与测试，不更改认证语义、后端契约或生产配置；#72 Library、#73 DevAgent 保留已有实现。

本轮实现：

- 修复 Menu/ContextMenu 的分隔符焦点索引、真实 pointerdown 提前关闭导致点击失效、点击已打开的菜单反而重开；Escape 返回触发控件，菜单支持方向键开启，Tabs 支持方向键/Home/End。
- Dock 支持 Shift+F10/ContextMenu 键打开管理菜单；修复向右移动无效。Settings 新增完整固定应用清单、pin/unpin、前后排序，复用既有布局保存。未完整确认的队列计数显示 `?`，避免把已加载第一页当总量。
- 增加手机 Menu Bar 的窗口切换器，可以恢复最小化窗口；标题栏可聚焦，桌面方向键移动、Shift+方向键调整大小。小屏仍按单个活跃窗口显示；平板使用既有近全屏策略。
- App Registry 的原生应用条目声明应用菜单，Menu Bar 不再按 app id switch；知识库菜单连接 Search / Query Console / Theorems / Refresh。命令面板的 XeLibrary provider 将搜索词交给知识库，用户确认后才执行检索，不在输入命令时调用模型/API。
- iframe 仅接受已注册 origin 和精确 contentWindow 的 `app.ready` / `app.status`。可信 `app.status` 可报告 `auth-error`、`network-error`，显示重试/外开；无法观察跨域 HTTP 状态时只显示“页面已载入；认证和业务连接尚未确认”。重载可按窗口定向。最小化保留 iframe 与 sandbox。
- 修复 `main.tsx` 的 API 回调身份随会话续期变化导致 DesktopShell 重新 connect、清空/恢复窗口的问题。稳定的 API 边界仍使用最新 session/token；临时失败和撤销会话只显示已有提示，保留窗口和本地编辑，只有显式退出才调用 logout。
- Focus trap 不因 onClose 回调重建而重置输入焦点；LaunchPad 补充 combobox/active-descendant 语义；减少动态效果/关闭动画实际停止无限动画，OS reduced-motion 也生效。视觉检查修正开发任务徽章被旧 CSS 拉伸、设置面板内容内距。

### 原始 §34 的 30 项 checkbox 映射

“本地通过”指当前实现与合成 API 的真实 Chrome 浏览器证据；不等于本轮已完成生产验收。原始 checkbox 和 Forgejo 状态未被本轮修改。

| # | 原始项目 | 当前证据与边界 |
|---|---|---|
| 1 | MenuBar 可用 | 本地通过：真实 pointer 点击、分隔符键盘导航、关闭/焦点恢复、Registry app menu；Shell CDP |
| 2 | Dock 可用 | 本地通过：固定/运行/最小化、双向排序、Shift+F10、Settings pin/unpin、自动隐藏/持久化 |
| 3 | LaunchPad 可用 | 本地通过：搜索、权限过滤、Enter/ESC、最近应用；CDP + Playwright |
| 4 | Window focus 正常 | 本地通过：鼠标、Menu Bar、命令面板、移动 switcher；活跃窗口跟随 |
| 5 | Move / resize 正常 | 本地通过：拖动/resize/min size；新增标题栏键盘 move/resize |
| 6 | Minimize / restore 正常 | 本地通过：Dock 与手机 switcher 恢复；iframe DOM 保留 |
| 7 | Maximize / restore 正常 | 本地通过：避让 Menu Bar/Dock、双击、恢复几何；window-manager 单测 |
| 8 | Window state 恢复正常 | 本地通过：服务器布局刷新恢复、关闭恢复仍保存 Dock、空 Dock；会话重绘不重置窗口 |
| 9 | 多窗口不会产生明显 z-index 错误 | 本地通过：数组顺序 focus 单测、多个应用切换与窗口截图；完整生产长时间使用待测 |
| 10 | 核心组件已经统一 | 已有 tokens/primitives 被 Shell、Settings、Library 使用；本轮修复 Menu/Modal/Tabs 行为；通用组件全集见下方限制 |
| 11 | 不再存在明显的多个相互冲突 UI 风格 | 明/暗 Shell、Settings、Development、Library 截图检查；修复 Badge/CSS 冲突；非核心旧管理表单未做全状态逐页审计 |
| 12 | Light / Dark 均可用 | 本地通过：设置切换和刷新恢复、明/暗截图；Shell CDP + Playwright |
| 13 | Loading / Empty / Error 状态统一 | 当前共享 Skeleton/EmptyState/ErrorState，System 错误重试与 iframe 明确状态已测；请求编号限制见下方 |
| 14 | Library navigation 达到可日常使用水平 | #72 的路径树、Favorites/Recent、Theorems；Library 浏览器回归；本轮增加 Menu/Palette 入口 |
| 15 | Table View 可用 | #72：sticky header、列宽、排序、过滤、多选、游标、虚拟窗口、编辑冲突；Library 浏览器回归 |
| 16 | Search 可用 | #72 Fast/Keyword/Structured 与 partial/approximate 提示；本轮命令 provider；Quality Qwen 未部署，明确 unavailable |
| 17 | Query UI 可用 | #72 editor/run/validate/results/duration/count/copy/分页；本轮 Registry 菜单入口；Library 浏览器回归 |
| 18 | Inspector 可用 | #72 metadata/schema/attachments/history/ACL/index，权限/冲突提示；Library 浏览器回归 |
| 19 | 大量数据不会导致明显 UI 崩坏 | Library virtualWindow 单测和浏览器有限 DOM 行数检查；尚未模拟真实超大目录/全天多 iframe 压力 |
| 20 | OpenWebUI 能可靠启动 | **未满足**：生产服务/注册入口未提供；显示“AI 对话尚未配置”；Playwright 验证该真实边界 |
| 21 | iframe 加载失败有明确状态 | 本地通过：超时、可信 auth/network error、reload/external、错误 origin/source 忽略、minimize 保留 sandbox |
| 22 | Development 能可靠访问 | 原生小版 Dashboard 与 Forgejo 外链；本地入口和视觉可用；真实登录态 Forgejo/PR/Actions 转换待 root 验收 |
| 23 | Settings 能可靠持久化 | 本地通过：主题/偏好/布局/Dock；失败可见并能恢复；显式保存与刷新恢复；CDP + Playwright |
| 24 | Desktop viewport 通过视觉验收 | 本地 Chrome 1920×1080、1440×900、1366×768、1024×768 明/暗和多窗口；本轮有截图逐图检查 |
| 25 | Tablet 基本可用 | 本地 Chrome 820×1180，近全屏窗口且无文档横向溢出；触屏生产路径未测 |
| 26 | Mobile 不出现完全不可操作状态 | 本地 Chrome/Playwright 390×844 单活跃应用、switcher、最小化恢复、设置与 Dock/Launcher 可达 |
| 27 | Keyboard 基础操作可用 | Menu/Tabs、LaunchPad/Palette、窗口 move/resize、Dock 管理、ESC/focus trap；CDP + Playwright |
| 28 | 无明显 accessibility blocker | 本地基础焦点/标签/状态/单窗口/reduced-motion 检查通过；未做专业读屏器/全页面 axe 审计，不声称全面认证 |
| 29 | E2E 核心路径通过 | CDP Shell/Library/Work + 新 Playwright 登录 UI→真实 PKCE callback 代码→桌面→知识库/设置→刷新；后端和身份响应为合成数据；生产账号流程待 root |
| 30 | Production build 通过 | TypeScript/Vite production build 通过 |

### 本轮实际验证

- `uv run pytest`：279 passed，19 个既有 FastAPI/httpx 弃用及 pytest 临时目录清理警告。
- `npm test`：39 passed，0 skipped；包括新增 Playwright usability E2E（系统 Chrome，不下载测试浏览器）与扩展 CDP Shell、既有 Library/Work 浏览器回归。
- `npm run build`：TypeScript + Vite production build 通过。
- `uv run python scripts/export_openapi.py`：重新生成，OpenAPI 无 diff；本轮无后端 API 变更。
- `git diff --check`：通过。
- 本机截图目录：`runtime/verification/issue-71-completion/`。Shell 合成截图包括 Desktop 明/暗、1920/1440/1366/1024/820/390、LaunchPad、Command Palette、Settings、Development、Knowledge Query；#72 的 Library 表格/Inspector/Search/错误/大表证据见 `xelibrary-2026-10-01.md`，本轮再次回归。
- 两套浏览器测试验证了真实 App 中 rotating token、session 503、session 401 和重连保留同一个窗口 DOM/local draft，且 logout 请求数为 0。Playwright 的 OIDC issuer/API 为测试 route，不代表真实 Keycloak、cookie/tunnel 或生产登录已验收。

### 尚未满足原始完整 DoD 的事项

1. OpenWebUI 服务部署、正式入口注册、真实认证和生命周期；本轮实现 unavailable 与统一 frame 状态，不伪造 Connected。跨域应用没有发 `app.status` 时无法可靠区分 HTTP auth/network error，浏览器 iframe load 本身不能证明业务就绪。
2. 原生 Forgejo labels/severity/component/updated_at、PR/Actions/Recent Activity 需要受控平台只读代理；现有 `/work` 不含这些字段，本轮未扩大后端。建议 root 以独立 Issue 明确受控 origin、scope、分页、错误与源更新时间，再接原生页面；本轮未创建或修改 Forgejo Issue。
3. System Storage/HF Cache/deployment history/Tunnel 观测仍缺桌面受控数据 API；当前展示未接入，不编造系统状态。Production Quality worker、全链路多模态等生产依赖不由本轮 UI 提供。
4. §4 建议的 Table/Tree/List/Tooltip/Dialog/Sheet 尚未全部导出为通用 primitive；现有 Library table/tree/list、native title tooltip、Modal/Inspector 使用统一样式与局部实现。§17 所选记录的 AI 动作仍无 Library UI extension API；本轮搜索跨 app intent 不能替代该点。
5. Error UX 尚未对全部 API 错误贯穿真实 request_id：Library 的既有错误展示有将 error code 用作“请求编号”的位置；本轮未扩大到该独立传输/错误格式修复。大表/多窗口性能有有限本地回归，未进行全天真实数据压力、完整对比度/读屏器审计。
6. root 仍需部署后使用真实用户/账号完成完整核心路径、Forgejo 外链、跨服务权限和公网/LAN 登录、真实浏览器视觉与人工作业验收；缺这些证据前不能关闭 #71。

Sol 子 Agent 交付时未推送、未 stage release、未部署或修改 Forgejo Issue 状态/评论；后续发布与验收由 root 执行，记录如下。

## 生产发布后验收：键盘、设置与会话续期增量（2026-10-01）

- 代码 commit：`296a55e98cb2ede4095ed46c3035a30dfc0bc154`；已由 root 推送至 `feature/platform-v1`。
- Deploy Gateway release：`c570bb5a-c455-4de8-b54f-ceebd349fa08`；deployment：`24a41dc8-b2d5-4e2c-89a3-9d2e34decd93`，状态 `completed`，租约已释放。
- 发布前 Desktop DB 快照 `runtime/backups/2026-10-01-issue71-final/predeploy-desktop.sqlite` 的 SQLite `integrity_check` 为 `ok`；本次无 schema/API migration。
- 后端 279 项、前端 39 项测试通过；TypeScript/Vite production build、OpenAPI 导出无 diff、`git diff --check` 通过。
- 五个服务入口 readiness 返回 200；VM 101/104/106/107/111 的 active release symlink 均指向该 commit。
- LAN 与公网首页、JS/CSS 均返回 200，JS/CSS SHA-256 与生产构建一致；公网未认证 `/api/v1/work` 返回预期 401。
- 本次未运行真实生产账号的完整浏览器登录流程。新增浏览器自动化使用合成身份/API route，不能证明当前公网 Keycloak、Tunnel 与用户会话流程；旧版 IP 入口 PKCE 验收记录仍见 deployment runbook。
- Issue #71 保持开放。OpenWebUI 服务、原生 Forgejo Dashboard 与受控只读 API、System 受控数据源、真实账号浏览器验收及其他剩余 DoD 仍未完成；不得将本次增量发布视为 Issue 完成。
