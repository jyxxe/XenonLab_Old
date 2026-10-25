# Issue #81 — 孤儿 `.xl` 非破坏性隔离（第六增量）

在已有 dry-run 修复计划上增加孤儿表文件的**隔离（quarantine）**动作：不删除用户数据，只把
catalogue 不认识的 `.xl` 移出活动目录，等待管理员复核后人工恢复或清理。

## 行为

- `repair_candidates()` 增加 `orphan_table_file` 候选：`tables/<uuid>.xl` 存在、catalogue 无该表、
  且 mtime 早于 **300 秒**（宽限期，避免与正在进行的建表竞争）。
- dry-run 计划包含 `{"kind":"orphan_table_file","table_id":...,"action":"quarantine"}`。
- 执行时把文件移动到 `tables/.quarantine/<uuid>.xl`（目录 0700；重名时追加随机后缀），
  写 `audit` 事件 `maintenance.repaired`，返回 `result=quarantined`；不删除内容、不改写文件。
- 计划仍受 500 项上限、10 分钟 TTL、单次使用与状态变化（`REPAIR_PLAN_STALE`）保护；
  若 dry-run 后该文件被恢复为 catalogue 表或消失，重算候选不同，执行会被拒绝。

## 扫描行为

- `tables/.quarantine/` 内的文件不再计入 `orphan_table_file`；
- 报告新增 `quarantined_table_file` warning 与 `quarantined_table_files` 计数，提示管理员复核；
- 隔离目录本身不被当作"非预期表目录条目"。
- 隔离不会让文件对 catalogue 可见：恢复需人工把文件移回 `tables/<uuid>.xl` 并核对
  `__xl_meta.table_id`，该流程暂不提供自动恢复端点。

## 测试

`tests/test_library_repairs.py` 新增 3 项：dry-run 只列出超过宽限期的孤儿文件、执行后文件被移动
且字节不变/审计存在/扫描报告隔离文件而非孤儿、dry-run 后文件消失导致计划过期。原 9 项继续通过。

## 仍未完成（Issue #81 保持开放）

- 隔离文件的自动/显式恢复（需要与备份核对）；
- 导入/恢复跨资源意图；
- 派生索引重建/回滚流程；
- 真实 SIGKILL/断电故障注入与生产数据验收。
