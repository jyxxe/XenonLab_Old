# 只读运维巡检（ops audit）

`scripts/ops_audit.py` 提供一条**只读**命令，汇总维护循环每轮需要看的信息，避免逐台手工 SSH：

- 根分区使用率与可用空间（阈值：≥90% 或 <5 GiB 报 finding）；
- `systemctl --failed`（默认容忍 `openipmi.service`，其余报 finding）；
- `/opt/xeonolab/releases` 数量（默认保留 4：current + 3 回滚窗口，超出报 finding）；
- `systemctl list-timers` 中 `xeonolab|devagent|backup` 相关定时器；
- `/var/backups/xeonolab` 最新快照路径与年龄（>192h 或无快照报 finding）；
- 24h `journalctl -p err` 的 top 来源（帮助识别 agetty 之类噪声：issue #391）；
- 指定 TLS 端点的证书剩余天数（默认只报告已过期；Caddy 内部证书为短周期自动轮换，0 天通常正常，需要提前预警时用 `--min-cert-days 7`）。

不写入目标、不读取任何密钥内容；SSH 只执行固定命令串，使用调用者提供的 key/known_hosts。

## 用法（DevAgent 主机）

```sh
uv run python scripts/ops_audit.py \
  --hosts 192.168.0.100,192.168.0.101,192.168.0.103,192.168.0.104,192.168.0.105,192.168.0.106,192.168.0.107,192.168.0.109,192.168.0.110,192.168.0.111 \
  --key /tmp/devagent-admin/vm-key \
  --known-hosts /tmp/devagent-admin/known_hosts \
  --cert 192.168.0.101:9443 --cert 192.168.0.101:9444 --cert 192.168.0.101:9445 \
  --ca-file /etc/devagent/lan-root-ca.crt
```

- 退出码 0 = 没有 finding；1 = 至少一条 finding（适合 cron/维护循环门禁使用）。
- `--json` 输出完整报告（reports/certificates/findings）供脚本消费。
- 阈值可按需覆盖：`--min-free-mib`、`--max-used-percent`、`--keep-releases`、
  `--max-backup-age-hours`、`--min-cert-days（默认 1）`、`--allow-failed`（可重复）。

## 解释与处置

| Finding | 处置 |
| --- | --- |
| 磁盘 ≥90% 或 <5 GiB | 先看 `/opt/xeonolab/releases`；`systemctl start xeonolab-release-prune.service`；仍不足则按 `deployment-disk-retention.md` 人工 dry-run + apply |
| `failed units`（非 openipmi） | 查 `journalctl -u <unit> -n 50`；确认是否遗留服务（如 `postgresql@18-main`，见 #391） |
| release 数 > 4 | 自动保留 timer 未运行/失败；按 runbook 检查 `xeonolab-release-prune.timer` |
| 无快照 / 快照过旧 | 备份缺口是 #390；当前为手工 quiesced 快照，先记录年龄，不做未验证的自动备份 |
| err 日志 top 源为 agetty | 已知噪声（#391），不作为故障；关注其余来源 |
| 证书已过期 | Caddy 内部证书为短周期自动轮换；若过期或轮换失败，查 Caddy 日志与系统时间 |

## 注意

- 该命令假设目标上 `xeonolab` 数据在 `/srv/xelibrary/core`（仅 VM104），无数据的节点不报备份 finding 以外的问题；
- 备份检查只看 `/var/backups/xeonolab` 是否存在快照及其 mtime，不代表快照可恢复（恢复演练见 #390）；
- 巡检不替代部署后的 5 目标/readiness/网关验证。
