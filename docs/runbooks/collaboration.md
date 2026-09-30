# Issues 与多 Harness 协作

Forgejo 16.0.5 部署在 VM 110，以非 root 用户运行，独立持久化数据位于 /var/lib/forgejo。
二进制已与官方发布 SHA-256 核对。临时入口：https://192.168.0.101:9445。
正式入口为 https://xenon.jyxxe.com/dev/，使用单域名子路径。认证入口 /auth/。

仓库：/xenon-admin/XeonoLab，源码公开，默认显示 feature/platform-v1 完整实现。main 禁止直接 push，要求一次审批。
已建立 plan.md §100 的 30 个首批任务，保持打开直到逐项验证；另有协作规范 Issue。Issues 页面包含评论、标签、指派、里程碑、关联与状态管理。
API 根路径 /api/v1；Swagger 位于 /api/swagger。仓库 Issue 接口：
GET/POST /api/v1/repos/xenon-admin/XeonoLab/issues，单条 PATCH /issues/{index}。

管理员 xenon-admin，Harness 账户 codex、opencode 使用不同密码和身份；Harness 仅仓库 write，
不能访问站点管理员 API。首次凭据存于操作主机 runtime/deploy-secrets/forgejo-users.json，权限 600。
此阶段 Forgejo 本地身份独立于 Keycloak；OIDC 统一登录与细分 API token 后续接入。
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

## DevAgent work queue 上线

VM 111 提供 DevAgent 8106，仅 VM 101 代理与本机可访问，防火墙设置开机应用。
桌面“任务队列”展示 30 个真实 Forgejo Plan Issues，可领取、释放、标记队列完成。
队列完成不自动关闭 Forgejo Issue；Issue 关闭仍需要实际验收证据。

独立 OAuth client codex-dev-01 与 opencode-01：client_credentials、audience xeonolab，
dev:read / dev:write / desktop:read，不含任何 deploy scope。凭据只在操作主机
runtime/deploy-secrets/harness-clients.json（600），管理员分别分发，禁止共用超级 token。
这些 OIDC 身份与 Forgejo 本地账户尚未做统一登录关联。

经过桌面入口访问以下接口，Authorization Bearer 必须携带对应 OIDC access token：

- GET/POST /api/v1/work
- POST /api/v1/work/{id}/claim，body ttl_seconds（30–3600）
- POST /api/v1/work/{id}/release，body epoch、completed
- GET/POST /api/v1/issues/{issue_id}/links，repository 与目标 issue/type

领取原子互斥、带 TTL 和递增 epoch；过期持有者不能完成重新领取后的任务。
Issue 关联以 repository 为命名空间；production 默认仅 xenon-admin/XeonoLab；
自关联和 duplicate_of 环路被拒绝。元数据持久化到 work.sqlite，独立于 Forgejo facts。
Forgejo webhook 自动同步、triage、自动评论/标签联动和任务续租接口仍待实现。

发布 commit c1effc8d463522084dae818e1742d06890442116；deployment
8460739e-1805-4c1a-87b7-297e9786b034 completed，lease 已释放。
22 项测试、TypeScript build、真实双身份并发与 holder 检查通过。
