# Issue #95 — 附件对象 GC（第二增量）

- 新增 `POST /api/v1/storage/attachment-gc`（`xelibrary:admin`）：默认 `dry_run=true`，扫描
  `objects/sha256/<xx>/<yy>/<digest>`，跨全部 `.xl` 表收集 `__xl_attachments.object_hash` 引用，
  仅把**未被引用且 mtime 早于 `grace_hours`（1–720）**的对象列为候选；`limit` 1–100000。
- dry-run 返回候选 digest/bytes 与 `confirm` token（候选集合+参数派生）；真实执行必须携带该 token，
  否则 409 `GC_CONFIRMATION_REQUIRED`；真实删除逐对象 unlink 并写 audit（候选/删除计数）。
- 引用对象与宽限期内的新对象不会被删除；回收前不触碰上传临时目录（`objects/.uploads`）。
- 测试：`tests/test_library_attachment_gc.py`（admin 门禁、dry-run 仅列旧孤儿、无确认 409、确认后只删候选、
  引用/新对象保留、非法参数 422）。
