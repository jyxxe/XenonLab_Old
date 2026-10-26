# Issue #6 — 表移动目标路径的 ACL 精确校验

## 背景

#6 复核意见（comment 225 第 3 项）指出路径写操作的目标校验使用了空 table id，导致显式表 ACL
与目标路径策略不能同时精确执行：显式表 ACL 授予写权限的用户在无路径 ACL 的目标会被误拒，
而目标路径的显式策略与表 ACL 的优先级也不清晰。

## 修复

`POST /api/v1/tables/{tid}/move` 现在执行两层检查：

1. `authorize(actor, tid, "write")`：当前表的写权限（表 ACL → 最近路径 ACL → owner/账号树继承）；
2. 新增 `path_write_allowed(actor, path)`：只沿目标路径链查找最近显式 path ACL 并判定 `write`；

因此：

- 表写权限（含显式表 ACL 授予）是必要条件；
- 目标路径若存在最近显式 path ACL，必须在该 ACL 中具备写权限；
- 目标路径链没有显式 path ACL 时，继承表写权限（不再因空 table id 落入 owner 判断而误拒）；
- admin scope 仍短路为允许。

该检查只读取 path ACL，不改变表/行 ACL 语义；`create_table` 的新建路径校验保持原行为（新表无 id）。

## 测试

`tests/test_library_move_permissions.py`（5 项）：

- 显式表写授权用户移动到无路径 ACL 的目标 → 允许；
- 目标路径显式只读 → 403；
- 目标父路径显式只读（最近祖先）→ 403；
- 目标路径显式写 → 允许；
- owner 在目标路径被显式拒绝 → 403。

## 关联复核项现状

- 第 1 项（relocate LIKE 未转义）：当前 API 只有单表 `move`（更新 catalogue path），没有子树
  `relocate_path`/rename 端点，故该 LIKE 缺陷在当前实现中不可达；若将来加入子树移动必须按段边界匹配。
- 第 2 项（ACL 重键）：表 ACL 以 table id 为键，单表移动不需要重键；path ACL 仍绑定原路径语义。
- 第 4 项（分页 ACL 过滤）：现有列表/排序分页在 ACL 过滤后生成 cursor（#70 请求级 ACL 快照），
  已有回归覆盖。
- 第 5 项（子树多文件事务）：当前没有子树写操作；若实现需结合 #81 的 journal/恢复设计。

#5/#6 仍保持开放：路径命名空间/子树操作等计划范围未整体实现；本增量只关闭复核中的目标 ACL 缺陷。
