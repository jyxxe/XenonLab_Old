# 实施状态 — 2026-10-01

## 已验证

- 10 个独立可启动 HTTP 服务入口，统一 request ID/error envelope/OpenAPI/metrics。
- OIDC RSA 验签、有效期、issuer、audience 校验；生产模式禁用开发 token。
- XeLibrary `.xl` 格式 1.0：元数据、schema、真实行数据、事务变更日志、FTS5、附件引用。
  bootstrap 行用户字段存储为 values_json，尚未采用每用户列一个 SQLite 物理列。
- 表创建/列表/读取/移动；行创建/更新/读取/分页/删除/历史；schema 迁移；创建行幂等键。
- ACL 以 row → table → 最近祖先 path 为优先级；不存在 ACL 时仅 owner 访问；admin scope 可管理。
- 结构化 DSL：FROM、WHERE、ORDER BY、LIMIT、OFFSET、嵌套布尔、比较、IN/NOT IN、
  CONTAINS/CONTAINS ANY/ALL、STARTS_WITH/ENDS_WITH/MATCHES（LIKE 通配，非 regex）、NULL、
  值函数 LOWER/UPPER/LEN/COALESCE/DATE/YEAR/MONTH、多语言字段。
  unsupported 操作拒绝，不执行原始 SQL、eval、脚本或命令。
- SHA-256 附件存储、大小限制、文件名清理、基础 PDF/PNG sniff、下载权限与删除行检查。
- D100M 真实权重 CPU 加载；256D/L2、128 token 上限、模型+tokenizer revision hash。
- 真实 HTTP embedding → tokenizer 分块（[PATH]/[TABLE]/[COLUMN]/[TEXT] 上下文，目标 108 token、
  overlap 16、hard limit 126，chunker_version 参与向量匹配）→ 本地精确向量索引 → semantic search →
  删除过滤已通过 smoke。
- 精确搜索过滤当前 ACL/行版本/删除状态，索引缺失返回 partial/missing_embeddings。
- Deploy release/人工审批/原子单租约/holder/epoch/renew/release/审计。
  即使 lease 过期，未确认结束的 deployment 仍阻止获取新 lease；OpenCode 无法部署。
- Forgejo webhook HMAC 签名、delivery 去重与冲突检测；Issue 文本仅保存为数据。
- Tool Gateway 只提供 search/query/get，透传验证的 bearer token，不代换超级管理员身份。
- LLM Gateway 固定 deepseek-flash、服务端 provider credential、非流式转发。
- React/TypeScript 桌面、受控 app registry、个人布局、窗口管理、iframe origin/source 验证。
- PVE 已上线当前实现：VM 101/104/106/107/109/105/111；早期验证 VM 未删除。xenon.lan LAN DNS/TLS 与 `xenon.jyxxe.com` Cloudflare Tunnel 均已配置，并保留直接 IP 入口。

## 尚未完成

| 计划阶段 | 当前状态 / 后续必要工作 |
|---|---|
| 0 基础设施 | PVE 可达并已盘点；已配置部分端口边界、LAN DNS/TLS、Keycloak OIDC；VM 布局调整、完整安全域、Forgejo、监控仍待实施 |
| 1 skeleton | 已实现独立入口与 OpenAPI；CI 工作流已提供；生产 readiness 仍需扩展依赖探针 |
| 2 数据核心 | bootstrap 已可用；PostgreSQL catalogue、路径对象、完整 DSL、批量作业、import/restore/purge、分片待实施 |
| 3 D100M | 实际推理与 Qdrant fast_text 生产连接已验证；后台自动索引队列、优先级调度和领域检索评测待实施 |
| 4 Qwen | 尚无模型 worker、vision、quality rerank/cache；质量请求返回 unavailable |
| 5 UI/Tools | 桌面和只读网关已实现；OIDC 浏览器 PKCE 登录已接入；OpenWebUI 原生用户 token exchange 尚未接入 |
| 6 Library Agent | 只有 skeleton；推理、证据、proposal、Lean sandbox 未实现 |
| 7 DevAgent | 已实现签名 webhook 与队列同步、规则 triage、Issue links、持久任务队列；LLM triage、自动评论/标签联动和多副本共享队列库仍待实施 |
| 8 Deploy | 租约已验证；固定 executor、健康检查与激活失败回滚已实现；完整状态机、SSE、跨版本迁移尚未实现 |
| 9 HF-cache | 已实现 SHA 固定 ensure、HF Hub 下载、manifest/hash、pin/lease/GC、异步预取 job、结构化候选审核、root-fed pool metrics fail-closed reader，以及 `hfcache_usage_bytes`、下载/hit/miss/eviction counters 和结构化事件日志；生产 PVE collector/wiring 与物理遥测证据、自动 signal discovery 尚未接入 |

