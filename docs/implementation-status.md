# 实施状态 — 2026-10-02

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
  即使 lease 过期，未确认结束的 deployment 仍阻止获取新 lease；DevAgent 只能通过 root-owned、精确 SHA 的受限 deployment helper 部署。
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
| 4 Qwen | #23已实现可选离线CPU worker与typed text/image facade（可选artifact tokenizers0.22.2；版本进入预处理空间），合成进程/API控制流可验证；真实weights、兼容CPU artifact、资源/吞吐/上线未验收，未配置仍503；vision、quality rerank/cache、优先级调度仍不属于本增量，见[Qwen worker](qwen-worker.md) |
| 5 UI/Tools | 桌面和只读网关已实现；OIDC 浏览器 PKCE 登录已接入；OpenWebUI 原生用户 token exchange 尚未接入 |
| 6 Library Agent | 只有 skeleton；推理、证据、proposal、Lean sandbox 未实现 |
| 7 DevAgent | V1 已部署至 VM111：OpenCode 1.18.34、deepseek-v4.1-flash、2 分钟 watcher 与 30 分钟 reconciliation；自动模式只做协调和 Issue 路由，不修改代码。生产部署/重启验证及权限边界见 [DevAgent runbook](runbooks/devagent-maintainer.md) 与 [deployment runbook](runbooks/deployment.md)。多副本共享队列库仍未实施 |
| 8 Deploy | 租约已验证；固定 executor、健康检查与激活失败回滚已实现；完整状态机、SSE、跨版本迁移尚未实现 |
| 9 HF-cache | 已实现 SHA 固定 ensure、HF Hub 下载、manifest/hash、pin/lease/GC、异步预取 job、结构化候选审核、root-fed pool metrics fail-closed reader，以及 `hfcache_usage_bytes`、下载/hit/miss/eviction counters 和结构化事件日志；PVE collector/VM103 receiver 现已实测上线，见 pool-telemetry 验收；VM103 应用未安装，API consumer/真实下载与自动 signal discovery 尚未接入 |

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


Issue #71 Sol 接管本地增量：修复设置回调与保存反馈、空 Dock/排序保存、最大化尺寸恢复、窗口菜单、移动端活跃应用和焦点陷阱；新增只读 System 分栏并复用已有 jobs/work/audit API。OpenWebUI 未部署时明确显示尚未配置，Forgejo 使用完整外链入口；未增加生产依赖。235 backend tests、25 frontend tests 与 TypeScript/Vite build 通过，Chrome 合成夹具检查明暗与五种视口；真实生产登录/集成验收及 #72 Library 整合仍待完成。本记录不是已部署或 #71 已完成的声明。

Issue #71 桌面 Shell/System 增量已于 2026-10-01 部署：commit `5a33d9342f9d65b737d16b64943ffb3cc58d6434`，release `b42c8abe-81be-4024-9fe1-9fafdbc0f186`，deployment `578b46c7-4f05-4eae-8b56-a1490e07e502`，状态 completed，租约已释放。235 backend tests、25 frontend tests、TypeScript/Vite build、Ruff、OpenAPI check 通过；公网静态资源 hash 与构建相同，七个服务 readiness 与直连 IP PKCE/session exchange/restore/API/logout 检查通过。公网 OIDC 配置和 discovery 正常，但本机自动化登录在代理提交 Keycloak 登录表单时超时，公网端到端浏览器登录仍待复测。Issue #71 保持打开：完整 XeLibrary 知识管理界面、原生 Forgejo Dashboard 和其余视觉/浏览器验收尚未达到 Definition of Done。详见 `docs/acceptance/ui-refactor-2026-10-01.md`。

### Issue #72 XeLibrary 产品化增量（已集成并部署）

OpenCode 的 `opencode/issue-72-xelibrary` 交付已集成到 `feature/platform-v1` 并部署。XeLibrary 现在是三栏知识应用：路径树/最近/收藏、表格、Inspector、搜索、Query Console、定理浏览与创建知识表向导。表格支持 sticky header、列宽调整、客户端排序/筛选、cursor 分页、单/多选、键盘导航、右键菜单、附件/revision/删除标识和虚拟窗口；Inspector 提供 Overview/Fields/Attachments/History（diff）/Permissions/Index。Fast 语义搜索保留关键词 FTS 选项并显示 partial/missing_embeddings/approximate；Quality 如实显示未部署；Query Console 校验/执行/分页；定理详情显示真实状态和关联错误，异步陈旧响应会丢弃。Schema 收紧 nullable、查询分页改写、账号隔离的最近/收藏、慢搜索跨表写入、跨表索引任务串显和 ACL 说明等回归均有测试保护。

