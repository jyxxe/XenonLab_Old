# XeLibrary 产品化增量验收 — Issue #72（2026-10-01）

本记录覆盖集成到 `feature/platform-v1` 后的本地实现与合成数据验收。浏览器夹具使用合成 API；生产发布后的只读检查单独列在文末。

## 范围

- 集成分支：`feature/platform-v1`；XeLibrary 初始交付来自 `opencode/issue-72-xelibrary`，本轮 coordinator 修复在同一分支完成。
- 交付：
  - `apps/desktop-web/src/library/`：`LibraryApp`、`Sidebar`（路径树/最近/收藏）、`TableView`、`Inspector`、`SearchView`、`QueryConsole`、`TheoremView`、`NewTableDialog`、`hooks.ts`、`logic.js`、`library.css`
  - `apps/desktop-web/src/Knowledge.tsx` 改为渲染 `LibraryApp`（保留原导出，桌面 shell 与 library origin 页面不变）
  - `design-system/primitives.tsx`：`Checkbox` 的 label 变为可选（无标签复选框）
  - 测试：`tests/library-logic.test.mjs`、`tests/browser-acceptance-library.test.mjs`、`tests/library-fixture.*`
- 未改动：任何后端代码（本轮没有后端改动），OpenAPI 导出无 diff。

## 对照 Issue 的能力

| Issue 要求 | 状态 | 说明 |
|---|---|---|
| §2 三栏布局（Sidebar / Content / Inspector） | 已实现 | 路径树、最近、收藏、四个视图切换；Inspector 可折叠 |
| §2 路径树 expand/collapse、当前高亮、lazy load、loading/empty/denied | 已实现 | `/tables` cursor 分页 + “加载更多”；首次展开包含表的路径 |
| §3 Table View：sticky header、列宽调整、排序、筛选、分页、行选择、多选、键盘、右键菜单、附件/ revision/删除标识、loading/empty/error | 已实现 | 客户端排序/筛选只作用于已加载行并在 UI 说明；服务端过滤走 Query Console；行窗口虚拟化，100 行只渲染约 25–50 个 DOM 行 |
| §4 Row Inspector：Overview / Fields / Attachments / History / Permissions / Index | 已实现 | 历史包含 diff 与“恢复会创建新 revision”提示；附件复用现有上传/续传/下载/解绑实现 |
| §5 统一搜索 Fast / Quality / Structured | 已实现 | Fast=semantic（D100M）+ 关键词 FTS 开关；Quality 明确显示不可用（后端 503 QWEN_UNAVAILABLE）；Structured 跳转 Query Console |
| §6 Search Filter | 部分 | 只提供后端真实支持的 path glob 与“字段 = 值”精确匹配；未伪造 tags/has-attachment 等过滤器 |
| §7 Search Result | 已实现 | title/snippet、path、table、row id、score、匹配类型；点击直接定位到 table+row |
| §8 搜索完整性可见 | 已实现 | `partial`、`missing_embeddings`、`approximate`、`sqlite_exact_bootstrap` 全部显式告警 |
| §9 Index Status 一等 UI | 部分（后端缺口） | 仅使用真实 `GET /api/v1/jobs?table_id=` 任务状态映射 Not Indexed/Queued/Indexing/Ready/Failed；平台没有每表 chunk/pending/indexed_revision API，界面明确说明不显示推测进度 |
| §10 保存后的索引状态 | 部分 | 保存提示写明“当前平台没有自动 indexing worker，需要手动重建索引”；未伪造自动完成 |
| §11 Query Console | 已实现 | Run/校验/复制、语法与执行错误、耗时、行数、计划摘要、结果表、OFFSET 分页 |
| §12 不伪造 DSL | 已实现 | “已知未实现语法”列出 EXISTS / SEARCH TEXT / HAS_ATTACHMENT 等 |
| §13 创建 Table | 已实现 | 向导（名称/路径/schema），创建后进入 Table View |
| §14 Schema Editor 风险提示 | 已实现 | Safe change 与 Potentially destructive 区分，破坏性变更需勾选确认；>10000 行由后端 413 说明 |
| §15 Table Delete UX | 已实现（诚实版） | 409 TABLE_NOT_EMPTY / TABLE_HAS_ATTACHMENTS / TABLE_HAS_VECTORS 等映射为可理解说明；不显示无法获取的表级计数，不做 cascade delete |
| §16 Attachment UX | 复用现有实现 | 16 MiB 直传 + 分片续传/重试/取消/进度、鉴权下载、解绑；拖拽上传未实现（见限制） |
| §17 ACL / Permissions | 部分 | 管理员读取真实 path/table/row ACL 并展示继承/显式覆盖；普通用户只看 scopes 推导能力，说明服务端每次复核 |
| §18 Recent / Favorites | 已实现（浏览器本地） | localStorage，按浏览器保存；跨设备持久化需要后端小 API（见后续 Issue） |
| §19 Theorems | 已实现 | 列表/搜索/详情、依赖/被依赖/evidence、真实 formal/review 状态；明确 Agent derivation / Lean verification unavailable |
| §20 OpenWebUI Integration Points | 部分 | 行右键菜单保留 “Ask AI about this row”（禁用并标注未实现）；未扩展 Agent 系统 |
| §21 Error Handling | 已实现 | `describeError` 覆盖 401/403/404/409/412/413/422/429/500/503 与业务 code；权限错误不会伪装成不存在 |
| §22 Optimistic Concurrency | 已实现 | PATCH/DELETE/restore 使用 If-Match；412 STALE_REVISION 显示冲突横幅（本地/服务端 revision + 重新加载），不自动覆盖 |
| §23/§24/§25 Empty/Loading/大规模数据 | 已实现 | 统一 EmptyState/ErrorState/Skeleton；cursor 分页、虚拟化、请求代次丢弃陈旧响应、受控窗口大小 |
| §28 测试 | 已实现（前端） | 逻辑与浏览器验收均有回归；后端 235 项回归通过 |
| §29 Browser Acceptance | 已实现 | 4 个桌面视口 + 移动端无横向溢出；Light/Dark 截图；慢 API 与 403 权限丢失状态 |

