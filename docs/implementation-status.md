# 实施状态 — 2026-09-30

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
- 真实 HTTP embedding → 分块 → 本地精确向量索引 → semantic search → 删除过滤已通过 smoke。
- 精确搜索过滤当前 ACL/行版本/删除状态，索引缺失返回 partial/missing_embeddings。
- Deploy release/人工审批/原子单租约/holder/epoch/renew/release/审计。
  即使 lease 过期，未确认结束的 deployment 仍阻止获取新 lease；OpenCode 无法部署。
- Forgejo webhook HMAC 签名、delivery 去重与冲突检测；Issue 文本仅保存为数据。
- Tool Gateway 只提供 search/query/get，透传验证的 bearer token，不代换超级管理员身份。
- LLM Gateway 固定 deepseek-flash、服务端 provider credential、非流式转发。
- React/TypeScript 桌面、受控 app registry、个人布局、窗口管理、iframe origin/source 验证。
- PVE 已上线当前实现：VM 101/104/106/107/109/105；现有 VM 未删除。xenon.lan LAN DNS/TLS，不配置 Cloudflare。

## 尚未完成

| 计划阶段 | 当前状态 / 后续必要工作 |
|---|---|
| 0 基础设施 | PVE 可达并已盘点；已配置部分端口边界、LAN DNS/TLS、Keycloak OIDC；VM 布局调整、完整安全域、Forgejo、监控仍待实施 |
| 1 skeleton | 已实现独立入口与 OpenAPI；CI 工作流已提供；生产 readiness 仍需扩展依赖探针 |
| 2 数据核心 | bootstrap 已可用；PostgreSQL catalogue、路径对象、完整 DSL、批量作业、import/restore/purge、分片待实施 |
| 3 D100M | 实际推理与全链路已验证；Qdrant、后台自动索引队列、优先级调度和领域检索评测待实施 |
| 4 Qwen | 尚无模型 worker、vision、quality rerank/cache；质量请求返回 unavailable |
| 5 UI/Tools | 桌面和只读网关已实现；OIDC 浏览器 PKCE 登录已接入；OpenWebUI 原生用户 token exchange 尚未接入 |
| 6 Library Agent | 只有 skeleton；推理、证据、proposal、Lean sandbox 未实现 |
| 7 DevAgent | 已实现可信 webhook 接收；triage/duplicate/关联/任务队列未实现 |
| 8 Deploy | 租约已验证；固定 executor、健康检查与激活失败回滚已实现；完整状态机、SSE、跨版本迁移尚未实现 |
| 9 HF-cache | 只有 skeleton；ensure/download/pin/lease/物理水位/GC 未实现 |

## 运行与恢复限制

Catalogue 为 SQLite bootstrap，生产 PostgreSQL 迁移另见 ADR 0001。Catalogue 与每个 `.xl`
文件之间没有分布式原子事务，进程崩溃可能产生未登记的孤立 `.xl` 文件；生产发布前须实现
manifest/outbox 与一致性扫描。索引作业状态已在 catalogue 持久化（待发布）；重启中断明确标为失败，自动恢复仍未实现；向量可重新生成。
表导出用 SQLite backup API 生成一致性快照。独立备份位置尚未提供，mirror 不被视为备份。

所有外部 secrets 只从环境读取；用户提供的 PVE/LLM/password 不写入仓库。
已有生产 LAN 上线、未删除 VM、未创建远端 PR。完整 V1 还不能视为验收完成。详见 runbooks/deployment.md。

## 验证记录

13 项后端测试通过；10 个服务均以独立进程通过 HTTP live/version/OpenAPI 检查。
D100M 实际模型端到端 smoke 通过；React/TypeScript production build 与 Ruff 检查通过。
本机缺少 Docker Compose 插件，未运行原 Compose 拓扑；线上采用 systemd，Keycloak 使用 Docker + 本地 PostgreSQL。

## 完整 Plan 推进

已补齐单行历史 revision 读取、restore 创建新 revision 与管理员 purge。
restore 校验当前 schema 与 If-Match，保留历史；purge 删除行历史、索引和附件引用，
保留独立审计，内容寻址对象的实际垃圾回收仍待实现。18 项测试通过。

优先级更新：Issues/Forgejo、权限页面和 Harness 协作优先。
已实现身份 scopes 展示、管理员 ACL 读取/编辑页面、管理员审计查询 API；19 项测试与前端构建通过。
公网主域已确定 xenon.jyxxe.com；Cloudflare 原 no-tunnel 例外已被用户撤销。

DevAgent 已实现持久化 work queue create/list/claim/release：scope dev:read/write、
SQLite 原子领取、TTL 与 epoch fencing、完成状态和审计。并发、重启、权限、过期重领测试通过。
该组件已线上接入，30 个真实 Issues 已入队，UI 领取/释放已发布；自动 webhook 同步、triage 仍待实施，不能视为完整 DevAgent 完成。

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
知识管理器提供表筛选/创建、schema 驱动记录表格/编辑和带 If-Match 软删除；附件/导入导出/历史界面仍待完成。
账号树与 API key 生命周期、持续登录刷新仍未实现。
Issue 同时间戳冲突冻结与主动 source reconcile 已实现且有测试，待配置和发布；25 项测试通过。

### 2026-09-30 WebUI priority update

- Deployed 0688c488: windowed knowledge manager, schema-driven record editing, table creation/filtering, soft delete, LaunchPad, settings/work windows. Full settings, attachment/history/import-export UI remain incomplete.
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