Coordinator 本地验证：38 项前端测试、235 项后端测试、TypeScript/Vite production build；OpenAPI 导出无 diff。全仓库 Ruff 当前有 11 项未修改 `infra/` lint 问题，本轮无 Python 文件改动。发布 commit `f1e9de3eb44795e8666ed0d0df151ba9b2de6edc`，release `f2c2446a-46cd-4c52-b3dc-779ee2041129`，deployment `958439cf-94b6-442f-8c82-a1d075fa7643` completed，租约已释放；七项 readiness、直连 IP PKCE/session exchange 与公网静态资产 hash 均通过，详见 `docs/acceptance/xelibrary-2026-10-01.md` 和 `docs/runbooks/deployment.md`。Issue #71 保持打开：仍缺每表索引状态 API/自动索引流水线、更多后端过滤能力、收藏跨设备持久化、附件拖拽、Quality/Library Agent/Lean 和原生 Forgejo Dashboard；公网带登录态的完整浏览器流程另行补齐。

Issue #71 System 概览错误状态增量（2026-10-01）已部署：代码 commit `44e5d1a2b2875b122f70eb27daa9afbe6d83361f`，release `85b605ea-46ba-4a26-bcd5-26d1caa79d75`，deployment `33a0af38-94e2-4a60-8d1a-3b6248c281e4` completed，租约已释放。工作队列、索引队列、知识库概览及 readiness 读取失败时统一展示带键盘可用重试的 ErrorState；合成浏览器验收覆盖工作队列失败与恢复。235 后端测试、38 前端测试、TypeScript/Vite build、OpenAPI 导出和 diff check 通过；LAN 五个服务 readiness、桌面 JS/CSS 发布哈希及未认证 API 401 验收通过。未做真实登录态浏览器验收；#71 保持开放。


### Issue #80 过期 Work 认领状态区分（2026-10-01，已部署）

`c16efc35c99ce7deb1b457dad35da08ca805542d` 将 `queued` 与有效 `claimed` 统一作为活动项；过期领取独立计数、展示并允许重领，覆盖 Dashboard、状态小组件、Dock badge、Development、Issues、System Agents 和 Work。Deploy Gateway release `03fd828d-399d-498e-8e7d-e27049ab0da4` / deployment `2083222b-8a1d-4615-bc99-4cd2b671ff9b` 已完成，租约已释放。39 项前端、279 项后端、41 项 Work API 测试通过；TypeScript/Vite build、OpenAPI 无 diff 和 diff check 通过。部署后静态资源与构建哈希一致，5/5 readiness、匿名 API 401；只读生产 Work API 显示活动 21 项、过期领取 20 项。尚未在发布后重新运行登录态 Chrome 流程，因此 #80 暂保持开放，完整 V1 Plan 仍未完成。验收细节见 `docs/acceptance/issue-80-2026-10-01.md`。

### Issue #71 Dashboard 知识表加载状态（2026-10-01，已部署）

首次目录请求、重试及定时刷新等待期间，System Dashboard 显示明确的读取状态，不再把尚未返回的数据误报为 0 张表。功能提交 `9565f03dc0b5a865ffa7b13ef51c1f7b8e5ff84c`，部署提交 `51a859c2e349c51e14a4a9d50bfec6de3b2db797`；release `da941881-eba2-4d91-aa34-74baff428470` / deployment `c27a59c5-8b32-4a4c-a5d0-b03630b94bb3` completed，租约已释放。39 项前端、279 项后端测试和 TypeScript/Vite build 通过；公网登录态浏览器 44/44 检查通过，五服务 readiness、静态 JS/CSS hash 与本机构建一致。此增量没有 API/schema/数据迁移；Issue #71 和完整 `plan.md` 仍未完成。详情见 `docs/acceptance/issue-71-dashboard-loading-2026-10-01.md`。

### Issue #72 子项：收藏/最近跨设备持久化（已集成部署）

