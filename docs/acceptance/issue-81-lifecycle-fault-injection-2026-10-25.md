# Issue #81 — 生命周期恢复的发布门禁故障注入场景

`scripts/verify_failure_injection.py` 是 CI backend job 已执行的进程级故障注入脚本（含 SIGKILL
事务边界、XeLibrary 并发写崩溃、协作 claim fence）。本增量新增
`scenario_lifecycle_recovery`：在临时数据目录上用**真实 XeLibrary 服务进程重启**验证
Issue #81 的启动恢复，不触碰 LAN/生产。

## 场景

1. 通过真实 API 创建三张表后停止服务；
2. 直接构造三种崩溃遗留（与真实持久化边界一致）：
   - 删除已写墓碑但 catalogue 行仍在（应回滚）；
   - 建表意图 + 未发布 `.xl`（identity 匹配，catalogue 无行，应删除）；
   - catalogue 路径已提交但 `__xl_meta.logical_path` 滞后（应补齐）；
   并写入三条 `pending` operations 意图；
3. 重新启动真实服务，断言：
   - 三张 catalogue 表均可读；
   - 删除墓碑恢复为规范文件；
   - 未发布 `.xl` 被删除；
   - 移动后表路径为 `/已移动`；
   - operations 状态分别为 `aborted`（删除回滚）、`completed`（未发布创建清理）、`completed`（移动对账）；
   - `audit` 至少三条 `operation.recovered`。
4. 输出 `PASS lifecycle recovery: ...` 并停止服务。

## 边界与诚实说明

- 该场景是**重启恢复**验证：崩溃遗留通过直接写入持久化状态构造，而非在操作窗口内真实 SIGKILL；
  脚本中其他场景仍保留真实 SIGKILL 证据；
- 不覆盖导入/恢复（当前为单表 DB 事务内的行级操作）与派生索引重建；
- 只在临时目录与 127.0.0.1 上运行，不读取生产配置、不写生产数据；
- CI backend job 已执行该脚本，失败会使门禁失败。

## 结果

本地 `uv run python scripts/verify_failure_injection.py`：四个场景全部 PASS（含
`PASS lifecycle recovery`）。Ruff 通过。该场景进入 CI 后为 #81 的启动恢复提供发布门禁。
