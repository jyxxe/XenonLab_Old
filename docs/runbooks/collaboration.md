# Issues 与多 Harness 协作

Forgejo 16.0.5 部署在 VM 110，以非 root 用户运行，独立持久化数据位于 /var/lib/forgejo。
二进制已与官方发布 SHA-256 核对。临时入口：https://192.168.0.101:9445。
正式入口为 https://xenon.jyxxe.com/dev/，使用单域名子路径。认证入口 /auth/。

仓库：/xenon-admin/XeonoLab，源码公开。Issues 页面包含评论、标签、指派、里程碑、关联与状态管理。
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