分支 `zcode/issue-72-prefs-sync`（基于 `e3c1b30`）：桌面偏好新增规范化 `library_recents`/`library_favorites`（条目仅保留 kind/id/title/at、标题截断 200、列表上限 50、无效条目丢弃，PATCH/GET/重启持久化、账号隔离）；前端 `useLibraryPrefs` 挂载时把服务端副本与本地缓存按 `kind:id` 合并（保留较新时间戳，recent 按时间排序），remember/toggle 变更经 PATCH 推回服务器，localStorage 继续作离线缓存。新增 1 项桌面测试（往返/清洗/隔离/重启）与 1 项 logic 合并测试（前端 46 项、后端 384 项通过）。
Coordinator 集成与发布（2026-10-22）：分支已并入 release base `8d1575a`。冲突解决：`xeonolab/desktop.py` 将本分支的偏好 revision/ETag 与 base 的 `preferences_from_definition()` 兼容读取、`custom_wallpaper_available` 及插件迁移合并（GET 保留兼容读取并返回 ETag；PATCH 保留 If-Match 且返回新 ETag；壁纸移除写入同时推进 revision）；`tests/test_desktop.py`、`docs/implementation-status.md`、`apps/desktop-web/src/library/*` 与 `library-logic.test.mjs` 按并集保留双方新增函数与测试；`docs/api/desktop-web.openapi.json` 由 `scripts/export_openapi.py` 重新生成（仅新增偏好字段）。评审 P2 一并修复：挂载合并现在同时回推 `library_recents` drift（此前只回推 favorites/tombstones，离线 recent 在重挂载后不会上传）；`PATCH /users/me/preferences` 响应返回新 ETag，前端写后更新 `revisionRef`，同会话连续写入不再先吃一次 412；补充两台设备的多设备用例（ETag/If-Match 412 与刷新后重试、A 删除后 tombstone 抑制旧缓存、离线 recent 上传后另一设备可见）。两条既有浏览器验收（附件选择器、批量选择）原断言“只有 GET/不产生写请求”，因 LibraryApp 现在会同步账号偏好而按新行为适配：允许且仅允许 `/users/me/preferences` 的偏好同步写入，记录/AI 写入仍被禁止（断言改为过滤偏好路径后检查，并给出更明确的失败信息）。验证：551 后端 pytest、全仓 Ruff、OpenAPI 无意外 diff；前端 135 单测、tsc/Vite build 与完整浏览器回归。仍不关闭 #72（自动索引流水线等子项另行处理）。
### Issue #74 可视 Embedding 队列增量（已集成部署）

分支 `zcode/issue-74-embedding-queue`（worktree `runtime/agent-worktrees/issue-74-embedding-queue`，基于 `a2f264e`）。Embedding 服务新增 `EmbeddingRequestStore`（`xeonolab/embedding_requests.py`，SQLite `embedding-requests.sqlite3`）：每个到达 D100M worker 的请求记录 running→completed/failed（模型、文本数、字符数、truncation、tokens、耗时、错误码、调用方 sub），忙时 429 拒绝也记为 failed/EMBEDDING_BUSY；未配置/未知模型请求不入库。已完成历史滚动保留最近 1000 条，running 行永不剪枝，进程重启时遗留 running 行标记 WORKER_RESTARTED（沿用 index_jobs 恢复语义）。新增只读端点 `GET /api/v1/embedding/queue`（active+counts+worker 状态）与 `GET /api/v1/embedding/requests?limit=&cursor=&status=`（新到旧 cursor 分页），scope 均为 `dev:read`（与 work/issues 运维只读视图一致，默认桌面身份可见）；队列不含任何输入文本，避免知识内容经 dev:read 泄露。`infra/network/xeonolab.caddy` 与 `cloudflare-origin.caddy` 新增 `/api/v1/embedding/queue*`、`/api/v1/embedding/requests*` → 192.168.0.107:8104 路由。桌面新增原生应用「Embedding 队列」（`EmbeddingQueue.tsx` + `embedding-queue-logic.js`，注册于 `shell/apps.tsx`，permissions `dev:read`，10 秒轮询 + 陈旧响应丢弃 + cursor 去重），展示 worker 忙碌/就绪/未配置、运行中/已完成/失败计数、未完成请求列表与最近请求表（加载更多分页）。验证：新增 8 项后端测试（全库 243 项通过）、5 项前端逻辑单测（全库 44 项通过）、tsc+vite build、OpenAPI 重新导出（仅 embedding.openapi.json 增加两路径）、合成数据 headless Chrome 验收通过（截图 `runtime/acceptance-artifacts/issue-74-embedding-queue-1440x900.png`，测试数据全部为本地合成 fixture）。未做真实登录态浏览器验收与生产路由验收，需发布后由协调器补充；忙时拒绝请求会占用每身份 embedding-fast 限流窗口之外的额外写库（可忽略量级）。

