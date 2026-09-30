# 完整 Plan 逐章验收审计

> 审计更新：2026-10-01。源码以当前 `feature/platform-v1` 为基线；生产记录以 `docs/runbooks/deployment.md` 中最近完成的 release 为准。表格区分源码、测试和生产验收证据；`部分实现` 不代表该章节完整达标，也不把单元测试当作硬件或浏览器实测。

状态含义：`本地实现 / 测试通过 / 未发布` 为当前工作分支内证据；`部分实现` 表示代码只覆盖所述子集；`外部实测待证` 表示实现依赖 VM/PVE/存储/身份供应商等仓库外状态；`未实现` 表示代码库内缺少该能力。详细服务现状见 [implementation-status.md](implementation-status.md)，规则基线见 [plan.md](../plan.md) 与 [ADR](adr/)。

| § | 章节 | 状态 | 源码/记录证据及实际缺口 |
|---:|---|---|---|
| 0 | 项目摘要 | 整体未达 V1 | 现有服务覆盖桌面、XeLibrary、D100M、网关、DevAgent 与 Deploy；《实施状态》声明 V1 未完成。 |
| 1 | 固定硬件条件 | 外部实测待证 | 盘点与部署记录涉及 PVE，但仓库无当前硬件实测清单可证全项一致。 |
| 2 | VM 规划 | 外部实测待证 | `infra/pve/desired-vms.json` 与部署记录覆盖部分 VM；不是计划中的完整 VM 拓扑。 |
| 3 | SSD 与 HDD 布局 | 外部实测待证 | XeLibrary 备份/恢复脚本和 PVE 文档存在；盘/池水位与全部磁盘布局未自动验收。 |
| 4 | 逻辑网络分区 | 外部实测待证 | 有 Network Server 防火墙配置与区间部署记录；计划四区隔离仍未逐条证明。 |
| 5 | 逻辑组件关系 | 部分实现 / 未做生产验收 | 代码有 FastAPI 服务调用边界、Tool Gateway 与 Deploy Gateway；多项计划服务仍是 skeleton。 |
| 6 | 推荐代码仓库结构 | 部分实现 / 未做生产验收 | 当前 Python package、React app、infra、docs、tests 可见；计划 package/service 布局未全部拆分。 |
| 7 | 初始技术栈 | 部分实现 / 未做生产验收 | FastAPI/React/SQLite/OIDC 等已使用；PostgreSQL/Qdrant/异步队列等计划栈尚缺。 |
| 8 | `.xl` 文件格式 | 部分实现 / 未做生产验收 | `xeonolab/library.py` 维护 SQLite `.xl` 文件、schema 与变更历史。 |
| 9 | XeLibrary 附件 | 部分实现 / 未做生产验收 | 内容寻址对象、附件引用、上传/绑定/下载/备份路径在 `library.py` 与 `backup.py` 有实现。 |
| 10 | D100M-Embedding 固定参数 | 部分实现 / 未做生产验收 | 真实 D100M 256D、token 上限、revision 与 CPU smoke 在 `embedding.py`/测试中实现。 |
| 11 | Qwen3-VL-Embedding-2B 角色 | 部分实现 / 未做生产验收 | Qwen API 只报告 not_configured/503；没有模型 worker。 |
| 12 | Chunk 设计 | 部分实现 / 未做生产验收 | `search.py` tokenizer-aware chunking 与 chunker 版本有测试；title 约定未落地。 |
| 13 | 向量空间 | 部分实现 / 未做生产验收 | D100M 本地精确向量索引存在；计划中的多逻辑向量空间与 Qdrant 尚无。 |
| 14 | 默认查询策略 | 部分实现 / 未做生产验收 | Fast semantic search 已实现且 ACL/行版本检查；quality 与 image 分支不可用。 |
| 15 | 定理区数据模型 | 部分实现 / 已发布，生产读写验收待做 | XeLibrary 定理字段、confidence/provenance、formal/review 状态分离、修订历史、依赖与 evidence refs 已随 release `7779e286` 部署。正向授权读写尚未在线验收；Lean 验证器仍未实现，详见 `xeonolab/theorems.py`。 |
| 16 | API 总体规范 | 部分实现 / 未做生产验收 | `common.py` 提供版本化 API、认证、request ID、错误格式、ETag；缺少完整兼容策略。 |
| 17 | 通用运维 API | 部分实现 / 未做生产验收 | 四个运维端点由 `common.py` 提供；ready 目前只用可选 callable。 |
| 18 | Web-Server API | 部分实现 / 未做生产验收 | 桌面 API 有 app registry、布局、偏好、ACL/account settings；协调器报告 `runtime/browser-private/issue41-results/report.json` 有 34+ 项真实 Chrome UI 检查。此证据覆盖 Issue #41 桌面路径，仍不等于本节所有 registry/API/security 条款都已验收。 |
| 19 | OpenWebUI → XeLibrary Tool API | 部分实现 / 未做生产验收 | Tool Gateway 有 search/query/get 且转发用户 bearer；attachment/theorem/derive/proposal 工具缺少。 |
| 20 | XeLibrary Catalog / Path API | 部分实现 / 未做生产验收 | 没有独立 path/catalog CRUD；路径是 table 的逻辑字段。 |
| 21 | XeLibrary Table API | 部分实现 / 未做生产验收 | 建表、schema、move、list/get 已实现；新增管理员 + If-Match 保护的空表删除，并对行、附件、向量、索引任务和活动上传 fail closed。表统计等 API 仍缺，删除仅有本地隔离测试，未生产验收。 |
| 22 | XeLibrary Row API | 部分实现 / 未做生产验收 | 行 CRUD、分页、历史、restore、软删/管理员 purge 已实现。 |
| 23 | XeLibrary Bulk API | 未实现 | 无通用 bulk insert/update/delete job API。 |
| 24 | XeLibrary Import / Export API | 部分实现 / 未做生产验收 | 表导出/备份存在；用户导入与完整 import/export 契约缺失。 |
| 25 | XeLibrary Attachment API | 部分实现 / 未做生产验收 | 内容寻址上传、元数据、绑定解绑、ACL 保护存在；quarantine/scanning/预览隔离未有。 |
| 26 | XeLibrary Query DSL | 部分实现 / 未做生产验收 | DSL parser/interpreter 有受限安全子集；EXISTS/SEARCH TEXT/HAS_ATTACHMENT/PATH_DEPTH 未实现。 |
| 27 | XeLibrary Query API | 部分实现 / 未做生产验收 | 查询和 explain 可用；完整计划过滤、语义与异步 job 行为仍缺。 |
| 28 | XeLibrary Search API | 部分实现 / 未做生产验收 | FTS/semantic endpoints 存在；Qwen/image/hybrid 仍不可用。 |
| 29 | Search Result 统一返回字段 | 部分实现 / 未做生产验收 | 不同 endpoints 的响应 envelope 部分统一；search result schema 未统一完成。 |
| 30 | XeLibrary Index API | 部分实现 / 未做生产验收 | 重建索引与持久化 job 状态实现；自动恢复/调度/Qdrant 未实现。 |
| 31 | XeLibrary Revision API | 部分实现 / 未做生产验收 | 行 revisions、历史读取、restore 和 If-Match 有实现与测试。 |
| 32 | XeLibrary ACL API | 部分实现 / 未做生产验收 | path/table/row ACL、继承、ETag/If-Match、审计 API 有实现与测试。 |
| 33 | XeLibrary Proposal API | 未实现 | 没有 proposal 存储、审批或 API。 |
| 34 | Theorem API | 部分实现 / 已发布，生产授权读写验收待做 | XeLibrary 的 CRUD、search、dependencies/dependents、evidence、promote/reject、路径 ACL、If-Match/ETag 与审计已随 release `7779e286` 部署；本次仅验收了服务健康和未认证请求拒绝，尚未做已授权主体的线上 CRUD/审核验收。`verify` 和 `candidates` 在 Lean/Library Agent 未配置时明确返回 503。 |
| 35 | Embedding API | 部分实现 / 未做生产验收 | Embedding D100M fast endpoint 有真实模型；Qwen/image 端点不可用。 |
| 36 | Embedding 调度优先级 | 部分机制 / 关键能力缺失 | CPU worker busy 会 429；没有请求优先级/后台 ingestion 抢占队列。 |
| 37 | HF-cache API | 部分实现 / 已发布，物理池接线未验收 | `xeonolab/hf_cache.py` 提供 ensure、manifest/hash、items、pin/lease、evict/GC、storage 与异步预取 job；运行记录确认 API 已部署。生产物理池数据源仍未接线，真实模型缓存下载/回收场景待验收。 |
| 38 | HF-cache 预测性 API | 部分实现 / 已发布，自动发现未实现 | 有 dependency signal、候选 accept/reject、预取 job/retry 和使用容量保护；生产配置引用与 Issue 扫描的自动发现/决策闭环仍缺。 |
| 39 | Library Agent API | Skeleton / 关键能力未实现 | Library Agent 仅 skeleton；无 derive/evidence/proposal 流程。 |
| 40 | Lean 验证接口 | 未实现 | 没有隔离的 Lean 编译/验证 sandbox。 |
| 41 | LLM Gateway API | 部分实现 / 未做生产验收 | LLM Gateway 支持固定模型、服务端密钥与非流式转发；无计划全量能力。 |
| 42 | Network Server API | 部分实现 / 未做生产验收 | `infra/network` 有防火墙、反代、Tunnel 健康服务；PVE 网络分区全项未证。 |
| 43 | Forgejo 集成 | 部分实现 / 未做生产验收 | Forgejo signed webhook、去重与 issue work sync 有实现；PR/scope 自动化未完成。 |
| 44 | DevAgent API | 部分实现 / 未做生产验收 | work queue、links、签名 webhook、规则 triage 已有；自动重复处理/LLM 分类未实现。 |
| 45 | 多 Agent 身份模型 | 部分实现 / 未做生产验收 | 账号 authority、OIDC agent_kind、work/deploy fencing 有测试；多主体现实并发验证仅限专门报告。 |
| 46 | Deploy Gateway | 部分实现 / 未做生产验收 | Deploy Gateway 有 release/approval/lease/deployment API；`docs/runbooks/collaboration.md` 记录已完成真实 deployment 与 lease 释放，`docs/runbooks/deployment.md` 记录受限 executor。任意 release 自动拉取、SSE 与跨版本迁移仍缺。 |
| 47 | Deploy Lease API | 部分实现 / 未做生产验收 | 单 lease、holder/epoch/renew/release、过期冻结均有测试；运行手册记录生产 lease/deployment 完成。仍缺完整事件流与所有状态迁移的生产验收。 |
| 48 | Release API | 部分实现 / 未做生产验收 | release API 与人类审批有实现；签名 artifact inventory 完整性仍有限。 |
| 49 | Deployment API | 部分实现 / 未做生产验收 | 部署状态接口与固定 executor 源码存在，运行记录包含真实 completed deployment；SSE/cancel/rollback API 缺。 |
| 50 | 部署状态机 | 部分实现 / 未做生产验收 | 关键 lease fence 实现；plan 完整状态机与生产故障转换未完整。 |
| 51 | Deploy Gateway 内部执行 | 部分实现 / 未做生产验收 | 有受限 receiver、systemd staging/activation；部署 runbook 记录按 allowlist 发布并完成。SSE、任意 release 自动拉取、PVE 硬件编排仍未实现。 |
| 52 | `deployctl` CLI | 未实现 | 没有 deployctl CLI 命令集。 |
| 53 | `xlctl` CLI | 部分实现 / 未做生产验收 | `xlctl` 提供 API 客户端基础操作；import/export/theorem/完整索引命令未齐。 |
| 54 | `hf-cachectl` | 未实现 | 没有 hf-cachectl。 |
| 55 | OpenWebUI 权限原则 | 部分实现 / 未做生产验收 | Tool Gateway scopes 与 bearer passthrough 限制写操作；真实 OpenWebUI integration 未完成。 |
| 56 | Web / iframe 安全 | 部分实现 / 未做生产验收 | iframe origin/source 校验、sandbox registry 有代码；所有独立 origins 的浏览器验证待完成。 |
| 57 | 身份服务 | 部分实现 / 未做生产验收 | Keycloak OIDC、PKCE browser session、account authority 有实现；身份全生命周期/部署验证仍需。 |
| 58 | Secrets | 部分实现 / 未做生产验收 | 源代码未纳入凭据；服务配置从 env/root-only paths 读取；秘密轮换流程仍待完善。 |
| 59 | 审计 API / Audit Log | 部分实现 / 未做生产验收 | ACL、account、deploy、work、app、library 审计存在；缺单一统一分页 audit API。 |
| 60 | Job API 统一规范 | 部分实现 / 未做生产验收 | index/work queue 状态 API 存在但 job 状态、取消、events 契约不统一。 |
| 61 | Event / Webhook 设计 | 部分机制 / 关键能力缺失 | Forgejo webhook 接入签名/幂等；DB outbox 与跨服务事件总线未实现。 |
| 62 | Webhook 重试规则 | 部分机制 / 关键能力缺失 | 已测试 Forgejo delivery 去重与乱序；通用重试/最少一次投递规则未实现。 |
| 63 | 监控 | 部分实现 / 未做生产验收 | 只有 request/error counters 与健康端点；计划业务队列/存储/延迟指标与dashboard未齐。 |
| 64 | 日志 | 部分实现 / 未做生产验收 | 标准 JSON request completion logging 存在；统一 timestamp/actor/event 字段与敏感数据策略需继续。 |
| 65 | OpenTelemetry | 部分机制 / 关键能力缺失 | 计划明确 V1 可先有 request ID/log/metrics；distributed tracing 未实施。 |
| 66 | XeLibrary 内部一致性 | 部分实现 / 未做生产验收 | `.xl` 事务、FTS、附件引用、index revisions 有实现；跨 catalogue/.xl 原子性及 outbox 未实现。 |
| 67 | 删除语义 | 部分实现 / 未做生产验收 | 行软删除立即过滤、当前 ACL 和索引重检存在；附件对象 GC 尚未实现。 |
| 68 | 权限与向量查询 | 部分实现 / 未做生产验收 | 检索候选返回前按当前 ACL/行状态复核；规模/严格 topK 生产保证未验证。 |
| 69 | fnOS 边界 | 部分实现 / 外部验证未覆盖 | 有 fnOS VM/数据边界记录；独立认证/平台停机时访问未在本轮实测。 |
| 70 | 备份 | 部分实现 / 未做生产验收 | XeLibrary backup/verify/restore 有脚本与故障测试；`docs/runbooks/deployment.md` 记载 2026-09-30 实际备份及 integrity/pg_restore 检查。定期自动化、异地存储和完整模型权重副本仍缺。 |
| 71 | XeLibrary 恢复顺序 | 部分实现 / 未做生产验收 | 恢复脚本与 runbook 有顺序；`dd6a397` / `docs/implementation-status.md` 记录发布前 quiesced backup 和 isolated restore（含空 object store 回归）通过。完整 PVE 灾难恢复与跨 catalogue/.xl 原子快照仍未验证。 |
| 72 | HF-cache 水位 | 本地实现 / 测试通过 / 未发布 | `infra/pve/pool_telemetry_collector.py`、`infra/hfcache/pool_metrics_receiver.py`、systemd units 与 `tests/test_pool_telemetry.py` 已合入；见 `docs/runbooks/pool-telemetry.md`。尚未选择生产 pool/address/secret、安装服务或应用防火墙规则，物理水位尚未在生产消费。 |
| 73 | 开发权限 | 部分实现 / 未做生产验收 | 仓库贡献规则/API 边界文档存在；远端 Forgejo 权限矩阵需平台配置证据。 |
| 74 | Codex 开发规则 | 部分实现 / 未做生产验收 | AGENTS/Agent workflow 有独立 worktree、测试与发布规则；PR required checks 需远端配置核实。 |
| 75 | Codex 发布规则 | 部分实现 / 未做生产验收 | Deploy policy 限制 OpenCode；只有通过工作 API 可证明 API policy，不等同所有主机凭证盘点。 |
| 76 | OpenCode 角色 | 部分实现 / 未做生产验收 | 独立 OpenCode 身份与 Codex/OpenCode lease 隔离有验证脚本；agent credentials 不纳入本审计。 |
| 77 | 初始开发阶段 | 部分实现 / 未做生产验收 | 《实施状态》记载部分基础设施部署；完整 Phase 0 未达标。 |
| 78 | Phase 1 — Repo / API Skeleton | 部分实现 / 未做生产验收 | 服务 skeleton、OpenAPI、统一 request ID 与 error 有实现；全服务 readiness/CI acceptance 未完。 |
| 79 | Phase 2 — XeLibrary Core | 部分实现 / 未做生产验收 | 表/行/schema/附件/query ACL 已部分实现；path catalogue/import 全套缺。 |
| 80 | Phase 3 — D100M Search | 部分实现 / 未做生产验收 | D100M 到精确向量搜索已有 smoke；Qdrant、队列、priority 尚缺。 |
| 81 | Phase 4 — Qwen / Multimodal | 部分实现 / 未做生产验收 | Qwen worker、image、rerank、quality cache 均无。 |
| 82 | Phase 5 — Web / OpenWebUI | 部分实现 / 未做生产验收 | Desktop shell/app registry/tool search-query-get 存在；OpenWebUI token exchange 缺。 |
| 83 | Phase 6 — Library Agent | 部分实现 / 未做生产验收 | Library Agent 任务/evidence/theorem/proposal/Lean 闭环不存在。 |
| 84 | Phase 7 — DevAgent | 部分实现 / 未做生产验收 | Webhook/work queue/links/triage 有组件；端到端自动 triage/duplicate 并未完成。 |
| 85 | Phase 8 — Deploy Gateway | 部分实现 / 未做生产验收 | leases 与受限执行器代码存在；SSE/full rollback 状态机及全量 production acceptance 缺。 |
| 86 | Phase 9 — HF-cache | 部分实现 / 未做完整生产验收 | HF-cache API、异步预取、候选审核、pin/lease/GC 和 fail-closed 水位消费者已实现；一般 API 已发布。PVE 物理遥测 collector/receiver 与自动依赖发现仍未上线。 |
| 87 | API 测试矩阵 | 部分实现 / 未做生产验收 | 新增 429 身份/路由组回归；全 endpoint 状态码/幂等/并发矩阵并未完整。 |
| 88 | Query 测试 | 部分实现 / 未做生产验收 | 中文/类型/嵌套布尔等 DSL tests 存在；attachment EXISTS/FTS/quality/stale/move 矩阵不完整。 |
| 89 | D100M 评测 | 未实现 | 没有本领域独立 held-out retrieval evaluation 与 Recall@10/nDCG/MRR 基线。 |
| 90 | API 稳定性 | 部分实现 / 未做生产验收 | 目前有 `/api/v1` 与 OpenAPI snapshots；破坏性 schema compatibility CI 未见。 |
| 91 | OpenAPI | 部分实现 / 未做生产验收 | OpenAPI export script 与每服务文档存在；生产 docs access policy 未单独核验。 |
| 92 | API 客户端 | 未实现 | 未见共享生成 client packages；前端 helper/各服务调用分散。 |
| 93 | 数据 ID | 部分实现 / 未做生产验收 | UUID IDs 用于多个记录；不是普遍 UUIDv7/sortable。 |
| 94 | 时间 | 部分实现 / 未做生产验收 | 时间字段为 ISO/epoch混合（如 `at` epoch/字符串）；全 API RFC3339 UTC 未统一。 |
| 95 | 分页 | 部分实现 / 未做完整生产验收 | 多个 listing 支持 cursor（tables/rows/work/ACL）；Work UI 已支持继续加载、游标循环保护和明确末页状态并已发布；生产 API `limit=1` 返回游标且下一页前进。其他 API 仍有 offset/limit，登录态 Work UI 现场操作未验收。 |
| 96 | Rate Limit | 已发布 / 生产阈值待校准 | `xeonolab/common.py` 按身份和路由组以 SQLite sliding window 限流，返回 `Retry-After` 并记录 429 审计事件；包含在最近部署的应用 release。测试见 `tests/test_rate_limit.py`；未做生产压测、429 live smoke、跨服务全局额度或告警保留验收。 |
| 97 | 上传限制 | 部分实现 / 未做生产验收 | 上传有 16MiB size、sniff/hash/name sanitize；quarantine/scanning 与独立 preview 隔离尚无。 |
| 98 | 不允许的架构捷径 | 部分实现 / 未做生产验收 | 代码避免任意 SQL/eval，Tool Gateway/Bearer/Deploy API boundary 有测试；宿主和 CI secrets posture 未全量审计。 |
| 99 | V1 最低可用接口集合 | 部分实现 / 未做生产验收 | 大部分 XeLibrary/D100M/desktop/tool/deploy/development minimum API 存在；theorem、Library Agent、Qwen/image、OpenWebUI 完整工具与 HF-cache 物理接线仍缺。 |
| 100 | 推荐首批 Issues | 部分实现 / 未做生产验收 | 初始 issue 目录/已建问题存在；推荐列表与当前实现状态未逐一同步。 |
| 101 | 初始 Codex 工作方式 | 部分实现 / 未做生产验收 | 工作流程要求读计划/ADR、分支/测试/OpenAPI；本审计也是据此执行。 |
| 102 | Definition of Done | 部分实现 / 未做生产验收 | 测试、权限、文档要求写入指导文件；大量缺功能意味着全局 Definition of Done 未满足。 |
| 103 | V1 成功标准 | 部分实现 / 未做生产验收 | 核心桌面、知识库、D100M、Forgejo/Deploy 部分闭环在线；Library Agent/Qwen/HF-cache/备份等阻止 V1 成功判定。 |
| 104 | 后续可扩展项 | 部分实现 / 未做生产验收 | 多租户、sharding、MCP 等可演进边界具备部分服务接口；尚未对全部扩展项做验证。 |
| 105 | 最终架构原则 | 部分实现 / 未做生产验收 | 服务 API、.xl 数据边界、固定 Deploy Gateway、独立身份有结构性实现；备份、真实事实源、受限 Agent 与可靠恢复仍是阻断缺口。 |

