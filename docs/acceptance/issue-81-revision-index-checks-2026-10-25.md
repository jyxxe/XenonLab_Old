# Issue #81 — revision 与派生索引一致性检测（第五增量）

本增量扩展只读一致性扫描，覆盖表 revision 元数据与派生索引状态；仍在检测阶段，不自动重写。

## 新增检查

| kind | 级别 | 含义 |
|---|---|---|
| `table_revision_lagging` | critical | `data._revision` 最大值大于 `__xl_meta.table_revision` |
| `change_revision_ahead` | critical | `__xl_changes.revision` 最大值大于 `table_revision` |
| `table_schema_empty` | critical | `__xl_schema` 无任何列定义 |
| `stale_vector_reference` | warning | `__xl_vectors` 指向已删除/不存在行，或 revision 与当前行不一致（派生数据，可重建） |
| `invalid_indexed_revision` | warning | `__xl_changes.indexed_revision > revision` |
| `invalid_index_job` | warning | catalogue `index_jobs.payload` 不是 JSON 对象 |
| `index_job_table_missing` | warning | 索引任务引用 catalogue 中不存在的表 |
| `index_job_state_unreadable` | warning | `index_jobs` 无法读取 |

报告新增计数：`stale_vector_references`、`index_jobs`。所有检查使用只读连接，不修改表或索引；
`stale_vector_reference` 为 warning，因为 reindex 可重建，不作为数据丢失主张。

## 边界

- 只检查表文件内实际存在的 `__xl_vectors`（未启用向量索引的表没有该表，跳过）；
- 不验证向量内容、模型空间或 Qdrant 一致性（需要外部服务，超出只读扫描）；
- 不自动删除 stale 向量；修复需通过 reindex 流程。

## 测试

`tests/test_library_doctor.py` 新增 4 项：revision 双向漂移、空 schema、stale 向量 + 非法
indexed_revision、未知表索引任务 + 非法 payload。原 11 项继续通过。

## 仍未完成（Issue #81 保持开放）

孤儿 `.xl` 显式回收 provenance、导入/恢复跨资源意图、显式回滚/重建派生索引流程、真实 SIGKILL/断电
故障注入与生产数据验收。