Coordinator 集成与发布（2026-10-22）：分支已并入 release base `e031b073`，唯一冲突为本文档同点插入（已按双方段落并集解决）与生成文件 `docs/api/embedding.openapi.json`（已用 `scripts/export_openapi.py` 重新生成，diff 仅新增两路 queue 端点）。同时把新浏览器验收 `tests/browser-acceptance-embedding-queue.test.mjs` 注册进 `run-browser-regression.mjs`，使 CI 实际执行该验收；并同步更新两条按目录数量断言的既有浏览器验收（`browser-acceptance-icons` 的 dock/LaunchPad 图标数 8→9、`browser-acceptance-launchpad-origin` 的全目录数 11→12、native 8→9），因为新增的「Embedding 队列」应用（`dev:read`、dock 可见）确实扩大了应用目录。新浏览器验收按既有 raw-CDP 验收的约定，在 root CI 容器内自动追加 `--no-sandbox`（本地保持沙箱），修复 CI 容器中 Chrome 无法启动的问题。验证：后端 537 项 pytest、Ruff 全通过、OpenAPI 导出无 diff；前端 122 项单测、TypeScript/Vite build、新 Embedding 队列浏览器验收通过；随后走精确 head CI、受保护 preflight、独立审批、合并与发布验证，并在生产边缘同步新增的 `/api/v1/embedding/queue*`、`/api/v1/embedding/requests*` 路由（LAN caddy 为运维同步文件，部署后单独校验）。仍不做等待/优先级调度：繁忙请求保持 429，历史保留最近 1000 条且按账户隔离。
### Issue #72 每表索引状态 API 增量（已集成部署）

分支 `zcode/issue-72-index-status`（worktree `runtime/agent-worktrees/issue-72-index-status`，基于 `24ee54e`）。补齐 #72 交付报告列出的首要缺口「每表 Index Status API 缺失，普通用户无 `xelibrary:admin` 读不到任务」：新增 `GET /api/v1/tables/{tid}/index-status`（scope `xelibrary:read` + 既有表读 ACL），从表文件本身推导真实覆盖——`row_count`（未删除行）、`embedded_rows`/`missing_rows`（`__xl_vectors` 按行当前 `_revision` 联结统计）、`pending_rows`（`__xl_changes` 中 `revision > indexed_revision` 的去重行数）、`chunk_count`、`last_job`（最近一条索引任务，不限 owner），并派生 `state`：queued/indexing/failed（最近任务）、empty（无行）、not_indexed（从未嵌入）、stale（有待索引变更或部分覆盖）、ready（全部覆盖）。模型/分词器升级导致的过期块不并入计数（搜索结果已单独提示）。前端 `library/hooks.ts` 新增 `useIndexStatus`（generation 守卫防串表），`logic.js` 新增 `indexStatusFromServer` 纯映射，Inspector「索引」标签改为优先展示真实覆盖统计与最近任务，无此 API 的旧后端或无权限时回退原 jobs 视图/提示；重建索引按钮与刷新语义不变。验证：新增 3 项后端测试（空表/未索引/ready/stale 推导、失败任务、401/403 ACL），全库 282 项通过；改动文件 Ruff 通过；OpenAPI 重新导出仅 xelibrary.openapi.json 增加一路径；前端 49 项测试通过（`indexStatusFromServer` 6 断言组 + library 浏览器验收新增已索引行/向量块计数断言），tsc+vite build 通过；合成数据截图 `runtime/acceptance-artifacts/issue-72-index-status-1440x900.png`。未做真实登录态浏览器验收；自动索引流水线（行变更后自动排队）涉及成本与触发策略决策，另行提案，不在本增量。

