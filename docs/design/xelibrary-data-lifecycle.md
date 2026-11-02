# XeLibrary 数据生命周期设计（Issue #117）

目标：为 `__xl_changes` 变更日志、附件对象与派生索引定义**有界、可审计、可恢复**的保留与回收策略，
不做静默删除，也不新增隐式后台行为。本文件是设计稿；实现拆分为后续独立增量。

## 现状（事实）

- `.xl` 表内：`data`（软删除用 `_deleted_at`）、`__xl_changes`（行变更历史，随行删除保留）、
  `__xl_attachments`（行附件引用）、`__xl_vectors`（D100M 向量）、`__xl_fts`/`__xl_fts_cjk`。
- 附件对象：`DATA_DIR/objects/sha256/<xx>/<yy>/<digest>`，按内容寻址、可被多行/多表共享；
  `DELETE /attachments/{id}` 的清理必须确认无其他引用。
- catalogue：`tables`、`jobs`/`index_jobs`、`bulk_jobs`（含 import/export 输出文件）、`audit`。
- 已有有界策略：bulk/导入导出终态历史与输出清理、embedding 请求历史 1000 条、审计只追加、
  release 目录保留（运维侧 current + 3）。

## 策略

1. **变更日志 `__xl_changes`**
   - 保留窗口：默认每表保留最近 **N=50,000 条或 30 天**（取先到者），只允许通过显式维护端点执行；
   - 有未完成 bulk/index/attachment 作业、或表处于 `deleting` 必须拒绝；
   - 删除按 `(row_id, revision)` 批处理并记录 `audit` 事件（表、删除条数、阈值、操作者）；
   - 不删除 `data` 当前行仍需要的最后一条变更；软删除行的历史随行 purge 一起清理。

2. **附件对象 GC**
   - 对象可共享：仅当 `objects/sha256/...` 不再被**任何表**的 `__xl_attachments.object_hash` 引用时回收；
   - 扫描顺序：先建立引用集合（跨全部 `.xl`），再与对象目录差集；默认 dry-run，输出候选与字节数；
   - 回收前校验对象 mtime 早于宽限期（默认 24h），避免与正在进行的上传/绑定竞争；
   - 每个删除写入 `audit`（object digest、大小、来源表计数）；
   - 上传临时目录（`objects/.uploads`）同样按宽限期清理，并跳过进行中的分片会话。

3. **派生索引与向量**
   - 行删除/更新已有 qdrant 清理；表删除前要求无活动索引任务（已实现）；
   - `__xl_vectors` 只保留与当前行 revision 匹配的向量；旧 revision 向量在 reindex 成功后可清理；
   - 模型空间变更（D100M → Qwen）不自动删除旧 collection，由运维按 runbook 显式迁移，避免不可逆。

4. **导入/导出与 bulk 输出**
   - 沿用现有有界策略（200 条终态、取消/失败清理文件）；
   - 新增统一维护端点时一并清理孤儿输出文件（`exports/` 无对应 job）。

5. **指标**
   - `xelibrary_changes_rows`（每表/总量）、`xelibrary_attachment_objects{referenced,unreferenced,bytes}`、
     `xelibrary_orphan_output_files`、`xelibrary_prune_runs_total{result}`、`xelibrary_prune_deleted_bytes`；
   - 系统面板「存储」在指标可用时展示池水位与 XeLibrary 增长（消费只读 API，不做管理操作）。

## 端点与权限（后续实现）

- `POST /api/v1/tables/{tid}/maintenance/prune-changes`（`xelibrary:admin`，body: dry_run/limit/older_than_days）；
- `POST /api/v1/storage/attachment-gc`（`xelibrary:admin`，默认 dry-run，返回候选与确认 token）；
- 两者均要求 `If-Match` 表/目录 revision 语义或显式确认参数，默认 dry-run，真实执行写入 audit。

## 故障与恢复

- 维护操作幂等：重复执行只删除候选，不扩大范围；中断后重新 dry-run 可继续；
- 附件 GC 在删除对象前写入待删清单（同目录临时文件 + fsync + rename），重启后可对账；
- 任何维护失败必须保持数据可读，不能造成半删除；错误以 typed `APIError` 返回并记录审计。

## 验收（实现阶段）

- dry-run 不删除任何数据；真实执行仅删除候选并产生审计；
- 共享附件不被误删（多行/多表引用测试）；宽限期内对象不被回收；
- 并发上传/绑定与 GC 竞争安全；维护中断可恢复；指标与审计可见；
- 性能：10 万行表 dry-run 有界内存（分页/游标），不阻塞正常读写超过设定阈值。

## 实现进展（2026-10-24）

首个增量已落地：`POST /api/v1/tables/{tid}/maintenance/prune-changes`（`xelibrary:admin` + 表写权限）：
默认 `dry_run=true`，按 `older_than_days`（1–3650）与 `keep_recent`（默认 5 万）计算候选，批处理 `limit`，
保留每行最新 revision；活动 bulk/index 作业或 deleting 表返回 409；真实删除写 audit。附件对象 GC、
增长指标与系统面板接线仍待后续增量（本文件保持设计状态）。