## 运行与恢复限制

Catalogue 为 SQLite bootstrap，生产 PostgreSQL 迁移另见 ADR 0001。Catalogue 与每个 `.xl`
文件之间没有分布式原子事务，进程崩溃可能产生未登记的孤立 `.xl` 文件；生产发布前须实现
manifest/outbox 与一致性扫描。索引作业状态已在 catalogue 持久化（待发布）；重启中断明确标为失败，自动恢复仍未实现；向量可重新生成。
分块器版本进入向量匹配键（chunker_version），版本升级后旧向量不再命中，需要重新索引。
表导出用 SQLite backup API 生成一致性快照。独立备份位置尚未提供，mirror 不被视为备份。

所有外部 secrets 只从环境读取；用户提供的 PVE/LLM/password 不写入仓库。
已有生产 LAN 上线、未删除 VM、未创建远端 PR。完整 V1 还不能视为验收完成。详见 runbooks/deployment.md。

## 验证记录

当前源码基线：189 项后端测试、11 项桌面测试与 1 项 Work 分页浏览器夹具通过；React/TypeScript production build、OpenAPI 导出和 diff 检查通过。
Issue #50 的末页与游标循环保护已随 release `c35b7e07fc14d1a32459f9108f8ba9285f37dd5d` 上线；公网静态资源哈希匹配构建，Work API live cursor smoke 通过。登录态 Chrome Work 页面现场操作仍待验收。
此前 10 个服务均以独立进程通过 HTTP live/version/OpenAPI 检查，D100M 实际模型端到端 smoke 通过。
本机缺少 Docker Compose 插件，未运行原 Compose 拓扑；线上采用 systemd，Keycloak 使用 Docker + 本地 PostgreSQL。

## 完整 Plan 推进

已补齐单行历史 revision 读取、restore 创建新 revision 与管理员 purge。
restore 校验当前 schema 与 If-Match，保留历史；purge 删除行历史、索引和附件引用，
保留独立审计，内容寻址对象的实际垃圾回收仍待实现。18 项测试通过。

优先级更新：Issues/Forgejo、权限页面和 Harness 协作优先。
已实现身份 scopes 展示、管理员 ACL 读取/编辑页面、管理员审计查询 API；19 项测试与前端构建通过。
公网主域已确定 xenon.jyxxe.com；Cloudflare 原 no-tunnel 例外已被用户撤销。

DevAgent 已实现持久化 work queue create/list/claim/renew/release：scope dev:read/write、
SQLite 原子领取与续租、TTL 与 epoch fencing、完成状态和审计。并发、重启、权限、失租拒绝和过期重领测试通过。
续租为本次 Issue #32 的新增实现，尚待发布；既有 claim/release 与 TaskCenter 已线上接入，30 个真实 Issues 已入队。
signed webhook 自动同步和规则 triage 已上线；LLM triage、自动评论/标签联动及多副本共享队列库仍未实现，不能视为完整 DevAgent 完成。

