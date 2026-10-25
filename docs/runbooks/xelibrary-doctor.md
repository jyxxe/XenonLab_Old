# XeLibrary 一致性扫描（只读）

对应 Issue #81 第一增量：检测 catalogue、表文件、附件引用、对象存储与上传状态之间的不一致。
扫描**只读**，不自动修复或删除任何数据。

## 调用

以持有 `xelibrary:admin` 的账户调用桌面 API（经网关/服务身份）：

```bash
GET /api/v1/maintenance/consistency
Authorization: Bearer <token>
```

响应为 `{"data": {...}, "meta": {...}}`；`data.ok=true` 表示无 critical 问题。

## 判读

1. 先看 `data.ok` 与 `critical_issues`；`warning` 级多为启动恢复或维护操作可处理的状态。
2. `issues[].kind` 见 `docs/acceptance/issue-81-consistency-scan-2026-10-25.md` 对照表。
3. `issues_truncated=true` 说明问题超过 200 项，应缩小范围或先处理批量子集，而不是假定只有报告中的问题。
4. `object_store.orphan_check_complete=false` 时不得按 `unreferenced` 判断孤儿对象，
   需先修复不可读表文件。
5. 扫描结果只是当前读取时刻的观测；孤儿对象在 attachment GC 前后变化属正常。

## 安全边界

- 扫描不执行写语句、不获取写锁、不修改 catalogue/表文件/对象/上传状态。
- 报告可能包含内部 table id、路径与对象 digest，只应提供给管理员；不要粘贴到公开渠道。
- 扫描不能替代备份。发现 critical 不一致时，先停止相关写入、保留现场并核对
  `docs/runbooks/xelibrary-backup-restore.md` 的备份/恢复流程，再由维护者决定恢复或修复。
- 尚未提供修复端点；不要手工编辑 `.xl`/catalogue 文件。缺失表文件时优先从备份恢复。

## 已知自动恢复

进程重启时 `recover_table_deletions()` 会收尾 `.<tid>.xl.deleting` 墓碑并清除 catalogue 内表的
`deleting` 标记；因此这两类 warning 通常在下次服务重启后消失。若持续存在，说明恢复自身失败，
应检查服务日志与文件权限。
