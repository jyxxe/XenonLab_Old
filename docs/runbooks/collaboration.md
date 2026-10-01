# Issues 与多 Harness 协作

Forgejo 16.0.5 部署在 VM 110，以非 root 用户运行，独立持久化数据位于 /var/lib/forgejo。
二进制已与官方发布 SHA-256 核对。临时入口：https://192.168.0.101:9445。
正式入口为 https://xenon.jyxxe.com/dev/，使用单域名子路径。认证入口 /auth/。

仓库：/xenon-admin/XeonoLab，源码公开，默认显示 feature/platform-v1 完整实现。main 禁止直接 push，要求一次审批。
已建立 plan.md §100 的 30 个首批任务，保持打开直到逐项验证；另有协作规范 Issue。Issues 页面包含评论、标签、指派、里程碑、关联与状态管理。
API 根路径 /api/v1；Swagger 位于 /api/swagger。仓库 Issue 接口：
GET/POST /api/v1/repos/xenon-admin/XeonoLab/issues，单条 PATCH /issues/{index}。

管理员 xenon-admin；Harness 账户 codex、opencode、chatgpt-web、zcode、general-agent 各自使用独立
Forgejo 密码。Harness 仅 `xenon-admin/XeonoLab` 仓库 write，不能访问站点管理员 API。凭据存于
操作主机 `runtime/deploy-secrets/forgejo-users.json`（权限 600）。
Forgejo 本地账户与 Keycloak OIDC subject 独立；通过各 Harness 的私有 `Agent.md` 分别配置两种身份。
不得将管理员凭据交给 Harness；不得提交任何凭据。生产激活仍通过受限 Deploy Gateway。

桌面已上线“身份与权限”：查看当前 JWT subject/kind/scopes；管理员可读取和编辑路径、表、行 ACL，
查看审计 API。只有带 xelibrary:admin 的主体可访问这些管理接口。空显式 ACL 拒绝非管理员，
资源 ACL 依 row → table → path 优先级；恢复继承的 ACL 删除接口尚待实现。

Cloudflare connector 位于 VM 105，已建立连接。Token 仅存 /etc/cloudflared/tunnel-token，权限 600。
公网 hostname xenon.jyxxe.com 与 *.xenon.jyxxe.com 的 Service： http://192.168.0.101:8080。
保留 Host，origin 不使用 TLS；该监听仅接受 VM 105 来源。单域名 Tunnel 路由已通过公网健康检查；origin 按 /auth 与 /dev 分发。
公网 OIDC issuer 切换必须与 callback、iframe origin 和服务端 JWT verifier 一起验证，不能仅改 DNS。

## 网络稳定性记录

公网真实 PKCE 登录与 Forgejo version 曾通过；随后 Cloudflare 所有 QUIC 连接超时，公网出现 1033。
已改用 HTTP/2，但 TCP 7844 TLS 握手仍超时。VM 105 UFW inactive，API 443 可达；
公网可靠性尚未验收，需继续排查外部出口对 Tunnel 7844 的可达性。
不要把曾成功的测试视为持续在线证据。canonical issuer 现为公网 /auth，登录也依赖该连接。

为恢复内网登录，已回退 canonical issuer 到 IP 9444；公网 /auth 配置保留，
待 Tunnel 连通稳定后同步切换，不将当前公网登录视为可用。Forgejo IP 9445 仍可管理。

Issue #68 local implementation now supports explicit per-desktop-origin public,
LAN and IP issuers while retaining exact JWT issuer checks and issuer-pinned
server sessions. Keycloak multi-host config and exact callback URIs are prepared
by the bootstrap generator. This code/config has not been applied or accepted in
production; see `docs/runbooks/oidc-public-issuer.md` for operator prerequisites,
staged verification and rollback.

## DevAgent work queue 上线

VM 111 提供 DevAgent 8106，仅 VM 101 代理与本机可访问，防火墙设置开机应用。
桌面“任务队列”展示 30 个真实 Forgejo Plan Issues，可领取、释放、标记队列完成。
队列完成不自动关闭 Forgejo Issue；Issue 关闭仍需要实际验收证据。