Issue links API 已实现 related/blocked/blocks/supersedes/duplicate 关系持久化，
重复关系拒绝自环和环路，跨仓库分离，生产固定 repository allowlist；22 项测试通过。
工作队列页面已构建，独立 OAuth 开发身份已创建（无 production deploy scope）。

DevAgent signed issues webhook 与 work queue 原子同步已实现并上线，Forgejo hook 已配置。
验证 delivery 去重、按 updated_at 防乱序覆盖、关闭撤销 claim、重开入队、repository allowlist；
非 issues 类型仅记录原始事件。旧 events.sqlite 事件去重历史复制到 work.sqlite，原文件保留。
不执行 Issue 文本；24 项测试覆盖签名、事务与迁移。自动 triage 仍待实现。

规则化 triage 端点已由 OpenCode harness 在分支 `opencode/issue-17` 实现：
`POST /api/v1/issues/{issue_id}/triage` 返回 labels/priority/duplicates/related/recommended_agent
建议，只读 work.sqlite 与已登记 Issue 标题，不调用 LLM、不写 Forgejo、不执行 Issue 文本。
新增 5 项测试覆盖规则、重复/关联、链接图与仓库隔离、权限与 404/422、注入文本仅作数据处理；
全套 31 项测试与 Ruff 通过。待评审与发布；LLM 语义 triage、mark-duplicate 与自动评论/标签联动仍未实现。

用户 UI 优先级更新：完整 WebUI/设置、可视化知识管理器、Issues CSS、账号树、LaunchPad、持续登录。
Issues ROOT_URL 子路径已修复；桌面功能已迁入子窗口并新增 LaunchPad（待发布）。
知识管理器提供表筛选/创建、schema 驱动记录表格/编辑、带 If-Match 软删除与附件界面（上传、分片续传、关联/解绑及鉴权下载）；导入导出/历史界面仍待完成。
账号树与 API key 生命周期、持续登录刷新仍未实现。
Issue 同时间戳冲突冻结与主动 source reconcile 已实现且有测试，待配置和发布；25 项测试通过。

### 2026-09-30 WebUI priority update

- Deployed 0688c488: windowed knowledge manager, schema-driven record editing, table creation/filtering, soft delete, LaunchPad, settings/work windows. The current feature branch adds attachment upload/link/detach/authenticated download UI with multipart retry; history/import-export UI remain incomplete and attachment UI still needs production deployment verification.
- Forgejo ROOT_URL now uses the public `/dev/` prefix; public index/theme CSS verified as CSS responses. IP entry supports the same prefixed assets.
- Persistent desktop browser sessions exchange PKCE codes server-side, keep refresh credentials in a private server SQLite database, and use secure HttpOnly same-site cookies. Server restart/page reload restore the session; access tokens remain short-lived and refresh automatically. Logout deletes this browser session. Idle offline timeout is configured to Keycloak's signed-integer maximum, with no maximum offline session lifespan. Browser cookie storage limits and deliberate revocation still apply; this is continuous renewal rather than literally immortal credentials. [Keycloak offline-session semantics](https://www.keycloak.org/docs/latest/server_admin/#_offline-access).
- Tests cover restart restoration, refresh after expiry, temporary provider failure, origin/redirect rejection, and logout. Account tree, inherited account permissions, password reset/revocation, and API key management are not yet implemented; existing resource ACL settings are not an account administration page.
- DevAgent reconciliation source now has a fixed Forgejo endpoint and verified LAN CA in deployment configuration.

### Existing-user login regression and multi-Harness verification

Keycloak rejected the imported administrator's offline code exchange with
`not_allowed: Offline tokens not allowed for the user or client`. New users had
inherited the default offline role, so the earlier smoke check missed this.
The persistent-login migration now adds only `offline_access` to existing human
users who lack it, preserving passwords and all other roles. Bootstrap admin
also declares the role. The real legacy-user regression removes default roles,
applies this migration, then verifies code exchange, session restore and logout.
Public desktop verification now uses the configured canonical IP issuer instead
of assuming the `/auth/` proxy is the issuer. Public Tunnel returned 1033/530 on
2026-09-30 during verification, independently of the resolved role issue.

