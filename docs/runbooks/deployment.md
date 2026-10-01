# PVE LAN 上线记录 — 2026-10-01

2026-10-01 更新：Issue #71 知识库错误请求编号修复已通过 Deploy Gateway 上线。代码 commit
`a866e2bb778840a4f12142b1cd2a869b7931129b`，release `6f0436d5-48fe-4c03-b307-83b90f126646`，
deployment `0deb7fc3-6ec8-4cc8-923e-64b69c55147a` 状态 `completed`，租约已释放。纯前端更新，无
数据库 schema 或生产数据迁移。后端 279 项、前端 52 项测试、TypeScript/Vite production build、
diff check 通过，Luna 独立审查通过。部署后 LAN 五个 readiness 与公网桌面 readiness 均为 200；
VM 101/104/106/107/111 均指向此 commit；LAN/公网 JS 与 CSS SHA-256 匹配；认证配置为 200；
匿名表 API 返回预期 401 且携带 request ID。Chrome 错误/重建索引回归使用合成 API，未执行真实生产账号登录；
Issue #71 保持开放，其他未完成范围见 `docs/acceptance/issue-71-request-id-2026-10-01.md`。

2026-10-01 更新：Issue #76 桌面会话冷却重试及原请求恢复已通过 Deploy Gateway 发布。代码 commit
`24ee54e965455827779b5341d62900d880f47079`，release `2514a42d-fcc0-4736-9366-581712785557`，
deployment `c7c5014f-1976-4ea5-aeed-0c2ee9f0feac`，状态 `completed`，租约已释放。发布前桌面数据库
SQLite backup API 快照位于 `runtime/backups/2026-10-01-issue76/predeploy-desktop.sqlite`，完整性检查为 `ok`。
前端测试 48/48、TypeScript/Vite build、diff check 及独立 Luna review 通过；无后端代码或数据库 schema 变更。
部署后 LAN 五个入口 readiness 均为 200；VM 101/104/106/107/111 release symlink 均指向该 commit；LAN 与公网
JS/CSS SHA-256 与本机构建一致，认证配置为 200。无 cookie 的 session token 请求返回预期 `401 NO_SESSION`。
真实五分钟限流症状仍未复现，Issue #76 暂保持打开，等待真实会话验收，细节见
`docs/acceptance/session-resilience-2026-10-01.md`。

2026-10-01 运维记录：查看发布执行器参数时误调用了无参数解析支持的 `scripts/execute_release.py --help`，导致本机
`release.json` 中现有桌面 release 被重新激活。目标仍是已部署的 commit
`51a859c2e349c51e14a4a9d50bfec6de3b2db797`；release `fda082c8-4557-4efb-8e1d-3f339e7e73cb`、deployment
`8ad95f57-f985-403d-a358-e6c4c9dbea41` 状态均为 `completed`。没有切换到本轮候选代码，也没有 API、数据库 schema
或生产数据变更。Deploy Gateway 已确认没有活动租约；桌面 LAN 与公网 `/health/ready` 均返回 200。

2026-10-01 更新：Issue #71 System Dashboard 知识表首次加载状态已通过 Deploy Gateway 上线。部署提交
`51a859c2e349c51e14a4a9d50bfec6de3b2db797`（功能提交 `9565f03dc0b5a865ffa7b13ef51c1f7b8e5ff84c`），
release `da941881-eba2-4d91-aa34-74baff428470`，deployment `c27a59c5-8b32-4a4c-a5d0-b03630b94bb3`
状态 `completed`，租约已释放。无 API、数据库 schema 或生产数据变更。前端 39 项、后端 279 项测试和
TypeScript/Vite build 通过。部署后公网登录态 Chrome 44 项检查全部通过；Knowledge API/行列表 API
均为 200，Dashboard 表数和图表与目录 API 一致，无浏览器、控制台、HTTP 错误或横向溢出。五个服务
readiness 均为 200，公网 JS/CSS SHA-256 与本机构建一致。Issue #71 继续保持打开，完整 UI/UX 工作
及 `plan.md` 尚未完成。详细验收见 `docs/acceptance/issue-71-dashboard-loading-2026-10-01.md`。

