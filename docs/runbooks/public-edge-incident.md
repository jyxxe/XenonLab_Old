# 公网入口故障处置手册（xenon.jyxxe.com / Tunnel）

面向值班维护者：公网桌面、Forgejo、认证等入口经 Cloudflare Tunnel（VM101 `cloudflared`）暴露。
本手册给出**先判定再动手**的流程，避免把账号侧/上游链路问题误当成隧道或应用故障。

相关文档：`monitoring.md`（probe/连接器诊断、升级与回滚）、`cloudflare-caddy-include.md`（边缘路由
include 的发布/回滚）、`ops-audit.md`（`dev-agent` 监控自检与 cloudflared finding）。

## 1. 三分钟判定（全部只读）

```sh
# LAN 是否正常（绕过公网边缘）
curl -sk -o /dev/null -w 'lan %{http_code} %{time_total}s\n' https://192.168.0.101/
# 公网是否正常
curl -s -o /dev/null -w 'public %{http_code} %{time_total}s\n' https://xenon.jyxxe.com/
# 维护主机采样（近 3 小时；LAN/公网/两个 chat 目标）
cd /var/lib/devagent/checkout && uv run python scripts/public_probe_report.py --since-hours 3
# 连接器：service/HA/自启动以来掉线
ssh xenon@192.168.0.101 'systemctl is-active cloudflared; /usr/bin/cloudflared --version; \
  sudo journalctl -u cloudflared --since "$(systemctl show -p ActiveEnterTimestamp --value cloudflared.service)" \
  | grep -c "Lost connection with the edge"'
ssh xenon@192.168.0.101 'sudo curl -s --max-time 5 http://127.0.0.1:20241/metrics | grep -E "^cloudflared_tunnel_ha_connections|^cloudflared_tunnel_request_errors"'
```

判定分支：

| LAN | 公网 | 连接器 | 结论方向 |
| --- | --- | --- | --- |
| 正常 | 正常 | 正常 | 无故障；若用户仍报错，索取其 URL/时间并对照 probe 日志 |
| 正常 | 失败 | 正常（HA=4、0 自启动以来掉线） | **上游/边缘路径或账号侧**，见 §2；不要重启隧道 |
| 正常 | 失败 | service 非 active / HA=0 | 连接器故障，见 §3 |
| 失败 | 失败 | 任意 | 源站/内网问题，见 §4（先查 Caddy/应用/gateway） |

## 2. 公网失败但 LAN 与连接器正常

按症状细分：

1. **全公网 530（含 `chat.jyxxe.com` 530）**：这是 Cloudflare 侧没有可用边缘路由/记录（DNS 记录、
   tunnel 路由或账户状态），不是本机服务故障。用只读命令确认无 530 之外的错误后，**转账号侧处理**
   （在 Cloudflare 控制台检查 DNS 记录、Tunnel 路由/配置；`chat.*` 子域的证书/记录问题同属账号侧）。
   不要重装/替换 tunnel，不要改 `/etc/cloudflared`。
2. **间歇 TLS handshake/read timeout，但 LAN 100%、连接器 0 掉线**：2026-11-06 实测模式——07:00–13:00Z
   100%，14:00–18:50Z 降到 43%–83%（失败均为握手/读超时，非 530），19:00Z 后恢复 100%。当时
   两个 A 记录单独 `--resolve` 均 200、IPv4 connect 0.19s、VM111 无全局 IPv6；Cloudflare 状态页有
   区域/POP 降级但无对应 incident。**归因上游 WAN/边缘路径丢包**，处置是收集证据并持续观察，不在本机改网络；
   若持续 >24h 或与 Cloudflare incident 对齐，再升级 WAN/套餐侧。
3. **证书/域名错误（如 `chat.xenon.jyxxe.com` handshake failure）**：DNS 记录指向不可用隧道或区域证书
   未覆盖该名称，属账号侧配置；记录到 #126/#284，不伪造本地证书。

证据采集（贴 Issue 时附上）：

```sh
# 逐小时成功率
cd /var/lib/devagent/checkout && uv run python scripts/public_probe_report.py --since-hours 24
# 两个 A 记录分别探测
for ip in $(getent ahostsv4 xenon.jyxxe.com | awk '{print $1}' | sort -u); do
  curl -sk -o /dev/null -w "$ip %{http_code} %{time_total}s\n" --max-time 12 --resolve "xenon.jyxxe.com:443:$ip" https://xenon.jyxxe.com/
done
# 连接器自身错误（不应出现 dial/deadline 类）
ssh xenon@192.168.0.101 'sudo journalctl -u cloudflared --since "6 hours ago" | grep -iE "context deadline|failed to dial|error proxying" | tail -20'
```

## 3. 连接器故障（service 非 active 或 HA=0）

1. `systemctl status cloudflared`、`journalctl -u cloudflared -n 100`：token/证书/网络错误；
2. 确认 token 文件（`/etc/cloudflared/tunnel-token`）未被修改、权限正确；**不要把 token 打印到聊天/Issue**；
3. `systemctl restart cloudflared`，等 10s 后复核 `ha_connections=4` 与公网 200；
4. 若新版本引入问题：按 `monitoring.md` 的 2026-11-06 升级记录回滚（旧二进制在
   `/var/backups/cloudflared/cloudflared-2026.9.3`，SHA256 `77e26d8d…`），再重启并复核；
5. 记录 service/版本/HA/自启动以来掉线数与 probe 结果到 #67。

## 4. 源站/内网问题（LAN 也失败）

1. VM101：`systemctl status caddy`、`caddy validate`、`journalctl -u caddy`；应用 readiness
   （桌面 9443、Forgejo 9445、认证 9444）逐项 curl LAN；
2. 子服务（VM104/109/110/107/106/111）各自 unit/health；必要时用 `ops_audit` 全量巡检；
3. 修复后按 Deployment Gateway 正常发布路径处理（不得绕过）。

## 5. 边界

- 不得替换/重装 tunnel、修改 token、改 DNS 记录或账户设置，除非任务明确授权且有回滚；
- 不得移除分支保护、伪造检查或清理未确认结束的部署租约；
- 不在 Git/Issue/PR/日志/聊天中输出 token、密钥或完整凭据；
- 所有诊断优先只读，写入操作（restart/rollback）须有前后验证与记录。

## 6. 事后

- 在对应 Issue（#67/#126/#284）与 `admin-runlog.md` 记录：时间窗、LAN/公网成功率、连接器指标、
  证据命令输出、处置与结论；若新增可复现模式，补进本手册与 `monitoring.md`。
