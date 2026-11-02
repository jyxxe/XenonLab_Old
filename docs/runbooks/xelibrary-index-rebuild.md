# XeLibrary 派生索引重建 runbook

适用：`XeLibrary` 文本/中文搜索索引（`__xl_fts`、`__xl_fts_cjk`、`__xl_fts_map`）与当前行
数据不一致，或只读一致性扫描/人工核查发现索引缺失、过期、重复。

只读发现手段：

- `GET /api/v1/maintenance/consistency`（`xelibrary:admin`）：索引相关告警（如
  `stale_vector_reference`、revision 类问题）；
- `GET /api/v1/storage/telemetry`（`xelibrary:admin`）：每表 `changes_rows` 等增长指标；
- `POST /api/v1/tables/{tid}/maintenance/rebuild-indexes` 的 `dry_run=true`：直接比对
  `data` 行数与两张 FTS 表行数，返回 `consistent`。

## 操作步骤

1. 确认没有进行中的写入型维护：
   - 该表不能有 queued/running 的 bulk row job（`409 TABLE_BULK_ACTIVE`）；
   - 不能有 queued/running 的 index job（`409 TABLE_INDEX_ACTIVE`）；
   - 表不能处于 deleting 状态（`409 TABLE_DELETING`）。
2. 先 dry-run：
   ```bash
   curl --cacert /etc/devagent/lan-root-ca.crt \
     -H "Authorization: Bearer $XELIBRARY_TOKEN" -H 'Content-Type: application/json' \
     -d '{"dry_run": true}' \
     https://192.168.0.101:9443/api/v1/tables/<tid>/maintenance/rebuild-indexes
   ```
   关注 `consistent` 与 `before` 计数。
3. 真实执行（`xelibrary:admin` + 表写权限）：
   ```bash
   ... -d '{"dry_run": false}' ...
   ```
   返回 `rebuilt` 行数、`before`/`after` 计数。
4. 验证：
   - 再次 dry-run，`consistent` 应为 `true` 且 `mapping_rows == data_rows`；
   - 抽查 `/api/v1/search/text` 与 CJK 查询；
   - 按需记录审计事件 `table.indexes_rebuilt`。

## 行为与边界

- 重建在**单个表事务**内完成：先删除三张派生索引，再按 `data` 当前有效行重新写入；
  任何失败/中断都会回滚到重建前的索引，不会留下半删除状态。
- 重建会删除旧的 `__xl_fts_map` 并重新记录 rowid 映射，同时清除
  `__xl_stats('fts_map_backfilled')` 标记（下一次写入按需重新回填）。
- 不修改行数据、revision 或变更日志；不触碰附件对象与向量索引。
- 大表（约 40 万行）重建为秒级到十秒级，建议低峰执行；执行期间该表写操作短暂排队。
- 失败时返回 typed `APIError`（409/500）并记录服务端日志，不会静默丢弃索引。
