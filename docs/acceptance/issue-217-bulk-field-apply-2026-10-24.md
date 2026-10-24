# Issue #217 — Table View 批量字段应用（2026-10-24）

- 多选行后，Inspector 概览显示「批量字段应用」：选择字段、输入值、点击「应用到已选」；
  使用既有 `POST /api/v1/tables/{tid}/rows:bulkUpdate`（携带每行 `row_id`/`revision` 与 `batch_id`），
  同步模式立即刷新，异步 job 模式轮询 `rows:bulk/{job_id}` 至终态后刷新。
- 权限：仅 `xelibrary:write` 显示操作；否则提示需要权限。值通过 `normalizeRowValues` 按列类型归一化。
- 既有单行编辑、Ask AI、TSV 复制等行为不变。
- 测试：`tests/browser-acceptance-library-bulk-field.test.mjs`（真实 LibraryApp + 合成 API）多选两行、
  应用 title=共享分类值，断言仅一个 bulkUpdate 请求、两行 values 与 batch_id；fixture 增加 bulkUpdate 路由。