Coordinator 集成与发布（2026-10-22）：分支已 rebase 到当时 release base `887bd14`（唯一冲突为本文档同点插入，已按双方段落并集解决），并补齐评审 non-blocking nit：Inspector「最近任务」仅在 `job_id` 存在时渲染 id（共享表读者的最小摘要不再显示前导 `·`），客户端 `IndexStatus.last_job` 类型同时允许该最小摘要（`job_id`/`owner` 可选）。验证：后端 528 项 pytest、Ruff 全通过、OpenAPI 重新导出无 diff；前端 117 项单测、TypeScript/Vite build 与 XeLibrary 浏览器验收通过；随后走精确 head CI、受保护 preflight、独立审批、合并与发布验证。仍未做真实登录态生产浏览器验收；自动索引流水线（行变更自动排队）不在本增量。

### Issue #94 修复（zcode 本地交付）

公网边缘（`cloudflare-origin.caddy` 站点块）补齐与 LAN `(xeonolab-desktop)` snippet 一致的安全头——`X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`，并按 Issue 建议的灰度值在两侧同时加 `Strict-Transport-Security "max-age=300"`（公网源站为 HTTP，经 Cloudflare HTTPS 边缘下发后浏览器按 HTTPS 响应生效；max-age 短窗灰度，可后续调大）。

首个增量（PR #106）用共享 `security-headers.caddy` snippet，但 VM101 的激活路径只安装 `infra/network/cloudflare-origin.caddy`，不安装被 import 的兄弟文件与 `xeonolab.caddy`；2026-10-22 的部署因此在 Caddy reload 时报 `File to import not found: security-headers.caddy` 并在激活后自动回滚（生产保持上一版本，未受影响）。随后修订为部署兼容方案：两个文件均内联同样的三条头（不再新增/import 文件），`tests/test_edge_security_headers.py` 改为按大括号作用域提取两个站点块，断言必需头齐全、值精确、两侧集合一致，且禁止 import 激活清单之外的文件。VM101 的 `xeonolab.caddy` 仍为运维手工管理文件（激活不覆盖），由操作者按仓库内容同步。CSP 的完整方案（iframe 应用白名单等）需单独评估，不在本增量内。

### Issue #116 桌面自定义能力（本地，未发布）

本地分支新增单账户自定义壁纸：`desktop:read` scope 下由 API 使用认证主体隔离，服务端限制 5 MiB/1600 万像素/单帧，解码验证 JPEG、PNG、WebP 后重新编码为 WebP，客户端路径和 MIME 不作为信任依据；API 提供读取、上传和重置。保留账户图片之外的纯色/渐变配置与压暗层，将图片数据留在受限资产 API、不写入偏好 JSON，并兼容旧版 `wallpaper=custom` 图片偏好。桌面组件包含时钟、系统状态、时区日历、按授权 registry 过滤的应用快捷入口、按当前权限显示的工作队列和 XeLibrary 搜索，以及账户偏好中的快速便签。便签显示保存状态并在失焦、pagehide 和组件卸载时尝试 flush；移动组件条适配 390 px 并可用键盘访问。验证覆盖账号隔离、重启持久化、旧偏好兼容、无效/超限图片、背景压暗叠放、工作项状态筛选、权限收缩、便签 pagehide 保存与键盘可用性。

本地全量后端 398 项、前端 48 项、浏览器合成验收、TypeScript/Vite build、目标 Ruff 与 OpenAPI 导出通过。后续加固在 `xeonolab/wallpaper_backup.py` 提供有界图片处理及本地桌面+壁纸备份/校验/隔离恢复：重编码输出受 5 MiB 限制；壁纸上传、替换、删除和快照使用同一个数据根文件锁，SQLite Backup API 快照后按快照中的不可变 UUID 复制文件，并校验元数据、哈希和实际 WebP 解码；备份默认总数据预算 512 MiB、1,000 个资产、预留 64 MiB 空间，可由 CLI 调整；空间预检不是并发写入下的绝对保证。每个服务进程两个图片处理中槽位，超容量返回 429（多进程部署的上限按进程数相乘），取消请求会等待受限线程真正结束后清理并释放槽位。`tests/test_wallpaper_backup.py` 覆盖 Alice/Bob 恢复、错 owner/缺文件拒绝、替换并发快照、事件循环响应、容量拒绝和编码失败清理。未部署或做真实登录生产验收。此 helper 仅涵盖 `desktop.sqlite` 与自定义壁纸，不代表账户/会话数据或平台管理秘密的全局备份；生产仍需独立存储、保留/监控和端到端恢复演练。旧版本不兼容 `wallpaper=custom`，回滚必须恢复匹配的旧桌面快照；操作步骤见 `docs/runbooks/desktop-wallpaper-backup-restore.md`。