2026-10-01 更新：Issue #80 过期 Work 认领状态区分已通过 Deploy Gateway 发布。代码 commit
`c16efc35c99ce7deb1b457dad35da08ca805542d`，release
`03fd828d-399d-498e-8e7d-e27049ab0da4`，deployment
`2083222b-8a1d-4615-bc99-4cd2b671ff9b`，状态 `completed`，发布租约已释放。纯前端更新，无 API
或数据库 schema 变更。全套后端 279 项、Work API 41 项、前端 39 项测试通过；TypeScript/Vite
production build、OpenAPI 无 diff 与 diff check 通过。LAN 五个 readiness 均为 200；LAN/公网 JS
与 CSS 的 SHA-256 均匹配该构建，认证配置为 200，未认证 Work API 为 401。生产只读 Work API 返回
69 项，其中 queued 20、有效 claimed 1、过期 claimed 20、completed 27、blocked 1，故活动数为 21。
随后协调器释放了自己为该 Issue 持有的 Work lease，并保持 Issue open；最新只读计数为 queued 21、
过期 claimed 20、completed 27、blocked 1，活动数仍为 21。
自动化 Chrome 验收覆盖合成过期任务和重新领取动作；部署后未重新运行登录态 Chrome 全流程，因此
Issue #80 暂保持开放。

2026-10-01 更新：Issue #71 Shell、键盘操作、Settings 与会话续期体验增量已通过 Deploy Gateway 发布。
代码 commit `296a55e98cb2ede4095ed46c3035a30dfc0bc154`，release
`c570bb5a-c455-4de8-b54f-ceebd349fa08`，deployment
`24a41dc8-b2d5-4e2c-89a3-9d2e34decd93`，状态 `completed`，发布租约已释放。此增量为前端
更新，无 API 或数据库 schema 变更。上线前 Desktop DB 快照位于
`runtime/backups/2026-10-01-issue71-final/predeploy-desktop.sqlite`，SQLite
`integrity_check` 为 `ok`。279 项后端测试、39 项前端测试、TypeScript/Vite build、OpenAPI
无 diff 与 `git diff --check` 通过。五个入口 readiness 均为 200，VM 101/104/106/107/111
当前 release symlink 均指向该 commit。LAN 与公网首页及 JS/CSS 均为 200，静态资源 SHA-256
与本地生产构建一致；公网未认证 `/api/v1/work` 返回预期 401。此次没有运行真实生产账号的完整
浏览器登录流程；#71 仍开放，尚未完成项见 `docs/acceptance/ui-refactor-2026-10-01.md`。

2026-10-01 更新：Issue #73 Forgejo claim 同步及 `assignees: null` 修复已上线。代码 commit
`b0e56a9d60061f234451c9bbeeb315b8ad838553`，Deploy Gateway release
`add884bb-6a4f-4ca1-9372-936f23cd9c08` / deployment `85480820-d168-4fac-ba61-997db8b3d828`
状态 `completed`，租约已释放。发布前 DevAgent `work.sqlite` 在线备份
`runtime/backups/2026-10-01-issue73/work-predeploy.sqlite` 的 `integrity_check` 为 `ok`，权限
`0600`；本次无数据库 schema 迁移。279 项后端测试、修改文件 Ruff、OpenAPI 导出及 diff check
通过。五个服务 readiness 全为 200，VM 101/104/106/107/111 均指向该 commit。生产
`general-agent-dev-01` 成功 claim Issue #73；Forgejo `general-agent` assignee 与 epoch 6 评论均已
核实。Issue 经生产验收后关闭，webhook 将 Work 状态推进为 completed（最终 epoch 7）。

2026-10-01 更新：Issue #71 System 概览失败反馈与重试已通过 Deploy Gateway 发布。代码 commit
`44e5d1a2b2875b122f70eb27daa9afbe6d83361f`，release `85b605ea-46ba-4a26-bcd5-26d1caa79d75`，
deployment `33a0af38-94e2-4a60-8d1a-3b6248c281e4` 状态 `completed`，租约已释放。此增量只改前端，
没有 API 或数据库 schema 变更。235 项后端测试（19 项既有临时目录清理警告）、38 项前端测试、
TypeScript/Vite production build、OpenAPI 导出及 diff check 通过。LAN 五个入口 readiness 均为 200；
桌面首页为 200，JS/CSS SHA-256 与发布构建一致，未认证 Work API 仍返回预期的 401。此次浏览器
故障/重试验收使用合成 API；未完成真实登录态浏览器验收。Issue #71 仍开放，完整原生 Forgejo Dashboard
和其余验收项仍待完成。

