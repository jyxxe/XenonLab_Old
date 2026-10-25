# Issue #81 — 表生命周期操作日志与启动恢复（第二增量）

本增量在只读一致性扫描之上加入**持久化多资源操作日志**，覆盖表创建与表删除。
意图在任何文件/目录/上传清理之前提交到 catalogue 的 `operations` 表；崩溃后启动恢复
先按既有文件级恢复收尾，再对账日志并清理可证明未发布的建表文件。

## 数据模型

`catalogue.sqlite` 新增：

```text
operations(id TEXT PK, kind TEXT, table_id TEXT, state TEXT,
           payload TEXT, created_at TEXT, updated_at TEXT, error TEXT)
```

- `kind`：`table.create` / `table.delete`（本增量）；其他 kind 在恢复时标记 `aborted`。
- `state`：`pending` → `completed` / `aborted`。
- 完成/中止的记录保留 30 天后由启动恢复清理；`pending` 永不自动删除。

## 写入顺序

- **建表**：写 `pending` 意图 → 同事务插入 catalogue 行并创建 `.xl` → 成功 `completed`；
  业务失败时删除半成品文件并 `aborted`（错误写入 `error`，最多 500 字符）。
- **删表**：通过全部 409 前置检查后写 `pending` 意图 → qdrant 清理 → `deleting=1` →
  改名墓碑 → catalogue 删除 → 上传清理 → 删除墓碑 → `completed`；任一步抛错则回滚并 `aborted`。
- 意图写入是独立已提交事务，崩溃时至少留下可重放的记录。

## 启动恢复

`recover_lifecycle_operations()` 在 `recover_table_deletions()` 之后运行：

- `table.create` 且 catalogue 有此表 → `completed`，保留文件；
- `table.create` 且 catalogue 无此表、文件存在 → 仅在文件 `__xl_meta.table_id` 与意图一致
  （或文件不可读）时删除该未发布文件，并写 `audit` 事件 `operation.recovered`；
- `table.delete` 且 catalogue 仍有此表 → 既有文件恢复已回滚，日志标记 `aborted`；
- `table.delete` 且 catalogue 已无此表 → `completed`（墓碑/上传由既有恢复清理）。
- 已结束记录超过 30 天删除；`pending` 保留待人工/后续增量处理。

日志不改变既有表文件、行、附件语义；创建/删除的对外 API 响应与错误码不变。

## 测试

`tests/test_library_lifecycle_operations.py`（9 项）：

- 成功创建/删除分别留下 `completed` 意图；
- 重名创建失败 → `aborted` 且不留下孤儿表文件；
- 未发布建表文件在重启后按意图删除，已发布表保留，`operation.recovered` 写入审计；
- 删除意图在 catalogue 保留时被标记 `aborted` 且表文件恢复；
- catalogue 已删除但墓碑残留时，重启完成清理并标记 `completed`；
- 超过 30 天的已结束记录被清理；
- 一致性扫描把 pending 意图报告为 `incomplete_lifecycle_operation` warning。

## 仍未完成（Issue #81 保持开放）

- 移动（`table.move`）的意图记录与恢复对账；目前仍只有扫描器的 `table_path_mismatch` 检测；
- 导入/恢复（`table.import`/`table.restore`）的可检测意图；
- 显式修复端点（如 `--repair <operation-id>`）与人工确认流程；
- revision/派生索引深度一致性；
- 各持久化边界的真实故障注入（SIGKILL/断电）与生产数据验收。

本增量只覆盖建表/删表的意图持久化与启动对账，不声称 #81 全部恢复/修复验收完成。
