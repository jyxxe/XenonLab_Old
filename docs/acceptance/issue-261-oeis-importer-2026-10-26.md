# Issue #261 — OEIS bundle 幂等导入器（第二增量）

在第一增量（离线 bundle 构建）之上，新增 `scripts/import_oeis.py`：通过既有 XeLibrary tables/rows
API 把 bundle 幂等导入知识表。本增量只在本地测试与 fake transport 上验证，**不导入生产数据**。

## 安全属性

- 导入前校验 bundle 的 sha256、字节数与序列条数必须匹配 manifest；不一致直接拒绝；
- checkpoint 绑定 bundle sha256 与目标 `{path,name}`，不同 bundle/目标拒绝续传；
- 每行使用稳定 `Idempotency-Key: oeis:<A-number>`，中断后续传不会重复插入；
- 每成功一行即原子写 checkpoint（同目录临时文件 + fsync + `os.replace`，权限 0600）；
- 401/403/网络错误立即停止并保留已写入进度，不自动重试；认证信息只从环境
  `XELIBRARY_TOKEN`/`XELIBRARY_TOKEN_FILE` 读取，不进入仓库；
- 建表先用列表查找，创建返回 409 时只做一次显式查找，不做盲目重试。

## 行为

- 目标列：`a_number(string, 非空)`、`terms(text)`、`term_count(int64)`、`snapshot(string)`；
- `--limit N` 单次导入上限（可反复续传）；`--checkpoint` 必填，建议放在 `runtime/oeis/`；
- 成功输出 JSON：`{ok, table_id, processed, total, snapshot}`；失败输出 `{ok:false, error}` 并返回非零。

## 测试

`tests/test_oeis_import.py`（7 项）：bundle 篡改拒绝、行写入与 checkpoint/0600、续传 + limit、
checkpoint 绑定 bundle/目标、失败行保留进度、`row_values`/`iter_entries`、本地 HTTP 服务验证
APIClient 建表与 Idempotency-Key 透传。

## 未完成（Issue #261 保持开放）

- `--audit` 全表复核（需要按 A-number 建索引或受控分页）；
- bundle 存储位置/保留策略与操作系统磁盘预算；
- 真实 OEIS 数据导入生产知识表并用有/无权限主体验收（不得用合成数据替代）；
- 名称/描述字段（`names.gz`）第二 bundle。