2026-10-01 更新：XeLibrary 知识管理 WebUI 及浏览器竞态修复已随 commit
`f1e9de3eb44795e8666ed0d0df151ba9b2de6edc` 发布；Deploy Gateway release
`f2c2446a-46cd-4c52-b3dc-779ee2041129` / deployment
`958439cf-94b6-442f-8c82-a1d075fa7643` completed，租约已释放。此版本仅更新前端，无数据库
schema migration。上线前 Web 101 的 `desktop.sqlite` 经 SQLite backup API 复制到
`runtime/backups/2026-10-01-issue72/predeploy-desktop.sqlite`，`integrity_check` 为 `ok`。
公网首页、auth config、JS/CSS 内容 hash、匿名 API 401、直连 IP 页面与 auth config 均通过；
七个服务 readiness 均为 200，真实 IP 入口 PKCE/session exchange/cookie restore/API/logout 全流程通过。
公网带登录态浏览器登录本次未测，Issue #71 保持打开；知识库 UI 与限制详见
`docs/acceptance/xelibrary-2026-10-01.md`。

2026-10-01 更新：Issue #71 桌面 Shell/System 增量已通过 Deploy Gateway 上线。代码 commit
`5a33d9342f9d65b737d16b64943ffb3cc58d6434`，release
`b42c8abe-81be-4024-9fe1-9fafdbc0f186`，deployment
`578b46c7-4f05-4eae-8b56-a1490e07e502` completed，发布租约已释放。无数据库 schema 迁移；发布前
SQLite backup API 快照 `/var/lib/xeonolab/desktop-web/desktop.sqlite`，本地副本
`runtime/backups/2026-10-01-issue71/predeploy-desktop.sqlite` 的 `integrity_check` 为 `ok`。
235 后端测试、25 前端测试、TypeScript/Vite build、Ruff、OpenAPI check 通过。公网 JS/CSS hash
与发布构建一致，公网 OIDC config/discovery 和直接 IP 完整 PKCE/session 流程通过，七个服务
readiness 均为 200。公网自动化 PKCE 在本机代理提交 Keycloak 登录表单时超时，因此完整公网登录
仍待真实浏览器路径复测；Issue #71 继续开放，XeLibrary 全量管理界面及原生 Forgejo Dashboard
尚未完成，验收细节见 `docs/acceptance/ui-refactor-2026-10-01.md`。

2026-10-01 更新：Issue #11 Qdrant `fast_text` 实现已随 commit
`c9e095108b678e98037fc123bb15dabae54ed7e7` 部署；release
`e6cb8491-dab2-4ec4-bed7-56795d195235` / deployment
`bec2b250-f25d-4312-b8f4-d23cd5b8233f` completed，lease 已释放。部署前完成
XeLibrary 停写快照校验与隔离恢复演练；234 项后端测试、Ruff、OpenAPI 检查通过，七个服务
readiness 全部通过。VM104 的真实 Qdrant 临时 collection create/upsert/query/delete acceptance
通过，公网桌面、Forgejo login 与 OIDC discovery 返回 200。XeLibrary 已接入生产 Qdrant 配置；
已认证生产 API 的正向语义查询与现有数据重建尚未验收，故 Issue #11 保持打开，证据见
`docs/acceptance/qdrant-fast-text-2026-10-01.md`。

Issue #11 发布前应用代码版本为 `313b8738c5d6198fc36e309285ca3f989d1f9301`。
完整 plan.md 的所有阶段尚未完成；该版本包含 DevAgent Issues 协作、HF-cache API/预取保护、Caddy include 同步及 Issue #48 安全空表删除 API。

2026-10-01 更新：Issue #70 每表每请求 ACL 快照与索引化 row ACL 查找已部署，代码 commit
`313b8738c5d6198fc36e309285ca3f989d1f9301`，Deploy Gateway release
`57798068-69cf-492f-a3a0-7b852be4c91c` / deployment
`94306d9d-c6d6-4495-8ab3-92df80fa11b4` completed，租约已释放。发布前短暂停止 XeLibrary 写服务，
复制 19 MiB 数据目录；6 个文件的快照校验通过，并完成隔离恢复演练。服务在快照完成后立即恢复。
部署后五个 `/health/ready` 均为 200；匿名表列表为 401；部署策略主体只读获取一页表及行均为 200；
公网桌面、Forgejo 和带正确 origin 的认证配置分别为 200。Smoke 未写生产表/行。尚未以非管理员
主体验证生产 row ACL 正向/拒绝路径，也未做生产查询负载基准；Issue #70 保持开放。

