# Issue #81 — 表移动意图与启动对账（第三增量）

本增量把 `table.move` 纳入持久化生命周期日志，并在启动时对账 catalogue 与表元数据路径。

## 写入顺序

`POST /api/v1/tables/{tid}/move` 在进入事务前提交 `pending` 意图，payload 为
`{"from": <旧 path>, "to": <目标 path>}`；成功后 `completed`，任何异常（权限、ETag 412、
目标冲突 409、表正在删除等）回滚并 `aborted`，错误有界写入 `error`。

## 启动对账

`recover_lifecycle_operations()` 对 pending `table.move`：

- catalogue 已无此表 → `aborted`（由删除流程/扫描器负责）；
- 表文件缺失 → `aborted`，交给一致性扫描的 `catalogue_table_file_missing`；
- catalogue path 仍等于 payload `from` → 移动未提交，`aborted`，元数据保持不变；
- catalogue path 已是目标、`__xl_meta.logical_path` 不同 → 在表锁内把元数据路径更新为 catalogue
  路径并 `table_revision+1`（与正常移动的版本语义一致），标记 `completed`，
  `error` 记录 `reconciled ...`；审计仍写 `operation.recovered`；
- 表处于 `deleting` → `aborted`，不触碰路径。

catalogue 是提交权威：崩溃发生在 catalogue 提交之后、表元数据更新之前时，恢复补齐元数据；
崩溃发生在 catalogue 提交之前时，移动视为未发生。该对账不会在启动时移动文件或修改 ACL。

## 测试

`tests/test_library_lifecycle_operations.py` 新增 4 项：

- 成功移动留下 `completed` 意图并更新元数据路径；
- 目标冲突 409 → 意图 `aborted`、路径不变；
- catalogue 已提交但元数据未更新 → 重启补齐路径并 `revision+1`、意图 `completed`；
- catalogue 未提交 → 重启标记 `aborted`、路径不变。

原建表/删表 8 项继续通过。

## 仍未完成（Issue #81 保持开放）

- 导入/恢复的多资源意图（当前行级导入/恢复在单表 SQLite 事务内，天然原子；如后续引入跨资源
  导入需另行设计）；
- 显式修复端点与人工确认流程；
- revision/派生索引深度一致性；
- 真实 SIGKILL/断电故障注入与生产数据验收。