GPT-6 Luna added and executed `scripts/verify_multi_harness.py`: independent
Codex/OpenCode identities; exclusive claims; non-holder and stale epoch denial;
OpenCode release creation/production lease denied; isolated Issue closed and
work completed. This checks the existing API boundaries, not arbitrary Harness
behavior or every plan requirement. OpenCode credentials/instructions are in
private, Git-ignored `Agent.md`; never publish this file.

Failure-injection 验证（OpenCode harness，分支 `opencode/issue-31`，`scripts/verify_failure_injection.py`）：
本地进程级 SIGKILL 注入全部通过——① COMMIT 前杀进程，未提交事务不可见且 integrity_check 通过；
② xelibrary 四个并发写入线程中 SIGKILL，重启后 catalogue/.xl integrity 通过、行数据完整可读、可继续写入；
③ dev-agent claim fence（holder/epoch 持久、他人 claim 409、release 仍要求当前 epoch）与 webhook delivery
去重跨 SIGKILL 保持。脚本 3/3 次稳定复跑，已加入 CI（push/PR）作为回归保护；过期 lease + 未结束
deployment 冻结场景已有单测覆盖（test_deploy）。待评审与发布。

### Account tree / API key implementation

Managed account APIs, interactive settings tree, partial/full inherited scopes,
password reset, one-time API key creation/list/revoke, and account audit now exist.
Common API authentication consults a fixed TLS authority for live permissions;
unavailable authority fails closed. The browser session also checks account
state before returning a cached token and renews its secure cookie during use.
Tests cover sibling/self-policy isolation, nested inheritance/shrink/disable,
key attenuation and revocation, password fencing/provider failure, and a remote
XeLibrary service losing permissions immediately. Thirty tests and frontend
build passed. Live deployment/provider verification is required before claiming
production acceptance. Preferences/app registry settings, knowledge attachments
and history UI, and the broader plan remain incomplete.

Real account verification found same-second JWT issuance rejected immediately
after password reset. Credential reset now uses a random version in an admin-only
Keycloak attribute/desktop token mapper, rather than an issue-time cutoff for
managed JWTs. The regression verifies a fresh version with unchanged issued-at
time. Opaque keys still use explicit revocation. The first disposable-account
verification failed at fresh login and was cleaned up; rerun after release.

Account inheritance now also carries verified ancestor principals into XeLibrary
ownership/ACL checks; a nearer child ACL entry overrides inherited parent entries,
and API scopes continue to cap writes. Tests verify actual parent-owned resource
access and explicit child denial, rather than only comparing scope arrays.
A second real password-reset check found the Keycloak attribute-only PUT cleared
profile fields and triggered UPDATE_PROFILE. The provider now preserves the full
existing profile when changing credential version; a regression asserts names,
email, existing attributes, password reset and logout ordering. Thirty-two tests
passed; the disposable real-account verification will be rerun after activation.


Desktop settings expansion (2026-09-30): app registry now exposes revision-checked update/delete, administrator audit records, canonical origins and per-app sandbox controls. Per-account theme, accent, wallpaper and IANA timezone preferences persist across process restarts. The settings window contains app management and desktop preferences; restored windows reattach resize observation. Validation: 34 tests passed, TypeScript/Vite production build passed, OpenAPI regenerated. Browser visual acceptance and the full plan remain incomplete.


