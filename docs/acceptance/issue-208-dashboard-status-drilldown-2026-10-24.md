# Issue #208 — 系统面板任务状态直达任务筛选（2026-10-24）

- System 面板「Harness 任务队列」图例的五个状态（排队/进行中/已过期/已完成/阻塞）现在是可聚焦按钮，
  点击后打开任务中心并**预选对应状态筛选**（`AppIntent.action=workStatus`）。
- `DesiredState`：普通 `openApp(id)`（如「打开任务中心」）会清除该应用遗留的 intent，避免再次打开时
  沿用旧筛选；带 intent 的打开仍保留既有 search/theorems/openTable 行为。
- `Issues` 新增对 `workStatus` intent 的处理（设置筛选 tab）；筛选逻辑维持既有 status/expired/active 语义。
- 无障碍：图例按钮有 `aria-label="查看<状态>任务"`，键盘可聚焦并 Enter 打开。
- 测试：扩展 `tests/browser-acceptance-dashboard-task-entry.test.mjs`，点击「排队」图例后断言任务中心打开、
  `排队` tab 选中且只显示 queued 合成任务；原有指针/键盘「打开任务中心」路径保持不变。

浏览器回归中 6 个与本改动无关的用例出现 `Timed out waiting for Chrome DevTools endpoint`（本机 4 CPU
同时跑完整回归时 Chrome 启动超时）；单独运行本用例通过，preflight/CI 将在 CI runner 上复核浏览器回归。
