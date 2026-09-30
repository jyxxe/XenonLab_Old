# 实施状态 — 2026-09-30

## 已验证

- 10 个独立可启动 HTTP 服务入口，统一 request ID/error envelope/OpenAPI/metrics。
- OIDC RSA 验签、有效期、issuer、audience 校验；生产模式禁用开发 token。
- XeLibrary `.xl` 格式 1.0：元数据、schema、真实行数据、事务变更日志、FTS5、附件引用。
  bootstrap 行用户字段存储为 values_json，尚未采用每用户列一个 SQLite 物理列。
- 表创建/列表/读取/移动；行创建/更新/读取/分页/删除/历史；schema 迁移；创建行幂等键。
- ACL 以 row → table → 最近祖先 path 为优先级；不存在 ACL 时仅 owner 访问；admin scope 可管理。
- 结构化 DSL：FROM、WHERE、LIMIT、嵌套布尔、比较、IN、CONTAINS、NULL、多语言字段。
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