## 本轮限流实现的边界

Rate Limit 实现按每个服务 `DATA_DIR/rate-limits.sqlite3` 持久化滑动窗口；bucket key 为 SHA-256(subject) + 路由组，SQLite `BEGIN IMMEDIATE` 避免同一数据库的并发超发。已覆盖 interactive、bulk、admin、agent、embedding-fast、embedding-quality 默认策略；健康、就绪、版本、metrics 不走身份依赖。429 包含整数秒 `Retry-After` 和结构化 `rate_limit.rejected` 审计日志。生产默认启用；环境策略通过 `RATE_LIMIT_ENABLED` 与 `RATE_LIMIT_POLICIES_JSON` 控制。实现已随最近部署的应用 release 发布，但未做实际 429 生产压力验收。

| 路由组 | 默认上限 | 路由选择 |
|---|---:|---|
| interactive | 120 / 60 秒 | 普通人类主体的 API |
| bulk | 10 / 60 秒 | import/export/bulk/index 路径 |
| admin | 60 / 60 秒 | accounts/deploy/releases/deployments/ACL/audit/apps/schema 路径 |
| agent | 60 / 60 秒 | 其余 agent/codex/opencode 主体 API |
| embedding-fast | 60 / 60 秒 | embed/embedding/embeddings 路径 |
| embedding-quality | 6 / 60 秒 | semantic search 路径 |

