# Issue #95 — 系统面板「存储」接线 XeLibrary 增长指标（第四增量）

- 系统面板「存储」在 HF-cache 水位卡片之外新增只读「XeLibrary 存储增长」区：
  知识表数/changes 行数、附件引用与字节、内容寻址对象总数/字节、未被引用对象数/字节。
- 仅当账户具备 `xelibrary:admin` 时请求 `GET /storage/telemetry`（同源经 xelibrary 路由）；
  否则显示权限说明；读取中显示状态提示；不提供任何自动删除/维护按钮，维护需管理员显式端点（默认 dry-run）。
- 测试：`browser-acceptance-issue201-storage.test.mjs` 扩展断言（changes 42 行、未被引用区渲染）；
  `system-fixture` 增加合成 `/storage/telemetry` 与 admin scope；原有 ok/forbidden/unavailable/not-wired 用例保持通过。