Public Tunnel recovery (2026-09-30): VM 105 logs confirmed simultaneous edge connection loss and TCP/TLS timeouts despite an active systemd process; the origin remained HTTP 200. A second connector on VM 101 uses the same remotely managed tunnel and fixed origin, without DNS changes. Both expose loopback-only readiness on 20241 and have a 30-second readiness timer that restarts only after 90 seconds of zero connections, with a 300-second cooldown. Credentials remain root-only token files and were transferred over SSH without terminal output. This reduces connector outages; both VMs share the PVE host and network uplink, so it does not eliminate common network failures.
Verified after origin allowlist update: public desktop, health, auth configuration, Forgejo login, Issues and realm discovery all HTTP 200 through the external proxy; real public PKCE/offline session login, restore and logout passed. 35 tests passed. Both readiness endpoints showed healthy connections and both timers were active.


Luna implementation integration (2026-09-30): reviewed and integrated knowledge manager and work pagination through PR #51/#52. Knowledge supports configurable schema creation/editing, move, record history/restoration and opt-in deleted rows with ACL filtering. Public apiPage retains response cursors; knowledge/work lists load subsequent pages and discard stale responses. Work rows link to their Forgejo issue. Own row writes refresh table metadata before later management operations. Coordinator integration verification: 37 pytest tests passed, Ruff passed, TypeScript/Vite build passed, OpenAPI regenerated. Browser visual acceptance remains unverified; full plan remains incomplete.
Query DSL 扩展（OpenCode harness，分支 `opencode/issue-8`）：补齐 NOT IN、CONTAINS ANY/ALL、
STARTS_WITH/ENDS_WITH/MATCHES（LIKE 通配，非 regex）、ORDER BY/ASC/DESC、OFFSET 与值函数
LOWER/UPPER/LEN/COALESCE/DATE/YEAR/MONTH。无 ORDER BY 时保持早停分页，ORDER BY 需全量匹配后排序。
新增 8 项测试（解析与拒绝、集合/文本运算符、函数、NULL 与类型语义、端点排序分页与注入拒绝）；
全套 42 项测试与 Ruff 通过。EXISTS(...)、SEARCH TEXT、HAS_ATTACHMENT()/PATH_DEPTH() 尚未实现，
待附件/路径/检索上下文接入执行计划。待评审与发布。


Reviewed PR integration (2026-09-30): Query DSL extensions include explicit builtin arity validation and null AST serialization; triage only classifies duplicate_of/related_to without losing directional dependency semantics; failure-injection CI creates its temporary parent in a clean checkout; tokenizer chunks record chunker-v2 and enforce the content/context token limits. Integrated 55 tests, Ruff, OpenAPI generation and frontend build passed; three real process SIGKILL recovery scenarios passed. Existing vector rows are preserved by additive migration but excluded until reindex under the current version. Full DSL, PostgreSQL/Qdrant and full plan acceptance remain incomplete.

