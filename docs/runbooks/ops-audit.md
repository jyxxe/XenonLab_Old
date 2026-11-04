# 只读运维巡检（ops audit）

`scripts/ops_audit.py` 提供一条**只读**命令，汇总维护循环每轮需要看的信息，避免逐台手工 SSH：

- 根分区使用率与可用空间（阈值：≥90% 或 <5 GiB 报 finding）；
- `systemctl --failed`（默认容忍 `openipmi.service`，其余报 finding）；
- `/opt/xeonolab/releases` 数量（默认保留 4：current + 3 回滚窗口；容忍部署到每日 prune 之间的 2 个瞬时增量，超过 keep+2 才报 finding）；
- `systemctl list-timers` 中 `xeonolab|devagent|backup` 相关定时器；
- 五个定时备份域（`xelibrary`/`forgejo`/`keycloak`/`desktop`/`data`）各自的最新快照与年龄（默认 >36h 或目录存在但无快照报 finding）；`data` 是数据盘内容 tar 快照（VM101/106/110/111，见 `data-disk-backup.md`）；其它主机上的一次性手工归档目录只进 JSON 报告，不报 finding；
- 上述备份域的 systemd timer/service 状态（timer 未 active、service 上次 run failed 报 finding）；
- 24h `journalctl -p err` 的 top 来源（帮助识别 agetty 之类噪声：issue #391）；
- 指定 TLS 端点的证书剩余天数（默认只报告已过期；Caddy 内部证书为短周期（12h）自动轮换，0 天通常正常，需要提前预警时用 `--min-cert-days 7`）；
- （可选）PVE 的 VM 级 vzdump 巡检：传入 `--pve-token-file` 时，只读查询 `GET /api2/json/cluster/backup` 与备份存储内容，报告任务被禁用、某 VM 无归档、最新归档超过 `--max-vm-backup-age-hours`（默认 192h）。token 只放 HTTP 头、不会写入输出；自签证书需显式 `--pve-insecure` 或 `--pve-ca-file`。

  运维循环可以把 PVE API token（`tokenid=secret` 一行）放在 DevAgent 主机的私密文件里，避免每轮重新处理密钥：

  ```sh
  # 一次写入（0600，仅 devagent 可读），之后每轮只读使用
  install -m 600 -D /dev/stdin ~/.config/devagent/pve-api-token <<'TOKEN'
  root@pam!<token-id>=<secret>
  TOKEN

  uv run python scripts/ops_audit.py \
    --pve-token-file ~/.config/devagent/pve-api-token --pve-insecure \
    --hosts 192.168.0.101,192.168.0.104,192.168.0.106,192.168.0.107,192.168.0.109,192.168.0.110,192.168.0.111 \
    --key /tmp/devagent-admin/vm-key --known-hosts /tmp/devagent-admin/known_hosts
  ```

  首次定时 vzdump（每周六 19:00 UTC）运行前，VM105/107/101/110/111 会按预期报「归档过旧」；运行后才应转为新鲜。

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
- `--json` 输出完整报告（reports/certificates/pve_backups/findings）供脚本消费。
- 阈值可按需覆盖：`--min-free-mib`（默认 5120）、`--max-used-percent`（90）、
  `--keep-releases`（4）、`--release-tolerance`（2）、`--max-backup-age-hours`（36）、
  `--min-cert-days`（0）、`--allow-failed`（可重复，默认 `openipmi.service`）。

## 解释与处置

| Finding | 处置 |
| --- | --- |
| 磁盘 ≥90% 或 <5 GiB | 先看 `/opt/xeonolab/releases`；`systemctl start xeonolab-release-prune.service`；仍不足则按 `deployment-disk-retention.md` 人工 dry-run + apply |
| `failed units`（非 openipmi） | 查 `journalctl -u <unit> -n 50`；确认是否遗留服务（如 `postgresql@18-main`，见 #391） |
| release 数 > keep+2 | 自动保留 timer 未运行/失败；按 runbook 检查 `xeonolab-release-prune.timer`；若刚连续部署两次属正常，等 04:30 的每日 prune |
| 某个备份域过旧/无快照，或 timer 未 active / service 上次 failed | 查 `journalctl -u xeonolab-<domain>-backup.service -n 50`；按对应 runbook（xelibrary/forgejo/keycloak/desktop backup-restore、data-disk-backup）修复后手动 `systemctl start` 一次；这是 #390 的备份年龄/失败巡检，不含外部推送告警 |
| 完全没有任何备份域 | 该主机可能不是数据承载节点；若应承载备份则回到 #390 的 runbook 安装对应 timer |
| PVE VM 备份 finding（disabled/无归档/过旧） | 查 PVE Web UI → Datacenter → Backup 与 `/var/log/vzdump`；任务定义见 `backup-drills.md` 的 VM 级章节 |
| err 日志 top 源为 agetty | 已知噪声（#391），不作为故障；关注其余来源 |
| 证书已过期 | Caddy 内部证书为短周期自动轮换；若过期或轮换失败，查 Caddy 日志与系统时间 |

## 注意

- 该命令假设目标上 `xeonolab` 数据在 `/srv/xelibrary/core`（仅 VM104），无数据的节点不报备份 finding 以外的问题；
- 备份检查只看各域最新快照目录名与其 mtime，不代表快照可恢复（恢复演练见对应 runbook 与 #390）；巡检本身不含外部推送告警，维护循环按 finding 处置；
- 巡检不替代部署后的 5 目标/readiness/网关验证。
