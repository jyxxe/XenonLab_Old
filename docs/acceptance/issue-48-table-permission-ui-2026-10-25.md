# Issue #48 — 表级 ACL 权限驱动的 Inspector/TableView 写操作门禁

## 背景

#48 复现报告指出：Library Inspector 的字段编辑、保存、软删除控件与 TableView 的“可写/只读”
指示只按身份 `xelibrary:write` scope 判断，没有反映当前表/路径 ACL；被显式只读授权的用户仍看到
可写控件，随后被服务端拒绝。

## 服务端

- `GET /api/v1/tables/{tid}` 与 `/tables/{tid}/schema` 响应新增
  `permissions: {read, write, attach}`，由调用者身份（含账号树继承、`*`、最近显式 ACL）在服务端计算。
- `GET /api/v1/tables` 每张可见表也返回 `permissions`；列表使用单个 catalogue 连接完成 read/write/attach
  判定（`allowed()` 增加可选共享连接，语义不变）。
- ACL 语义完全复用既有 `allowed()`：显式 ACL 命中后不再回退 owner，admin scope 仍短路为允许。
  响应只是把判定结果告知调用者，不改变任何授权行为。

## 前端

- `types.Table` 增加 `permissions`；`logic.js` 新 helper `tableWriteAllowed(scopes, table)`：
  - 服务端返回 `permissions.write` 时以它为准；
  - 旧响应/合成 fixture 缺少 `permissions` 时回退到 `xelibrary:write` scope（保持兼容）。
- LibraryApp 用该 helper 计算当前表写权限，驱动「新增记录」「移动」按钮、TableView 的可写指示与
  Inspector 的字段/保存/软删除/批量更新/历史恢复控件；Inspector 新增可选 `canWrite` prop，
  缺省仍回退 scope。只读提示改为“身份或当前表 ACL 不允许写入”。

## 验证

- 后端 `tests/test_library_permissions.py`（4 项）：owner/admin 全权限；显式 ACL 只读时
  write/attach=false；授予后恢复 true；显式拒绝 owner 时读取 403；`GET /tables` 列表权限随 ACL 变化。
- 前端 `tests/library-logic.test.mjs` 新增 gate 单测；`npm test` 161 passed；`npm run build` 通过。
- 浏览器验收：`browser-acceptance-library`、`browser-acceptance-library-bulk-field`、
  `browser-acceptance-inspector-drafts` 均通过（合成 API fixture 无 permissions 时按 scope 回退）。
- 后端全量 `uv run pytest`、Ruff、OpenAPI 导出在本 PR 门禁中执行。

## 边界

- 浏览器验收使用合成 fixture，不等于生产真实受限账号/ACL 的写路径验收；#48 仍需生产临时 ACL
  浏览器验收（保持开放）。
- 服务端仍是唯一授权权威；前端门禁只是提前禁用明显不允许的操作，不替代 403 处理。
