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
