# Issue #81 — XeLibrary 只读一致性扫描（第一增量）

本次增量实现 #81 的**检测**部分：新增 `GET /api/v1/maintenance/consistency`
（`xelibrary:admin`，只读），扫描 catalogue、规范化表文件、附件引用、内容寻址对象与上传状态，
以有界 JSON 报告不一致。**本增量不做修复**：不删除、不改写、不重建任何用户数据；持久化操作
journal/outbox 与显式修复动作留给后续增量。

## 端点语义

- `mode` 固定为 `read_only`；`scanned_at` 为 UTC ISO 时间。
- `ok=true` 表示没有 `critical` 级问题；`warning` 级问题（可自动恢复或可维护）不使 `ok=false`。
- `issues[]` 每项含 `kind`、`severity`（`critical`/`warning`）、可选 `table_id`、`detail`；
  列表默认最多 200 项，超出置 `issues_truncated=true`。
- `counts`：catalogue 表数、规范化表文件数、删除墓碑数、附件引用数、上传会话数。
- `object_store`：对象总数/字节数、未被任何 `__xl_attachments.object_hash` 引用的对象数/字节数；
  若存在不可读表文件，无法确信引用集合完整，则 `orphan_check_complete=false` 且
  `unreferenced=null`（fail closed，不谎报孤儿数量）。

检查项：

| kind | 级别 | 含义 |
|---|---|---|
| `catalogue_unreadable` / `table_file_unreadable` | critical | catalogue 或表文件无法按 SQLite 读取 |
| `catalogue_table_file_missing` | critical | catalogue 有记录但 `<tid>.xl` 缺失 |
| `orphan_table_file` | critical | 存在 `<tid>.xl` 但 catalogue 无记录 |
| `table_schema_incomplete` / `table_metadata_invalid` | critical | 表文件缺少必需表或元数据行数异常 |
| `table_identity_mismatch` | critical | `__xl_meta.table_id` 与文件名/catalogue 记录不一致 |
| `invalid_attachment_reference` / `missing_attachment_object` | critical | 附件 digest 非法或对象缺失 |
| `unreadable_object_entry` | critical | 对象文件无法 stat |
| `interrupted_table_delete` | warning | 存在 `.<tid>.xl.deleting` 墓碑，启动恢复会收尾 |
| `table_path_mismatch` | warning | `__xl_meta.logical_path` 与 catalogue path 不一致 |
| `table_deletion_marker` | warning | 表被标记 deleting，启动恢复会清除 |
| `attachment_row_missing` | warning | 附件引用指向已不存在的 row |
| `upload_table_missing` | warning | 上传会话引用不存在的表 |
| `orphan_upload_staging` / `stale_upload_temp` | warning | 无会话的上传临时目录/装配文件 |
| `unexpected_table_entry` / `unexpected_object_entry` / `unexpected_upload_entry` | warning | 目录内非预期条目 |

`tables/` 下的 `-wal`/`-shm`/`-journal` sidecar 与 `objects/.uploads/.locks` 视为预期条目；
其余无法识别的条目按 `unexpected_*` warning 报告。

## 只读性

扫描通过 SQLite 只读 URI（`mode=ro`）打开 catalogue、表文件与 `uploads.sqlite`，不执行任何
写语句，也不获取表写锁。SQLite 自身可能为 WAL 数据库建立/刷新共享内存 sidecar，但用户数据
（`.xl` 表文件与对象文件）逐字节不变，且扫描不会恢复、移动或删除任何内容。规范表文件在扫描期间
若被并发创建/删除，orphan 判定使用前后两次 catalogue 快照的并集，以降低瞬时误报。

## 测试

`tests/test_library_doctor.py`（11 项）：admin 门禁、干净态、缺失/孤儿表文件、删除墓碑、
deleting/path 漂移、identity 不一致、附件引用四类问题、孤儿对象计数与只读性（用户数据字节不变）、
不可读表文件、上传遗留、问题列表截断、catalogue 不可读。

## 未完成（Issue #81 保持开放）

- 持久化操作 journal/outbox 与 create/move/delete/import/restore 的崩溃恢复；
- 显式修复动作（`--repair <operation-id>` 等）与缺失 `.xl` 的恢复决策；
- 表 revision 与派生索引/revision 元数据的深度一致性；
- 生产只读扫描与真实故障注入验收。

本地通过不代表生产数据已验证；生产验收需在真实宿主上以管理员身份读取，并与受信备份/宿主视图核对。
