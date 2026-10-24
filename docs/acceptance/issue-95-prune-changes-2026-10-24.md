# Issue #95 — `__xl_changes` 保留窗口（首个增量）

- 新增 `POST /api/v1/tables/{tid}/maintenance/prune-changes`（scope `xelibrary:admin`，并校验表写权限）：
  - `dry_run` 默认 true；`older_than_days` 1–3650；`keep_recent` 默认 50000；`limit` 默认 5000（1–100000）。
  - 候选 = 早于 cutoff、超出 keep_recent 窗口、且**不是该行最新 revision** 的变更；按 sequence 有序分批。
  - 活动 bulk 作业 / 活动 index 作业 / 正在删除的表返回 409；真实删除写入 audit（表、dry_run、候选数、删除数）。
- 测试：`tests/test_library_prune_changes.py`（admin 门禁、dry-run 不删除、真实执行仅删 1 条且保留最新 revision、非法参数 422）。
- 附件对象 GC、增长指标、系统面板接线仍为后续增量；设计见 `docs/design/xelibrary-data-lifecycle.md`。
