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