第二增量：`POST /api/v1/storage/attachment-gc`（xelibrary:admin，dry-run 默认）跨全部表引用差集、
24h 宽限期、limit 有界、真实执行需 dry-run 的 confirm token、逐对象 audit；上传临时目录不参与回收。
增长指标与系统面板接线仍待后续。

第三增量：`GET /api/v1/storage/telemetry`（xelibrary:admin 只读）提供每表 changes/附件计数与
内容寻址对象 referenced/unreferenced 字节数，供系统面板「存储」与运维监控消费；不做回收。

第四增量：桌面系统面板「存储」新增只读「XeLibrary 存储增长」区（`xelibrary:admin`），
消费 `/api/v1/storage/telemetry`；不提供自动维护操作。HF-cache 池水位仍依赖 #66 服务路由。

第五增量（Issue #81 第一增量，2026-10-25）：新增只读一致性扫描
`GET /api/v1/maintenance/consistency`（`xelibrary:admin`）。扫描 catalogue ↔ `.xl` 文件、
附件引用 ↔ 内容寻址对象、上传会话/暂存目录、删除墓碑与表元数据；默认最多 200 项问题，
对象孤儿统计在存在不可读表文件时 fail closed（`orphan_check_complete=false`）。
不执行修复、不写用户数据；持久化操作 journal/outbox 与显式修复动作仍待后续增量。

第六增量（Issue #81 第二增量，2026-10-25）：catalogue 新增 `operations` 意图日志，覆盖
`table.create` / `table.delete`。意图在文件/目录/上传清理前独立提交；启动时
`recover_lifecycle_operations()` 在既有墓碑恢复之后对账日志：已发布的建表保留并完成，
未发布的建表文件仅在意图与 `__xl_meta.table_id` 一致（或文件不可读）时删除，删除意图按
catalogue 最终状态标记 completed/aborted；已结束记录保留 30 天。移动、导入/恢复意图、
显式修复端点与真实故障注入仍待后续增量。

第七增量（Issue #81 第三增量，2026-10-25）：`table.move` 纳入 `operations` 意图日志。启动对账以
catalogue 为权威：catalogue 尚未提交的移动标记 aborted；catalogue 已提交而 `__xl_meta.logical_path`
滞后时，在表锁内补齐路径并 `table_revision+1`，标记 completed；表删除中/文件缺失不自动改动。

第八增量（Issue #81 第四增量，2026-10-25）：新增 `POST /api/v1/maintenance/consistency/repairs`
（xelibrary:admin，dry-run 默认）。dry-run 生成 `repair_plans` 计划（≤500 项，1 天清理）；执行需
plan_id，校验未使用/未过期/候选未变化，逐项写 `maintenance.repaired` 审计。仅覆盖墓碑收尾/回滚、
catalogue/元数据路径对账与 deleting 标记清除；孤儿 `.xl` 回收、导入/恢复意图与真实故障注入仍待后续。

第九增量（Issue #81 第五增量，2026-10-25）：只读一致性扫描增加 revision 与派生索引检查：
`table_revision_lagging`、`change_revision_ahead`、`table_schema_empty`、`stale_vector_reference`、
`invalid_indexed_revision`，以及 catalogue `index_jobs` 的无效 payload/未知表引用。均为只读检测，
不自动删除或重建向量；revision 元数据 critical 项需先备份现场再由维护者处理。

第十增量（Issue #81 第六增量，2026-10-25）：修复计划新增孤儿 `.xl` 的**非破坏性隔离**。扫描候选要求
catalogue 无记录且 mtime 超过 300 秒宽限期；执行仅把文件移动到 `tables/.quarantine/`（0700），写审计，
不删除内容。扫描器把隔离文件报告为 `quarantined_table_file` warning 而非孤儿；恢复需人工核对后移回。

第十一增量（Issue #81 第七增量，2026-10-25）：隔离恢复。当 catalogue 表存在但规范 `.xl` 缺失、
且隔离目录中存在 `__xl_meta.table_id` 匹配的副本时，修复计划可 `restore_from_quarantine`：表锁内复核
catalogue/目标/identity 后移回主文件并带走 sidecar。identity 不匹配或状态变化一律跳过/stale；
不提供丢弃隔离文件的动作，恢复后仍需按 runbook 抽查。

第十二增量（Issue #81 第八增量，2026-11-02）：新增派生索引重建
`POST /api/v1/tables/{tid}/maintenance/rebuild-indexes`（`xelibrary:admin` + 表写权限，dry-run 默认）。
dry-run 比对 `data` 有效行数与 `__xl_fts`/`__xl_fts_cjk`/`__xl_fts_map` 行数并返回 `consistent`；
真实执行在单个表事务内清空并按当前行重写三张派生索引、清空 `fts_map_backfilled` 标记，写
`table.indexes_rebuilt` 审计。活动 bulk/index 作业或 deleting 表返回 409；失败整体回滚，不产生半删除
索引。运维步骤见 `docs/runbooks/xelibrary-index-rebuild.md`。生产真实数据恢复演练、导入/恢复意图
日志与向量索引重建仍待后续。

