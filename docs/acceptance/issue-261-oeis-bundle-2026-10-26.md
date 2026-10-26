# Issue #261 — OEIS 离线数据 bundle 构建（第一增量）

## 背景

本 Issue 要求把 `oeis.org` 数据引入 XeLibrary 知识表。生产导入不应在请求时直接依赖外网，
因此第一增量先实现**可复现、可校验的离线 bundle 构建器**；API 导入器与生产验收在后续增量。

## 数据源与格式（实测）

2026-10-26 从 VM111 实际获取 `https://oeis.org/stripped.gz` 文件头（HTTP 200）确认格式：

```text
# OEIS Sequence Data (http://oeis.org/stripped.gz)
# Last Modified: October 25 04:28 UTC 2026
# Use of this content is governed by the OEIS End-User License.
A000001 ,0,1,1,1,2,1,...
```

即每行一个序列：`Axxxxxx` + 逗号分隔的十进制项字符串；行首 `#` 为注释/元数据。

## 实现

`scripts/build_oeis_bundle.py`：

- 自动识别 gzip（magic）或纯文本输入，支持 `--input -` stdin；
- 流式解析：保留 A-number 与项字符串的精确内容（不重新格式化数字）；
- 校验：行格式、A-number、项字符集、长度上限、**重复/乱序 A-number 拒绝**；
- 输出 JSONL bundle（每行 `{"a_number","terms"}`）；
- 写 `<output>.manifest.json`：`format/source/snapshot/count/term_count/bytes/sha256/created_at/limit`；
  snapshot 默认取文件头 `# Last Modified`；
- 同目录临时文件 + `fsync` + `os.replace` 原子发布；**拒绝覆盖已存在 bundle/manifest**，避免快照被静默替换；
- `--limit` 便于 smoke/测试，`--source`/`--snapshot` 可覆盖元数据。

## 测试

`tests/test_oeis_bundle.py`（6 项）：精确解析与 snapshot、非法/重复/乱序输入拒绝、bundle+manifest
原子落盘与 sha256/计数一致、gzip + limit、拒绝覆盖、空项集合。

## 未完成（Issue #261 保持开放）

- 通过既有 tables/rows API 的幂等导入器（checkpoint/续传/401 保留进度/`--audit`）；
- bundle 存储位置/保留策略与操作系统磁盘预算；
- 真实 OEIS 数据导入 XeLibrary 的生产验收（不得用合成数据替代）；
- 名称/描述等附加字段可再引入 `names.gz` 作为第二 bundle。

本增量只证明离线数据构建与校验，不声称已导入任何生产知识库。
