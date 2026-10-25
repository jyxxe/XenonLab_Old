# XeLibrary 一致性扫描（只读）

对应 Issue #81 第一增量：检测 catalogue、表文件、附件引用、对象存储与上传状态之间的不一致。
扫描**只读**，不自动修复或删除任何数据。

## 调用

以持有 `xelibrary:admin` 的账户调用桌面 API（经网关/服务身份）：

```bash
GET /api/v1/maintenance/consistency
Authorization: Bearer <token>
```

响应为 `{"data": {...}, "meta": {...}}`；`data.ok=true` 表示无 critical 问题。

## revision 与派生索引

- `table_revision_lagging` / `change_revision_ahead` 为 critical：说明 `__xl_meta.table_revision`
  落后于行/变更记录，先停止相关写入、备份现场，再由维护者核对；不要直接改 revision。
- `stale_vector_reference` 为 warning：派生向量指向已删除行或旧 revision，可通过 reindex 重建，
  扫描器不会自动删除。
- `invalid_indexed_revision`、`invalid_index_job`、`index_job_table_missing` 反映索引任务状态，
  可结合索引队列与 reindex 流程处理。

## 判读

1. 先看 `data.ok` 与 `critical_issues`；`warning` 级多为启动恢复或维护操作可处理的状态。
2. `issues[].kind` 见 `docs/acceptance/issue-81-consistency-scan-2026-10-25.md` 对照表。
3. `issues_truncated=true` 说明问题超过 200 项，应缩小范围或先处理批量子集，而不是假定只有报告中的问题。
4. `object_store.orphan_check_complete=false` 时不得按 `unreferenced` 判断孤儿对象，
   需先修复不可读表文件。
5. 扫描结果只是当前读取时刻的观测；孤儿对象在 attachment GC 前后变化属正常。

## 安全边界

- 扫描不执行写语句、不获取写锁、不修改 catalogue/表文件/对象/上传状态。
- 报告可能包含内部 table id、路径与对象 digest，只应提供给管理员；不要粘贴到公开渠道。
- 扫描不能替代备份。发现 critical 不一致时，先停止相关写入、保留现场并核对
  `docs/runbooks/xelibrary-backup-restore.md` 的备份/恢复流程，再由维护者决定恢复或修复。
- 尚未提供修复端点；不要手工编辑 `.xl`/catalogue 文件。缺失表文件时优先从备份恢复。

## 操作日志与自动恢复

- 建表/删表/移动会在 catalogue `operations` 表留下持久意图。扫描报告中的
  `incomplete_lifecycle_operation` 表示恢复后仍有 pending 意图；对照 `kind`/`table_id`/`created_at`
  与 `docs/acceptance/issue-81-lifecycle-journal-2026-10-25.md` 处理。
- 进程重启时 `recover_table_deletions()` 会收尾 `.<tid>.xl.deleting` 墓碑并清除 catalogue 内表的
  `deleting` 标记；随后 `recover_lifecycle_operations()` 对账日志：
  已发布表的建表意图直接完成，未发布且 `__xl_meta.table_id` 匹配的半成品 `.xl` 会被删除并写审计
  `operation.recovered`；删除意图按 catalogue 最终状态标记完成或回滚；移动意图以 catalogue 路径为准，
  已提交但元数据滞后的移动会补齐 `__xl_meta.logical_path` 并增加一次 `table_revision`，未提交的移动标记回滚。
- 因此墓碑/`deleting`/pending 操作类 warning 通常在下次服务重启后消失。若持续存在，说明恢复
  自身失败，应检查服务日志与文件权限，先备份现场再人工处理。
- 已结束的操作记录保留 30 天，只用于对账和审计，不会无限增长。

## 显式修复计划

扫描发现 `interrupted_table_delete` / `table_path_mismatch` / `table_deletion_marker` 时，
可在不重启服务的情况下用 dry-run 计划修复：

1. `POST /api/v1/maintenance/consistency/repairs` body `{"dry_run": true}`，检查返回的
   `actions` 与 `counts`；此时不会改动任何数据。
2. 确认后用同一 `plan_id` 调用 `{"dry_run": false, "plan_id": "..."}`；计划 10 分钟内有效、
   只能使用一次，若扫描状态已变化会返回 `REPAIR_PLAN_STALE` 且不执行。
3. 每项动作写 `maintenance.repaired` 审计；完成后重新扫描复查。

该端点处理与启动恢复一致的墓碑/路径/标记问题，并可**隔离**孤儿 `.xl`：孤儿文件必须已超过
300 秒宽限期且 catalogue 无记录；执行只是移动到 `tables/.quarantine/`（0700），不删除内容，
扫描会以 `quarantined_table_file` 提示复核。恢复需人工核对 `__xl_meta.table_id` 与备份后移回
`tables/<uuid>.xl`；当前没有自动恢复端点。计划生成前请先确认 catalogue 是期望的权威状态；
该端点不替代备份。