## 验证结果（实际运行）

| 命令 | 结果 |
|---|---|
| `cd apps/desktop-web && npm test` | 38 passed（含 Library 逻辑、桌面 shell、XeLibrary 与 Work 浏览器验收） |
| `cd apps/desktop-web && npm run build` | `tsc --noEmit` + Vite production build 通过 |
| `uv run pytest -q` | 235 passed；19 个现有临时目录清理警告 |
| `uv run python scripts/export_openapi.py` | 无 diff（本轮没有后端改动） |
| `git diff --check` | 通过 |
| `uv run ruff check` | 全仓库检查有 11 个未改动 `infra/` lint 问题；本轮无 Python 文件变更 |

浏览器验收覆盖：路径树与表打开、虚拟化（100 行仅渲染窗口内行）、排序、选择、Inspector（附件、保存 If-Match、字段编辑）、412 冲突横幅、过滤条件随 Fast 请求提交、从关键词 Fast 切换到 Quality 仍调用 Quality API、Query Console（成功/显式语法校验/无效查询不执行/分页/未实现语法）、定理详情陈旧响应丢弃与关联 403 显示、延迟的搜索结果请求不会把旧表行写入当前表、不同知识表切换时不显示上一表索引任务、破坏性 schema 提示、移动表、删除被阻止的解释、慢 API 加载条、403 权限丢失（不伪装为不存在）、明暗主题与多视口无横向溢出、无未捕获异常。

截图：`runtime/verification/issue-72-xelibrary/`（`xelibrary-table-dark-1440x900`、`xelibrary-table-light-1440x900`、`xelibrary-inspector-dark-1440x900`、`xelibrary-search-dark-1440x900`、`xelibrary-query-dark-1440x900`、`xelibrary-table-dark-390x844` 等）。

## 已知限制 / 未完成

- **每表索引状态 API 缺失**：无法展示 chunks/pending/failed/indexed_revision；当前只映射任务状态，普通用户甚至无法读任务（需 xelibrary:admin）。
- **自动索引流水线缺失**：编辑后不会自动排队 embedding，UI 明确提示手动重建。
- **搜索过滤器有限**：后端只支持 path glob 与行值精确匹配；tags/has-attachment/updated range/confidence 未实现，未在 UI 伪造。
- **Recents/Favorites 仅本地**：跨设备持久化需要小 API。
- **Attachment 拖拽上传**未实现（保留文件选择与分片续传）。
- **Quality / Library Agent / Lean** 未接入，相关 UI 显示不可用。Fast semantic API 已接入生产 Qdrant；登录态生产搜索本轮没有作为验收项。
- 行级右键的 “Ask AI about this row” 仅为占位（禁用）。
- 未做后端表级计数 API，删除 UX 不显示行/附件/向量数量。

## 后续 Core Issues（已作为协调器写入请求提交）

1. XeLibrary 每表 Index Status API + 自动索引流水线（outbox → worker → vector store → indexed_revision）。
2. XeLibrary Consistency Scanner + 附件对象垃圾回收。
3. Large Table Lifecycle（Archive / 后台 purge / 安全删除）。
4. （可选）Recent/Favorites 服务端持久化小 API。

## coordinator 回归修复

本轮修复了实测发现的虚拟窗口越界、nullable 改为必填未标记为破坏性变更、分页改写引号内 `LIMIT/OFFSET`、编辑查询后 offset 未重置、Query DSL 校验错误未显示、搜索使用旧过滤条件、Quality 模式沿用关键词 endpoint、定理详情响应竞争及关联 API 错误被吞掉、慢搜索结果写入已切换的另一张表、不同 Harness 最近/收藏共用、跨表索引任务短暂串显，以及权限说明把 scopes 描述成单独资源授权等问题。单元与浏览器回归已覆盖这些行为。