2026-10-01 更新：Issue #48 安全空表删除 API 已部署，代码 commit
`03adae446b1c233eb2c22dcf36f5aad196de3adf`，Deploy Gateway release
`98d3adb8-e3d3-4be2-8d84-1bce1e32d9a3` / deployment
`eb761639-270b-426b-b832-328e8619ec5f` completed，租约已释放。部署后五个服务入口
`/health/ready` 均为 200；桌面首页为 200；未认证表列表与随机 UUID 的空表删除请求均为 401，确认授权拦截生效且删除路由已加载。此次只读检查未创建或删除生产数据。已认证的正向管理员删除、浏览器 UI 操作与隔离 fixture 清理仍待验收，因此 Issue #48 继续保持打开。

## 访问

直接 IP 访问：`https://192.168.0.101:9443`，信任下面的本地 CA 后即可登录。

域名访问：

1. 将客户端 DNS 设置为 `192.168.0.105`，或在客户端 hosts 加入：

   ```text
   192.168.0.101 xenon.lan auth.xenon.lan library.xenon.lan tools.xenon.lan deploy.xenon.lan llm.xenon.lan
   ```

2. 信任本地 CA：仓库本机 `runtime/deploy-secrets/lan-root-ca.crt`。
   也可从 `https://192.168.0.101:9443/xeonolab-ca.crt` 取得同一公开证书；安装前核对本机副本。
3. 打开 `https://xenon.lan`，点击“登录 XeonoLab”。
   用户名 `admin`，初始密码保存在本机权限为 600 的 `runtime/deploy-secrets/admin-login.txt`。
   首次登录需要修改密码。密码不写入源代码或此文档。

旧版入口 `https://192.168.0.101` 保留；新版备用地址 `https://192.168.0.101:9443`。
IP 入口可直接登录，无需客户端 DNS；认证标准地址为 `https://192.168.0.101:9444`（公网 Tunnel 故障后回退）。
更换 issuer 后，已有会话需重新登录。域名入口继续保留。Cloudflare Tunnel 已配置，公网桌面 https://xenon.jyxxe.com；Issues 位于 /dev/。

## 服务位置

| VM | 地址 | 已上线组件 |
|---|---|---|
| Web 101 | 192.168.0.101 | React 桌面、桌面 API、Caddy HTTPS |
| XeLibrary 104 | 192.168.0.104 | `.xl`、ACL、附件、DSL、全文及精确向量搜索 |
| Docker 106 | 192.168.0.106 | Tool / LLM / Deploy Gateway |
| Embedding 107 | 192.168.0.107 | 真实 D100M CPU worker |
| Auth 109 | 192.168.0.109 | Keycloak 26.7.2 + 本地 PostgreSQL 18 |
| Network 105 | 192.168.0.105 | xenon.lan DNS |
| DevAgent 111 | 192.168.0.111 | Forgejo webhook 同步、work queue、规则 triage 与 Issue links |

API：`https://library.xenon.lan/api/v1/...`、`https://tools.xenon.lan/api/v1/tools/...`、
`https://deploy.xenon.lan/api/v1/...`、`https://llm.xenon.lan/v1/...`。
后端 8100/8102/8103/8104/8105 与 Keycloak 内部端口只允许固定服务节点，管理 SSH 保留。
LLM 和 Tool Gateway 使用不同系统用户，均不能读取部署私钥，Tool 也不能读取 provider key。

## 发布与验证

通过真实 OIDC Codex 身份注册 release，由明确授权的 policy 身份批准，再 acquire lease、
调用 Deploy Gateway。网关只运行固定 executor，受限 SSH forced-command 只激活已暂存的
指定 commit。`stage_release.py` 会在 VM104 上预先安装 XeLibrary 的 Qdrant 环境文件和受保护
API key；代码激活仍通过 Deploy Gateway 完成。各服务健康检查成功后租约释放。再次通过相同
流程重启各应用，样例数据仍保留。
最新上线 ID：`8300843b-d69a-46f1-8e94-c8ac71a3905e`；对应 release
`7779e286-5a72-424c-8b63-7e9769847865`，commit
`67bea9f81bb450afbcb7690a00006e765a99d40f`，状态 completed，租约已释放。

本次包含 Issue #26 定理数据模型与 API。上线后的只读验证中，所有五个入口
（桌面、XeLibrary、Tool Gateway、Deploy Gateway、LLM Gateway）的 `/health/ready`
均返回 200；未认证的定理列表请求返回预期的 401 `UNAUTHENTICATED`。公网桌面首页和认证配置
均返回 200。尚未使用具备正向定理权限的登录主体执行线上 CRUD/审核验收，因此不能据此认定
定理授权读写已完成生产验收。Lean 验证和 Library Agent 仍未部署，`verify`/`candidates`
继续按设计返回 503。

