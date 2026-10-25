# Issue #81 — 显式修复计划端点（第四增量）

本增量提供 `xelibrary:admin` 的**dry-run 优先 + 显式计划确认**修复端点，覆盖启动恢复已在
处理的三种安全不一致：删除墓碑、catalogue/元数据路径漂移、残留 `deleting` 标记。端点在长
运行服务上无需重启即可收尾；每次真实执行都写审计。

## 端点

`POST /api/v1/maintenance/consistency/repairs`（`xelibrary:admin`）

```json
{"dry_run": true}                                  // 生成计划，返回 plan_id + actions
{"dry_run": false, "plan_id": "<dry-run 返回的 id>"} // 执行已生成且仍有效的计划
```

### dry-run

- 只读扫描并返回 `actions[]`（`kind`/`table_id`/`action`，路径漂移含 `from`/`to`）；
- 把计划写入 catalogue `repair_plans(id, created_at, plan, used)`，**不改动任何文件或表**；
- 计划最多 500 项，超过返回 413 `REPAIR_TOO_LARGE`（要求分批人工处理）；
- 生成计划时顺带清理 1 天前的旧计划/已用计划，避免无限增长。

### 执行

- 必须提供 dry-run 返回的 `plan_id`；缺失 422 `REPAIR_PLAN_REQUIRED`；
- 计划不存在 404、已使用 409 `REPAIR_PLAN_USED`、超过 10 分钟 409 `REPAIR_PLAN_EXPIRED`；
- 执行前**重新计算**候选并与保存的计划精确比较；状态变化返回 409 `REPAIR_PLAN_STALE`，
  不执行任何动作（防止 TOCTOU）；
- 成功执行后标记 `used=1`，逐项返回 `result`（restored/finished/reconciled/cleared/
  skipped-missing-*），每项写 `audit` 事件 `maintenance.repaired`。

## 支持的动作

| kind | action | 行为 |
|---|---|---|
| `interrupted_table_delete` | `restore` | catalogue 仍有表：墓碑恢复为规范 `.xl`，清除 `deleting` |
| `interrupted_table_delete` | `finish` | catalogue 已删除：清理该表上传会话并删除墓碑 |
| `table_path_mismatch` | `reconcile_path` | 表锁内把 `__xl_meta.logical_path` 对齐 catalogue 路径并 `table_revision+1` |
| `table_deletion_marker` | `clear_deleting_marker` | 清除 catalogue 内表的残留 `deleting` 标记 |

上述行为与启动恢复语义一致；端点不会删除孤儿 `.xl`、不会改写 ACL、不会处理未知/不可读文件，
这些仍由只读扫描报告交人工决策。

## 测试

`tests/test_library_repairs.py`（9 项）：admin 门禁、dry-run 不改状态、恢复墓碑、完成已提交删除
并清理上传、路径对账 + 标记清除 + 扫描复查、计划复用时 409、过期计划 409、缺失/未知 plan_id、
超过 500 项拒绝。

## 仍未完成（Issue #81 保持开放）

- 孤儿 `.xl` 的显式回收（需要更强 provenance/备份核对，不能仅凭文件名删除）；
- 导入/恢复跨资源意图；
- revision/派生索引深度一致性；
- 真实 SIGKILL/断电故障注入与生产数据验收。