独立 OAuth client `codex-dev-01`、`opencode-01`、`chatgpt-web-dev-01`、`zcode-dev-01`、
`general-agent-dev-01` 使用 `client_credentials` 和 audience `xeonolab`，权限为
`dev:read dev:write desktop:read`，不含任何 deploy scope。各 client secret 存于操作主机
`runtime/deploy-secrets/harness-clients.json`（600），不得共用或提交。ChatGPT Web、Zcode、
General Agent 的私有说明和各自账号凭据位于 `runtime/agent-profiles/<harness>/Agent.md`，目录权限
700、文件权限 600；只读取当前 Harness 的文件。这些 OIDC 身份与 Forgejo 本地账户没有自动绑定。
Keycloak `client_credentials` token 请求应省略 `scope` 参数；`dev:read`、`dev:write` 与
`desktop:read` 是客户端的默认平台授权，不是可直接请求的 Keycloak scope。额外传入这些值会得到
`invalid_scope`。Access token 当前有效期约 300 秒，应在过期后重新获取。

经过桌面入口访问以下接口，Authorization Bearer 必须携带对应 OIDC access token：

- GET/POST /api/v1/work
- POST /api/v1/work/{id}/claim，body ttl_seconds（30–3600）
- POST /api/v1/work/{id}/renew，body epoch、ttl_seconds（30–3600）
- POST /api/v1/work/{id}/release，body epoch、completed
- GET/POST /api/v1/issues/{issue_id}/links，repository 与目标 issue/type
- POST /api/v1/issues/{issue_id}/triage，body repository；返回规则化建议
  labels/priority/duplicates/related/recommended_agent（只读 work.sqlite，不调用 LLM，不写 Forgejo）

领取原子互斥、带 TTL 和递增 epoch；续租仅当前 holder、当前 epoch 且未过期时成功，且不改变
epoch；过期或被重新认领后 renew/release/complete 一律拒绝，持有者必须停止写入并重新 claim
（epoch+1），旧 epoch 不再有效。

## 新 Harness 身份与并发认领验收（2026-10-01）

- ChatGPT Web 使用 Forgejo `chatgpt-web` 与 OIDC `chatgpt-web-dev-01`；Zcode 使用 `zcode` 与 `zcode-dev-01`；General Agent 使用 `general-agent` 与 `general-agent-dev-01`。
- 三个账号都能通过 Forgejo REST 完成身份认证，并且对 `xenon-admin/XeonoLab` 只有仓库写权限、没有站点管理员权限。三个 OIDC 身份均能访问平台 Work API，实际 platform scopes 为 `dev:read`、`dev:write`、`desktop:read`，均无 deploy scope；独立 subject 已验证。
- 私有 `Agent.md` 指引分别保存在本机 `runtime/agent-profiles/{chatgpt-web,zcode,general-agent}/Agent.md`；目录权限 `700`、文件权限 `600`，由 Git 忽略。文件包含各自 Forgejo 密码和 OIDC client secret，不应复制到仓库、Issue、日志或其他 Harness。
- 真实 LAN API 并发验收用临时 Forgejo Issue #79：ChatGPT Web 与 Zcode 同时 claim，结果恰为一个 `200`、一个 `409 WORK_CLAIMED`；非持有者 release 得到 `409`；释放后 General Agent 以递增 epoch 领取并完成。临时 Issue 已关闭，Work item 已完成；没有创建 release 或取得部署租约。
- Issue #73 仍开放：当前 Work claim API 不会自动同步 Forgejo assignee 或写领取评论；该联动与后续验收仍待实现。

Issue 关联以 repository 为命名空间；production 默认仅 xenon-admin/XeonoLab；
自关联和 duplicate_of 环路被拒绝。元数据持久化到 work.sqlite，独立于 Forgejo facts。
Forgejo 签名 webhook 已同步 Issue 状态与 work queue；自动评论/标签联动仍待实现。
规则化 triage 端点已由 OpenCode 交付并合并（PR #42）；LLM 语义与 Forgejo 联动仍未实现。

发布 commit c1effc8d463522084dae818e1742d06890442116；deployment
8460739e-1805-4c1a-87b7-297e9786b034 completed，lease 已释放。
22 项测试、TypeScript build、真实双身份并发与 holder 检查通过。

## 真实 webhook 验收

2026-09-30 已上线 commit cccf63b5565127f8cd7306c0f4ce436fbfeee4f3，deployment
64f0a9d1-2d85-4c0a-8cfb-d53c0392ede7 completed，lease 已释放。
Forgejo Issue #33 为实际联调记录，创建自动入队、关闭撤销 claim、重开入队、最终关闭通过。
仅 issues 事件驱动同步，其余事件仅记录；ALLOWED_HOST_LIST 固定 192.168.0.101，
webhook HTTP 8081 仅接受 Forgejo 110 来源，服务再次校验签名。Secret 以私密文件提供。
仍需完善同一 updated_at 的冲突事件处理与主动 reconcile，不能以此次常规路径验收推断全部故障恢复完成。
