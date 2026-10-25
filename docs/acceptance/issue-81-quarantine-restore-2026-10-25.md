# Issue #81 — 隔离文件恢复到缺失的 catalogue 表（第七增量）

在隔离（quarantine）基础上增加安全恢复动作：当 catalogue 仍有一张表、但规范 `.xl` 缺失，而隔离
目录中存在 `__xl_meta.table_id` 完全匹配的副本时，dry-run 修复计划提供
`catalogue_table_file_missing` → `restore_from_quarantine`。

## 行为

- dry-run 扫描 `tables/.quarantine/`，按文件名首段 UUID 分组；仅当：
  - catalogue 中有该表，且 `tables/<uuid>.xl` 不存在；
  - 隔离文件可读且 `__xl_meta.table_id == <uuid>`；
  才生成恢复候选（每个表最多一个候选，带 `file` 名）。
- 执行时在表锁内重新校验 catalogue 行、目标文件不存在、隔离文件存在且 identity 匹配；
  通过后 `os.replace` 回 `tables/<uuid>.xl`，并把同名 `-wal`/`-shm`/`-journal` sidecar 一起移回，
  写 `maintenance.repaired` 审计，返回 `restored-from-quarantine`。
- identity 不匹配、catalogue 行不存在、目标已存在、隔离文件消失 → 返回对应 `skipped-*`，
  不移动任何文件；若 dry-run 后状态变化，计划比对会先返回 409 `REPAIR_PLAN_STALE`。
- 隔离移动（上一增量）现在也会把 sidecar 一起移动，避免 WAL 数据留在活动目录或恢复时丢失。

## 扫描

`catalogue_table_file_missing` 的 detail 在检测到同名隔离副本时追加
`a quarantined copy exists for explicit restore review`；隔离 sidecar 不再计入
`quarantined_table_files` 计数。

## 测试

`tests/test_library_repairs.py` 新增/扩展 4 项：

- 隔离时 sidecar 一并移动；
- 缺失 catalogue 文件从 identity 匹配的隔离副本恢复（字节一致、隔离目录清空、扫描复查）；
- identity 不匹配的隔离副本不提供恢复候选；
- dry-run 后隔离文件消失导致计划 stale。

## 边界

- 恢复只对 identity 完全匹配的隔离副本生效；不猜测、不改写表内容；
- 不提供"丢弃隔离文件"动作（不可逆删除仍需人工/备份流程）；
- 不替代备份，也不验证表内容与业务期望一致（恢复后仍应按 runbook 抽查）。
