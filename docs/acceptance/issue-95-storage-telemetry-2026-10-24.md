# Issue #95 — 存储增长指标（第三增量）

- 新增 `GET /api/v1/storage/telemetry`（`xelibrary:admin`，只读）：返回
  - `tables[{table_id, changes_rows, attachment_refs, attachment_bytes}]`；
  - `totals{tables, changes_rows, attachment_refs, attachment_bytes}`；
  - `attachment_objects{total, referenced, unreferenced, bytes_total, bytes_unreferenced}`
    （内容寻址对象与跨表 `__xl_attachments.object_hash` 引用差集）。
- 不执行任何回收或修改；不可读的表文件会被跳过而不是整体失败。
- 测试：`tests/test_library_storage_telemetry.py`（admin 门禁、每表 changes/附件计数、
  对象总数/referenced/unreferenced/字节数）。
- 系统面板「存储」接线与 HF-cache 池水位仍依赖 #66 服务路由；本增量提供可消费的管理只读数据。
