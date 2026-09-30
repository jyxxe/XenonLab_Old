# PVE LAN 上线记录 — 2026-09-30

当前可运行版本已上线。应用代码版本 `8e048cb83e1107a71689a2e139f93526898e259d`。
完整 plan.md 的所有阶段尚未完成；本次上线范围是已有实现的桌面、XeLibrary、D100M 和网关。

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
IP 入口可直接登录，无需客户端 DNS；认证标准地址为 `https://192.168.0.101:9444`。
更换 issuer 后，已有会话需重新登录。域名入口继续保留。未配置 Cloudflare Tunnel。

## 服务位置

| VM | 地址 | 已上线组件 |
|---|---|---|
| Web 101 | 192.168.0.101 | React 桌面、桌面 API、Caddy HTTPS |
| XeLibrary 104 | 192.168.0.104 | `.xl`、ACL、附件、DSL、全文及精确向量搜索 |
| Docker 106 | 192.168.0.106 | Tool / LLM / Deploy Gateway |
| Embedding 107 | 192.168.0.107 | 真实 D100M CPU worker |
| Auth 109 | 192.168.0.109 | Keycloak 26.7.2 + 本地 PostgreSQL 18 |
| Network 105 | 192.168.0.105 | xenon.lan DNS |

API：`https://library.xenon.lan/api/v1/...`、`https://tools.xenon.lan/api/v1/tools/...`、
`https://deploy.xenon.lan/api/v1/...`、`https://llm.xenon.lan/v1/...`。
后端 8100/8102/8103/8104/8105 与 Keycloak 内部端口只允许固定服务节点，管理 SSH 保留。
LLM 和 Tool Gateway 使用不同系统用户，均不能读取部署私钥，Tool 也不能读取 provider key。

## 发布与验证

通过真实 OIDC Codex 身份注册 release，由明确授权的 policy 身份批准，再 acquire lease、
调用 Deploy Gateway。网关只运行固定 executor，受限 SSH forced-command 只激活已暂存的
指定 commit。各服务健康检查成功后租约释放。再次通过相同流程重启各应用，样例数据仍保留。
最新上线 ID：`0a7fc6c0-95ce-48fc-ae48-8910026b5aef`；对应 release
`902e126c-e916-47fd-a774-bc4133a2465f`，状态 completed，租约已释放。

- 16 项代码测试通过，TypeScript/React production build 通过。
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

尚未上线：Qwen worker、HF-cache、Library Agent、DevAgent triage、OpenWebUI、Forgejo 和完整监控。
这些功能不能从当前部署成功推断为已完成。