覆盖配置示例：`RATE_LIMIT_POLICIES_JSON='{"interactive":{"requests":90,"window_seconds":60}}'`；未覆盖路由组保留默认值。每服务共用其 `DATA_DIR` 下的 sqlite 文件；配置不支持跨服务的全局共享窗口。

尚未做多机/共享数据库验证；不同 service `DATA_DIR` 之间不共享 bucket，这代表身份+路由组是 service-local 限流，不是跨全平台的全局额度。受保护的 `/api/v1/*` 与兼容 `/v1/*` 参与；不经 `identity()` 的无身份 browser-session 登录端点不在此限流实现范围。限流日志由运行时日志系统接收，本轮未验证生产日志保留与告警。Quality mode 的 semantic search 与 `/v1/embeddings` model 字段按 JSON body 分流至严格 bucket；Qwen/image worker 尚未上线，因此不代表 Qwen 生产限额已验收。

## 2026-10-01 源码验证

- `uv run pytest -q`：189 passed；有 Starlette/httpx 弃用提示和 pytest 临时目录清理警告。
- 桌面 `npm test`：11 passed；`npm run test:work-browser`：1 passed，覆盖 Work 分页、游标去重/循环、旧响应丢弃、loaded-only 筛选与 Forgejo 链接。
- 桌面 `npm run build`、`uv run python scripts/export_openapi.py`、`git diff --check`：通过。
- 生产阈值校准、跨进程外部服务压测、限流日志保留检查与真实 429 HTTP smoke 未执行。