部署源码 commit `67bea9f81bb450afbcb7690a00006e765a99d40f` 及验收文档已由受限发布脚本
`scripts/publish_source.py --local` 同步至 Forgejo 的 `feature/platform-v1`；远端分支从
`0efab2d` 快进到 `2587cae`，未改写历史。`2587cae` 只增加本次部署/验收记录，不改变已部署代码。

- 189 项后端测试、11 项桌面测试、TypeScript/Vite production build 通过；本次改动的 Ruff 检查通过。
- Issue #69 Caddy include 已同步；Caddy active，完整 Caddyfile validate 通过。公网桌面、OIDC discovery、Forgejo 登录页及 IP 入口 smoke check 通过。
- Issue #50 Work 分页保护随本次 release 上线。直连与公网 JS SHA-256 均与发布构建一致，包含明确末页状态和重复游标停止提示；生产 Work API 的 `limit=1` 确认返回游标，下一页记录前进。未做登录态 Chrome Work 页面现场操作。
- HTTPS 证书链与 hostname 验证通过，LAN DNS 解析通过。
- 真实 Keycloak 授权码 + PKCE 登录、签名 JWT、桌面管理员 role-to-scope 校验通过。
- 未登录接口返回 401；内部 Embedding 端口拒绝操作主机直接访问。
- 两条示例知识已写入 `/示例/平台/部署验收`，真实 D100M 编码、索引、搜索通过。
- Tool Gateway 用户身份透传验证通过。
- 真实登录后的 deepseek-flash 网关调用通过；provider 模型映射已配置。首次调用遇到上游 TLS 连接错误，复测成功，外部连接稳定性仍需观察。
- 服务重新激活后数据保留，systemd 服务及防火墙均设置开机启动。

模型目录 `/opt/xeonolab/D100M-Embedding`，权重 SHA-256：
`5d8fb2e14f24af1b1f5b0701051b89f571d457823334212b44589870bf099d58`。
XeLibrary 数据在 mirror-backed `/srv/xelibrary/core/xeonolab`。发布代码与数据目录分开。

## 运维与恢复

管理节点使用现有 SSH 管理身份；应用本身不持有 PVE root token。
常用读取：`systemctl status xeonolab@xelibrary`、`journalctl -u xeonolab@embedding`。
后续应用激活仍通过 Deploy Gateway；`scripts/execute_release.py` 不携带生产 SSH 私钥，
它读取本机独立的 OAuth client credential。executor 的 commit/digest allowlist 由管理员暂存更新。
当前不支持任意 release 自动拉取、SSE 或数据库跨版本迁移；不应绕过 allowlist 热改代码。

上线备份已复制到操作主机：`runtime/backups/2026-09-30/`，含 XeLibrary facts、桌面 DB、
网关 DB 和 Keycloak pg_dump。SQLite 备份使用 backup API；解包后的 integrity_check 通过，
Keycloak dump 可被 pg_restore 读取目录。模型原始权重仍位于操作主机原提供目录。
该备份尚未设置定期自动化或异地存储；多个 `.xl` 与 catalogue 的快照不是全局原子快照。
恢复时停写、恢复 catalogue/.xl/objects/ACL、校验一致性，再重建索引。

尚未上线：Qwen worker、Library Agent、OpenWebUI 和完整监控。HF-cache API 与预取水位保护代码已部署，
但物理池 telemetry collector/receiver 尚未完成生产接线；PVE 池映射、VM103 接收、防火墙和真实下载验收仍待完成。
这些功能不能从当前部署成功推断为已完成。

## Issue #75 Dock release (2026-10-01)

Actual merge commit `01ed6911294f803a12b9f7b85a9649ab8b903739`, release
`87f3d9b2-43d9-43b1-9aaa-72c3b8436edd`, deployment
`fd3dee47-4c3b-462e-9a34-48f55efd1b55`: completed; exclusive lease inactive.
Five VM current links, LAN/IP/public readiness and LAN/public HTML/JS/CSS match
were verified. Auth config was 200; anonymous tables were 401 with request IDs.
Root authenticated public Chrome confirmed normal 420ms Dock reveal across the
90ms step path, retained hover, hide on leave, fast re-entry, keyboard/reduced
motion and mobile-touch viewport behavior. Original QA preferences/layout were
restored and verified, then temporary QA user/config/NSS trust were removed.
Issue #75 closed; details: `docs/acceptance/issue-75-dock-2026-10-01.md`.
The subsequent #82 changes are CI/test-only; application build assets remain
identical. No separate application activation is implied by a CI source push.