### Issue #85 修复（zcode 本地交付）

分支 `zcode/issue-85-issues-toolbar`（基于 `754ff27`）：`.ds-tabs` 的 `flex-shrink:0` 使任务页状态筛选条在 390px 视口撑宽 `.issues-app`（实测 scrollWidth 523 > clientWidth 380）并裁切筛选选项。修复限定在 `.issues-toolbar` 作用域：`ds-tabs` 允许 `flex-wrap:wrap;flex-shrink:1;min-width:0`，表格保留自身横向滚动。新增 390×844 合成 fixture 与浏览器验收（app/toolbar 无横向溢出、每个筛选选项完整落在视口内、截图 `runtime/acceptance-artifacts/issue-85-issues-toolbar-390x844.png`）。全前端 45 项测试与构建通过。

### Issue #96 修复（zcode 本地交付）

分支 `zcode/issue-96-blocked-claim`（基于 `754ff27`）：blocked 状态的 work 项不再渲染必然 409 的「领取」按钮，操作列改为「来源冲突 · 不可领取」提示（`work-state.ts` 新增 `isBlockedWorkItem`）；work 浏览器验收新增 blocked 行无按钮断言。全前端 45 项测试与构建通过。

### Issue #93 修复（zcode 本地交付）

分支 `zcode/issue-93-work-active`（基于 `754ff27`）：`GET /api/v1/work` 新增 `?active=true|false` 服务端过滤（SQL 级，游标分页语义不变）并在每行显式返回 `lease_expired` 布尔（claimed 且租约已过期），客户端不再各自实现 `expires > now` 比较；计算列由 SQLite 产出后转回 JSON 布尔以保持既有契约。新增 1 项测试覆盖五种状态的过滤/标记/游标/422（全库 353 项通过），改动文件 Ruff 通过，OpenAPI 导出仅 dev-agent.openapi.json 增加查询参数。

### Issue #92 修复（zcode 本地交付，叠放于 #93 之上）

分支 `zcode/issue-92-work-ordering`（基于 `zcode/issue-93-work-active`，PR 需在 #102 合并后重定向 `feature/platform-v1`）。`work` 表新增 `created_at REAL`（新库建表含列，存量库经 `ALTER TABLE` 迁移、旧行保持 0 即视为最旧）；两个 INSERT 点写入创建时间戳。`GET /api/v1/work` 改为 FIFO 排序（`created_at ASC, id ASC`，分页在新项到达时保持稳定），游标格式升级为 `<created_at>:<id>`（非法值 422 `INVALID_CURSOR`）；新增 `status`（逗号分隔多值）/`repository`/`issue_id` SQL 级过滤，非法状态 422 `INVALID_STATUS`；`meta.counts` 恒返回全队列状态计数（供前端徽章后续迁移）。新增 2 项测试（FIFO/确定性/counts/游标/四类过滤 + 重启与迁移持久化，全库 355 项通过），改动文件 Ruff 通过，OpenAPI 导出仅 dev-agent.openapi.json 变化。前端各视图迁移到服务端过滤属后续增量（counts 已就绪）。

### Issue #91 管理员收尾端点（zcode 本地交付）

分支 `zcode/issue-91-work-complete`（基于 `e3c1b30`）：`POST /api/v1/work/{id}/admin-complete`（scope `dev:write`）补齐队列卫生缺口——无人认领或认领已过期的项可由管理员标记完成（status=completed、holder 清空、epoch+1 fencing），**活跃认领一律 409 `WORK_CLAIMED`**、已完成项 409，写 `work.admin_completed` 审计事件；与 reconcile（Issue 关闭→completed）互补。新增 1 项测试覆盖 queued/过期认领/活跃认领/已完成/404/401/幂等（全库 384 项通过），Ruff 与 OpenAPI 导出通过。
### Issue #83 Bulk rows API（已集成部署）

`POST /api/v1/tables/{id}/rows:bulkInsert|rows:bulkUpdate|rows:bulkDelete` 与 `GET /api/v1/tables/{id}/rows:bulk/{job_id}`：逐条复用单行语义（schema/type 校验、表+行 ACL、逐条 revision 检查、`__xl_changes`、FTS/CJK、Qdrant 清理、删除审计），每条独立事务提交，部分成功按条返回 `index/status/error{code,message}`。批量 ≤ `XELIBRARY_BULK_SYNC_LIMIT`（默认 100）同步执行；超过则 202 + `data.job_id`，后台任务从 catalogue `bulk_jobs` 持久化载荷逐条应用，进度实时可查；`XELIBRARY_BULK_MAX_ROWS` 超限 413 `BULK_TOO_LARGE`；finished 作业保留最近 200 条。

