# Issue #221 — 定理待审队列与晋升/拒绝操作（2026-10-23）

前端增量，复用既有定理 API，无新增后端接口或迁移。

- 「待审队列」按钮按 `GET /api/v1/theorems?review_status=proposed&limit=50` 拉取**无关键词**的
  proposed 列表，支持 `meta.next_cursor` 的「加载更多待审定理」，并显示当前已加载数量（+ 表示还有）。
- 定理详情在 `selected.review_status === 'proposed'` 且账户拥有 `xelibrary:theorem:promote` 时显示
  晋升/拒绝：先用 `GET /theorems/{id}` 取当前 `ETag`，再以 `If-Match` 调用既有
  `POST /theorems/{id}/promote|reject`；成功后刷新待审队列与详情并给出「已晋升/已拒绝」状态。
  412/409 会显示可读提示并刷新详情；无权限时按钮不显示。
- 界面继续分别呈现 `review_status` 与 `formal_status`，晋升不宣称 Lean/形式证明已验证。
- 测试：browser acceptance 扩展（待审队列请求、proposed 列表、两枚操作按钮、POST 携带当前 ETag、
  成功状态）；完整浏览器回归与前端单测。
