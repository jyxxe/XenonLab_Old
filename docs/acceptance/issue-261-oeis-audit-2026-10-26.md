# Issue #261 — OEIS 导入审计模式（第三增量）

在幂等导入器上增加只读 `--audit`：通过分页读取目标表，与已校验 bundle 逐条比对，报告缺失、
项不一致与多余/重复 A-number。仍不写数据、不导入生产。

## 行为

```bash
uv run python scripts/import_oeis.py \
  --bundle runtime/oeis/bundles/<snapshot>.jsonl \
  --base-url "$XELIBRARY_URL" \
  --path /OEIS --name "OEIS 数据" \
  --checkpoint runtime/oeis/import.checkpoint.json \
  --audit [--audit-limit N]
```

- 先校验 bundle manifest（sha256/字节/条数）；
- 读取 checkpoint 并要求已有 `table_id`（审计不创建表）；
- 以 `/tables/{id}/rows?limit=1000&cursor=...` 分页扫描，收集 `a_number → terms`；
- 报告：`scanned/expected/seen/missing_count/mismatched_count/extra_count`，并附最多 100 条明细；
  `extra` 包含表中不存在于 bundle 的 A-number 与重复出现的 A-number；
- `--audit-limit N` 可有界停止（此时 `complete=false`，缺失统计不代表全表结论）；
- 结果 `ok = complete && 无缺失 && 无不一致 && 无多余`；有差异时退出码 1，便于脚本化验收；
- 401/403/网络错误会中止并报错，不做自动重试。

## 测试

`tests/test_oeis_import.py` 新增 3 项（合计 10 项）：干净 bundle、缺失/不一致/多余/重复检测、
`--audit-limit` 部分扫描时 `complete=false`。使用分页 fake client，不依赖生产。

## 未完成（Issue #261 保持开放）

1. 真实 OEIS 数据导入生产知识表并运行真实 `--audit`（不得用合成数据替代）；
2. bundle 存储位置/保留策略与操作系统磁盘预算；
3. `names.gz` 名称/描述第二 bundle；
4. 按 A-number 建索引可把审计从全表扫描降为定点校验（可作为后续性能增量）。