WebUI usability increment (2026-09-30, issue #41, branch opencode/issue-41): a built-in 系统面板 window shows the current user's Embedding index queue (owner-filtered `GET /api/v1/jobs`, administrator scope, table/status filters, owner-scoped counts), the Harness work queue as a macOS activity ring with repository-aware Forgejo issue links, ACL-visible knowledge tables grouped by top-level path, and same-origin service readiness with latency. Work/table sample caps are surfaced instead of presenting partial counts as totals; dashboard errors clear after successful refresh. Desktop preferences persist a validated `widgets` list with settings checkboxes, and the knowledge window has an administrator-only 重建 Embedding 索引 action with job polling. Dashboard's app ID is reserved from the external registry. Validation after local integration and review fixes: 77 tests passed (one existing Starlette/httpx deprecation warning), Ruff clean, OpenAPI regeneration produced no `docs/api` diff, and TypeScript/Vite production build passed. `GET /api/v1/jobs` filters both job rows and status counts to the authenticated owner, matching the existing single-job owner check. Queue/table samples mark truncation explicitly and dashboard errors clear after a successful refresh. A local browser demo used a stub embedding HTTP endpoint and seeded jobs, so no real D100M inference or production visual acceptance is claimed. Upload/import-export UI, cross-user/global queue visibility and richer monitoring remain incomplete.


Issue #41 integration (2026-09-30): OpenCode delivery 4c413e2 reviewed and integrated with owner-scoped embedding jobs, explicit truncated-list statistics, recoverable error state and reserved dashboard app ID. Preserves attachment/ACL management. Coordinator verification: 78 tests, Ruff, regenerated OpenAPI and frontend build. Production pre-release quiesced backup and isolated restore verified, including empty object-store restore regression. Browser and release verification follow activation; full plan remains incomplete.


Resumable uploads integration: multipart sessions, owner/ACL/scope revalidation, stable attachment IDs and crash-retry completion are implemented. Backup validation accepts canonical upload lock files. Coordinator: 82 tests, Ruff, OpenAPI and frontend build passed; pre-uploads production backup and isolated restore verified. Production activation and real multipart verification passed as recorded below. Qdrant systemd/TLS/firewall templates were subsequently connected to production semantic search by Issue #11.

Production uploads: release c32fd7f completed; real PKCE test uploaded 18,900,000 bytes in 3 parts, reused a repeated part, completed idempotently, verified downloaded SHA-256 and unbound the fixture. Qdrant v1.19.1 official musl binary SHA-256 verified, service VM109 TLS/API-key checked from VM104, unauthenticated access rejected with 401, vector survived restart and probe collection removed. Issue #11 subsequently connected Qdrant to the production XeLibrary search path; its live acceptance limits are recorded below.


WebUI session resilience: timers and API renewal no longer clear the workspace on 401 or temporary failure. Temporary provider errors retain persistent session credentials, startup restores retry, and only explicit logout clears UI state. Revocation requires explicit reauthorization and forbids cached-token fallback. Coordinator validation: 82 backend tests, 4 executable frontend session-state tests, Ruff, OpenAPI and frontend build. Keycloak offline absolute lifespan remains disabled; live Chrome fault injection follows activation.


### Issue #32 工作租约续租

工作租约续租与失租停止约定已随生产 release `c32fd7f` 发布：
- `POST /api/v1/work/{id}/renew`（scope `dev:write`；body `epoch`、`ttl_seconds` 30–3600，默认 900）：
  仅当前 holder、当前 epoch 且租约未过期时延长 expires；续租不改变 epoch，写 `work.renewed` 审计。
- 过期或被 webhook 重新认领后 renew/release/complete 一律 409；持有者必须停止写入并重新 claim。
- 新增 1 项测试覆盖：续租与进程重启持久化、错误 holder、旧 epoch、过期、TTL 越界、缺少 scope、404、
  审计记录与重新认领后的 epoch fencing。
- 文档：`docs/runbooks/collaboration.md` 与 `docs/agent-workflow.md` 补充续租与失租停止约定。
- 未实现（另议）：webhook 自动更新 Forgejo assignee/评论；多副本共享具备事务锁的状态库。

监控增量（2026-09-30，issue #30 / plan §63，分支 opencode/issue-30）：共享基座新增最小 Prometheus 注册表（counter/gauge/histogram、标签转义、抓取时动态 gauge provider），`/metrics` 保留 `xeonolab_requests_total`/`xeonolab_errors_total` 名称并新增路由/状态/延迟与 `xeonolab_build_info`；XeLibrary 落地 §63 全部指标（查询计数/耗时/错误、行数、向量块、索引进度、embedding backlog、存储字节），状态快照使用带 single-flight 的 60 秒缓存，数据库读取错误省略状态样本并用 `xelibrary_snapshot_success` 明确报告，只有缺失可选 `__xl_vectors` 表计为零，storage stat 仅忽略并发消失文件；Embedding 指标区分真实队列深度（V1 恒为 0）与 `embedding_in_flight`（worker 锁占用），意外 encoder 异常仍返回 500 并以固定 `INTERNAL_ERROR` 标签记录；Deploy 落地租约活跃、执行耗时、失败与回滚计数及部署状态分布；HF-cache skeleton 仅暴露真实使用量。`infra/monitoring/` 提供 Prometheus 抓取与告警参考配置、Grafana 仪表盘（18 面板），运行手册见 `docs/runbooks/monitoring.md`。修复 reviewer 两轮问题后验证：95 项测试通过（含指标错误、缓存竞争、stat竞态及 encoder exception regression），修改文件 Ruff 检查通过、OpenAPI 重新导出、前端构建通过。未实现：OpenTelemetry、Alertmanager 路由、Qwen/HF-cache 下载类指标与系统层资源 exporter。

Issue #30 integration on `feature/platform-v1` preserves the existing request rate limits and `Retry-After` responses. Review caught and fixed a Starlette exception-path gap so uncaught HTTP 500 responses are also counted in common metrics. Coordinator validation: 109 tests, full Ruff, unchanged generated OpenAPI, 10 frontend tests/build, and monitoring YAML/JSON parsing passed. Prometheus/Grafana remain reference configuration and are not installed in production by this release.

Issue #54 ACL subject selector production update (`f5584ce`): supports account-tree subject selection and preserves grants whose subjects are not in the loaded tree. Production API/account verification passed; browser save/round-trip acceptance with an initialized account tree remains pending.

Issue #26 theorem data model and API are implemented and deployed in release `7779e286-5a72-424c-8b63-7e9769847865` (deployment `8300843b-d69a-46f1-8e94-c8ac71a3905e`, commit `67bea9f81bb450afbcb7690a00006e765a99d40f`). The API provides theorem CRUD, revisions, search, dependencies/dependents, evidence, promote and reject routes; mutations use `If-Match`, path ACLs, status-specific authorization and audit events. Create and patch cannot forge verifier/reviewer states, inaccessible dependency metadata is hidden, and verifier/candidate routes fail closed with 503 until Lean and Library Agent services exist. The platform-admin deployment role includes theorem read/propose/promote scopes. Coordinator validation: 202 tests passed; targeted Ruff, OpenAPI regeneration and diff checks passed. Production readiness checks returned 200 on all five service entry points and public desktop/auth configuration; unauthenticated theorem listing returned the expected 401. Authenticated theorem CRUD and positive-role production smoke remain pending. Lean and Library Agent are not deployed. The deployed commit and release record are synchronized to Forgejo `feature/platform-v1`; branch head `fa416b3` adds the Issue #40 evaluation artifacts without changing the deployed runtime.

### Issue #40 XeLibrary 核心效率评估

`opencode/issue-40` 的隔离基准、Rust 原型和 ADR 已审查并合入 `feature/platform-v1`（`fa416b3`）。复核确认全部 92 项基线测试通过；独立复跑 500 行/1000 个 256D 向量基准得到一致的 best index/checksum，OpenAPI 没有变化。ADR 0004 接受“不为 V1 整体重写”的决定：测量中查询瓶颈在逐行 ACL/catalogue 连接；Rust 只作为隔离实验，不进入生产依赖或发布物。样本为合成数据和本机进程内调用，不代表生产 SLO；C++/pybind11 本轮未测。Issue #70 已在 `193724a` 集成每表每请求 ACL snapshot 与 row ACL 复合索引范围查找；5,000 行本地选择查询约从 1,143 ms 降至 33 ms，真实性能仍需上线负载验收。此实现已随 release `57798068-69cf-492f-a3a0-7b852be4c91c` / deployment `94306d9d-c6d6-4495-8ab3-92df80fa11b4` 部署；服务 readiness、匿名拒绝及管理员只读请求通过，非管理员 row ACL 生产验收尚待完成。Issue #11 的 Qdrant 生产接线已完成；已认证生产语义查询仍待验收。

Issue #48 isolated table fixture support (2026-10-01, deployed `03adae4`; release `98d3adb8-e3d3-4be2-8d84-1bce1e32d9a3`, deployment `eb761639-270b-426b-b832-328e8619ec5f`): `DELETE /api/v1/tables/{id}` removes empty tables only and requires `xelibrary:admin` plus the current table ETag. It refuses rows (including soft-deleted rows), attachment/vector references, queued/running index jobs, or active uploads. Successful deletion removes the `.xl` file, catalogue entry, table/row ACLs, and completed/cancelled upload-session metadata/files while retaining an independent audit event. A stable per-table `flock` lock under `DATA_DIR/.table-locks` serializes writers, uploads, index-job creation, recovery and deletion across local processes; shared locks protect reads through open/query, preventing SQLite from recreating a table file during rename. A durable deleting marker and tombstone recovery handle interruptions on both sides of the catalogue commit. Local tests cover blockers, ACL/upload cleanup, race interleaving, restart recovery and backup-root validation; the merged full suite has 208 passing tests, targeted Ruff and OpenAPI export passed, and the frontend production build passed. Post-release read-only checks found all five `/health/ready` endpoints and the desktop homepage healthy; unauthenticated API requests were rejected with 401, including a request to the new delete route. No production table was created or deleted during smoke testing. Authenticated admin deletion and browser-based isolated-fixture acceptance remain pending, so Issue #48 stays open. Content-addressed object garbage collection remains unimplemented, and cleanup for any future external vector backend remains a production dependency.


Issue #11 Qdrant `fast_text` production integration (2026-10-01): release `e6cb8491-dab2-4ec4-bed7-56795d195235`, deployment `bec2b250-f25d-4312-b8f4-d23cd5b8233f`, commit `c9e095108b678e98037fc123bb15dabae54ed7e7` completed through Deploy Gateway. VM104 now receives protected Qdrant URL/key-file/CA settings. Coordinator validation: 234 backend tests, scoped Ruff, unchanged OpenAPI export, pre-release 6-file data snapshot verification and isolated restore; all seven deployed service readiness probes and public desktop/Forgejo/OIDC discovery returned 200. A live, uniquely named Qdrant collection was created, queried for one point and removed by the acceptance script. A positive authenticated XeLibrary semantic API request and production reindex of existing rows remain unverified, so Issue #11 stays open. Details: `docs/acceptance/qdrant-fast-text-2026-10-01.md`.
### Issue #71 桌面 UI/UX 重构增量（本地）

`opencode/issue-71-desktop-ui`（worktree `runtime/agent-worktrees/issue-71-desktop-ui`）交付第一阶段到第四阶段的可验证增量：新增 `design-system/`（tokens、primitives、menu-logic）与 `shell/`（DesktopShell、MenuBar、Dock、LaunchPad、CommandPalette、WindowFrame、IFrameApp、window-manager、内置应用注册表），窗口管理改为纯函数并新增拖动/resize 下限/最大化避让 Menu Bar 与 Dock/双击标题栏/键盘快捷键，窗口与 Dock 顺序 debounce 自动保存并启动恢复，iframe 超时给出重载与外开入口；设置重组为分类并新增 `dock_size`、`dock_autohide`、`restore_windows`、`animations`、`reduced_motion` 向后兼容偏好字段。保留生产 QA 依赖的 DOM 契约（`nav.dock`、`.window`、`.bar .close`、`.launchpad`、`.knowledge`、`.settings-panel`、`.settings-tabs`、`[data-testid=session-*]`）。验证：211 项后端测试、24 项前端测试（新增窗口管理、菜单键盘逻辑与合成 API 浏览器验收集合）、TypeScript/Vite production build、OpenAPI 重新导出通过；合成数据截图覆盖 1920×1080/1440×900/1366×768/1024×768/390×844、明暗主题、LaunchPad 与命令面板，见 `docs/acceptance/ui-refactor-2026-10-01.md` 与 `docs/ui-audit.md`。未完成：XeLibrary 三栏布局与表格/搜索/Query Console、System App 分页、Forgejo 原生 Dashboard 数据源、真实后端登录与生产浏览器验收；未部署、未推送远端。
