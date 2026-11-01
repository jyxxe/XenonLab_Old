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

## LAN HTTPS 与导入凭据

- 生产 XeLibrary 只通过 LAN 边缘 `https://192.168.0.101:9443` 暴露（内部 8100 端口对 VM111 防火墙关闭），
  因此导入器新增 `--ca-file` / `XELIBRARY_CA_FILE`，用指定 CA 验证 TLS（不关闭校验）。
- 生产导入使用 Keycloak 专用 service account client（`oeis-import-agent`，realm role `platform-admin`，
  audience mapper 加入 `xeonolab`，token lifespan 7200s）；client secret 仅保存于 VM111
  `runtime/oeis/keycloak-client.json`（0600），不进入 Git/日志。
- `/公开数据` 路径 ACL 显式授予 `*: read`，导入表创建后普通登录用户可只读访问；写权限仍仅限服务账户/admin。

## 并行分片导入

逐行导入在 120 请求/分钟/身份下需约 55 小时。导入器新增 `--shard-count/--shard-index`：
按 bundle 1-based 位置把条目分配给 N 个独立导入器（各自凭据与 checkpoint，互不写同一行），
checkpoint 记录全局进度；`--audit` 仍对整表复核。计划用多个 Keycloak service account 并行，
以缩短生产导入时间；每个 shard 的 token 由外层 runner 定期刷新，checkpoint 续传保证幂等。

## 原子批量导入端点与批处理（2026-11-02）

- 逐行 commit 在 VM104 WAL 上约 1 秒/行（8 分片并发总吞吐约 2–3 行/秒，全量约 40 小时），
  证明仅靠分片/429 重试不足以在合理时间完成导入。
- 新增（admin-only）`POST /api/v1/tables/{tid}/rows:importBatch`：单个 table 事务内插入
  ≤1000 行，逐行做 schema 校验、行/表 ACL、change log、FTS，使用稳定 `__xl_idempotency` key；
  任一 key 冲突则整批回滚（409 `IDEMPOTENCY_CONFLICT`），重放同批返回 `existing` 而不重复插入；
  写 `rows.import_batch` 审计。无新依赖。
- 导入器新增 `--batch-size N`（1..1000，batch 模式不支持 shard 并行）：每 N 行一次请求、
  批后写 checkpoint；批内原子、批间幂等，可在中止后续传。
- 测试：batch 端点 3 项（原子/幂等/冲突回滚/权限与上限），导入器 batch 模式 2 项（分批+续传、
  与 shard 互斥）；OpenAPI 重新导出。
