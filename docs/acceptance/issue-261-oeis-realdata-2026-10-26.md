# Issue #261 — 真实 OEIS 数据构建、导入冒烟与 429 处理

## 真实数据构建（VM111，临时目录外）

- 下载官方 `https://oeis.org/stripped.gz`：HTTP 200，33,788,344 字节，
  sha256 `ed9e819469e08de04318f99f8a38ea9bc9327071ddb7d8eb7802ec2bab6ce181`
  （Last Modified 2026-10-26 05:00 GMT）。
- 用 `scripts/build_oeis_bundle.py` 构建：
  - 序列 **400,709** 条，term **17,826,812** 个，bundle **91,814,866** 字节，耗时约 7 秒；
  - bundle sha256 `e44fbcc43e13a731af3a0ea23cc8ac3e038bbb50316da8966068bd9a5e1d4c0c`，
    snapshot `October 26 03:43 UTC 2026`；
  - 文件位于 `runtime/oeis/source/stripped-20261026.gz` 与
    `runtime/oeis/bundles/oeis-20261026.jsonl(.manifest.json)`（不进入 Git）。
- 该步骤验证构建器在真实全量数据上的性能、条数与格式兼容性；不是生产导入验收。

## 端到端导入冒烟（临时 XeLibrary 实例，非生产）

- 在临时数据目录启动真实 `xelibrary` 服务，使用真实全量 bundle 导入前 100 行：
  - `import_oeis.py --limit 100` 退出 0，checkpoint `processed=100`、`total=400709`；
  - 通过 API 读取该表 100 行，`/OEIS` 与名称正确；
  - `--audit` 对已扫描行 `mismatched=0, extra=0`，因只导入 100 行而报告其余 missing（符合预期）。
- 该冒烟验证真实数据 → bundle → API 导入 → audit 的链路，使用的仍是临时实例。

## 发现的限流问题与处理

- 一次尝试导入 200 行时，`interactive` 策略（120 请求/60 秒）在第 121 次写入后返回 429，
  导入器当时按设计停止。全量 400k 行若逐行导入会持续触发限流。
- 已为 `APIClient` 增加 **429 重试**：尊重 `Retry-After`（上限 60 秒，缺失时指数退避），
  `--retry-429` 默认 5 次；仍不对 401/403 重试，重试使用 `Idempotency-Key` 保证写安全。
- 新增本地 HTTP 测试：首个写入返回 429 + `Retry-After: 0`，重试后成功，记录等待 0 秒。

## 仍未完成（Issue #261 保持开放）

- 全量生产导入（400,709 行）尚未执行：逐行 API 导入在 120 请求/分钟下至少约 55 分钟，
  需在维护窗口执行并确认客户端重试/超时/断点续传；尚未完成生产导入验收。
- 批量写入（`rows:bulkInsert`）可显著减少请求数，但需处理异步作业与幂等语义，后续增量评估。
- bundle 存储/磁盘预算（当前约 92 MB bundle + 34 MB 源文件）与保留策略。
- `names.gz` 名称/描述第二 bundle；按 A-number 建索引可降低审计成本。