审查修复（PR #109 复审）：`batch_id` 由全局唯一改为 `(owner, table_id, kind)` 作用域——同一作用域相同载荷为幂等重复（返回原 job），不同载荷返回 409 `BATCH_CONFLICT`（旧表在启动时幂等迁移重建）；每条已应用结果在**同一个表事务**内写 `__xl_bulk_applied` 标记，进程重启后从标记对账补回丢失的持久结果再标 failed（提交窗口不再丢记录/误报余量）；删除表时若有 queued/running bulk 作业返回 409 `TABLE_BULK_ACTIVE`。未包含：取消/断点续跑、作业列表分页、前端批量 UI。
### Issue #84 JSONL/CSV import-export（已集成部署）

`POST /api/v1/exports` + `GET /api/v1/exports/{job_id}` + `/download`：按表导出 JSONL/CSV 快照，行级读 ACL 过滤（无读权限的行不会出现），下载与状态仅 owner 或 `xelibrary:admin`。`POST /api/v1/imports` + 状态 + `/cancel`：内容上限 `XELIBRARY_IMPORT_MAX_BYTES`（默认 2MB，超限 413），导出行快照与纯值对象均可导入（往返无缝），逐条结果与错误定位、部分失败不丢弃已应用行；取消为协作式（queued 直接 cancelled，running 由 worker 行间检查），取消/失败清理输出文件。作业持久化扩展 #83 的 `BulkJobStore`（output/cancel_requested 列与 complete/set_done/request_cancel/finish_cancelled），重启恢复语义一致。集成时与 #83 的作用域 batch_id、载荷摘要与恢复对账合并且迁移同时覆盖旧表与导出列；OpenAPI 重新生成。

### Issue #117 XeLibrary 数据生命周期设计（设计稿，未实现）

`docs/design/xelibrary-data-lifecycle.md` 定义 `__xl_changes` 保留窗口（每表 5 万条/30 天，显式维护端点、默认 dry-run、audit）、共享附件对象 GC（跨表引用差集 + 24h 宽限期 + 待删清单对账）、派生索引/向量与模型空间迁移边界、import/export 输出清理与增长指标。实现（维护端点、GC 编排、指标接线）作为后续独立增量，不在本设计内自动启用。

### Epic #305 Phase 1 续：能力版本兼容性检查

`GET /api/v1/capabilities/{id}` 新增 `require` 版本约束（精确 / `^` / `~` / `*`）；不满足返回 409 `CAPABILITY_INCOMPATIBLE`，非法约束 422 `INVALID_VERSION_CONSTRAINT`。与上一轮“已启用插件能力并入发现”共同构成 #313 的前两项交付。

### 2026-11-03 备份/巡检/会话验收（#390/#70/#76/#400–#403）

- 四个数据域每日校验备份全部落地并完成首次自动运行验证（XeLibrary 03:47Z、Forgejo 04:07Z、Desktop 04:15Z、Keycloak 04:46Z），每域保留 7 份、发布前自校验、恢复抽检通过；入口与覆盖缺口见 `docs/runbooks/backup-drills.md`。
- `scripts/ops_audit.py` 增加逐域备份年龄与 backup timer/service 状态检查（默认 36h），并去掉无定时备份主机的一次性手工归档噪声；release 保留计数容忍部署窗口（keep+2）。
- #70 导出路径改为每请求一次 ACL 快照（5,000 行导出 catalogue 连接 5,005→6），`#70` 仍待生产非管理员线上验收。
- #76 会话韧性（缓存 token、Retry-After、失败重放、logout/刷新 fencing）复核通过并关闭；冲突标记文档修复 + 新增 `tests/test_documentation_hygiene.py`（全量 1572 passed）。
- PVE 首次建立每周 VM 级 vzdump 调度（VM105/107/109 → `xeonolab-backup`，keep-last 2）；VM109 首份归档实测 `zstd -t` 通过（SHA256 `7fe4d264…`）。大容量数据盘（VM101/106/110/111）需先标 `backup=0` 并另定策略，见 #113/#390。
