# 数据盘内容快照与恢复演练（VM101/106/110/111）

四台发布目标把持久批量数据放在独立的 PVE 数据盘上，并在 guest 内挂载：

| 主机 | 挂载点 | 实测内容 |
| --- | --- | --- |
| VM101 web-server | `/srv/control` | caddy-config/data、postgresql 遗留目录、cutover 等（约 113 MiB） |
| VM106 docker-server | `/srv/docker` | 空（约 20 KiB） |
| VM110 dev | `/srv/dev` | public-source、archives、bug-reports、workspaces（约 654 MiB） |
| VM111 dev-agent | `/srv/dev-agent` | 空（约 20 KiB） |

这些盘的虚拟容量为 200 GiB–8 TiB，无法用 VM 级 `vzdump` 覆盖；实际内容很小，因此按**目录 tar 快照**
纳入每日校验备份（#390）。

## 快照策略

- `xeonolab-data-backup.timer` 每日 04:45 UTC（随机 ≤20min，`Persistent`）触发；
- wrapper 按 hostname 选择数据根（web-server→`/srv/control`、docker-server→`/srv/docker`、
  dev→`/srv/dev`、dev-agent→`/srv/dev-agent`），其它主机直接 no-op；
- tar.gz（compresslevel 6，不做跨挂载点遍历），跳过顶层 `lost+found` 与 tar 无法安全表达的
  特殊文件（socket/fifo/device，计数记录在 manifest）；
- 快照目录 `/var/backups/xeonolab/data/data-<UTC stamp>Z/`：`data.tar.gz`（0600）+
  `manifest.json` + `manifest.sha256`（source、archive SHA-256、字节数、entry 数、skipped 特殊文件数）；
- 发布前校验：manifest 校验和 → 归档 SHA-256 → 逐条遍历 gzip/tar 流并比对 entry 数；通过后原子 rename 发布；
- 保留最新 **7** 份（`DATA_BACKUP_KEEP`），只修剪 `data-<stamp>Z` 目录。

**限制**：同机本地副本，不是 offsite；tar 是 **best-effort**：对正在写入的大量文件（例如 worktrees）
不保证跨文件事务一致性；`/srv/control/postgresql*` 等冷数据目录可被完整备份，但若数据库仍在线运行，
应优先使用数据库自身的导出而不是直接 tar 数据目录。

## 安装/验证（发布目标，operator 首次安装）

受保护部署只更新 `/opt/xeonolab/releases/<sha>` 与 `current`，**不执行** `stage.sh`；stage.sh 只为
bootstrap 提供安装块。首次/刷新按下面步骤（工具来自已部署 release 树）：

```sh
R=$(readlink -f /opt/xeonolab/current)
sudo -n install -m 755 "$R/infra/systemd/data_backup.sh" /usr/local/libexec/xeonolab-data-backup.sh
sudo -n install -m 644 "$R/infra/systemd/xeonolab-data-backup.service" /etc/systemd/system/
sudo -n install -m 644 "$R/infra/systemd/xeonolab-data-backup.timer" /etc/systemd/system/
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now xeonolab-data-backup.timer
```

## 运行与验证

```sh
sudo -n systemctl start xeonolab-data-backup.service
sudo -n journalctl -u xeonolab-data-backup.service -n 10 --no-pager
sudo -n find /var/backups/xeonolab/data -maxdepth 1 -mindepth 1 -type d
latest=$(sudo -n find /var/backups/xeonolab/data -maxdepth 1 -mindepth 1 -type d -printf '%T@ %p\n' | sort -rn | head -1 | cut -d' ' -f2-)
sudo -n /opt/xeonolab/current/.venv/bin/python \
  /opt/xeonolab/current/scripts/data_backup_online.py verify --dir "$latest"
sudo -n systemctl list-timers xeonolab-data-backup.timer --no-pager
```

预期：`snapshot ok: ... (attempt 1, N bytes, E entries, skipped S special, pruned 0)`；
verify 输出字节数/entry 数；timer 下次 04:45 UTC 左右。

## 恢复演练（隔离目录，不覆盖生产）

```sh
latest=$(sudo -n find /var/backups/xeonolab/data -maxdepth 1 -mindepth 1 -type d -printf '%T@ %p\n' | sort -rn | head -1 | cut -d' ' -f2-)
drill=/var/tmp/data-restore-drill-$(date -u +%Y%m%d%H%M%S)
sudo -n mkdir -p "$drill"
sudo -n tar -xzf "$latest/data.tar.gz" -C "$drill"
echo "restored files: $(sudo -n find "$drill" -type f | wc -l)"
echo "source files:   $(sudo -n find /srv/dev -type f ! -path '*/lost+found/*' | wc -l)"   # 按实际数据根替换
sudo -n du -sh "$drill"
# 抽查关键文件内容一致后清理
sudo -n rm -rf "$drill"
```

预期：恢复文件数与源一致（VM101 实测 2054 == 2054，恢复目录约 112 MiB vs 源 113 MiB；
特殊文件按设计跳过）。真实恢复时先停相关写入方/服务，恢复后再启动并验证。

## 与 VM 级备份的关系

本快照只覆盖**数据盘内容**；OS 盘/配置/密钥的整机备份仍取决于 PVE 层
（`docs/runbooks/backup-drills.md` 的 VM 级部分）。两者都落地前，不声称有完整灾备。
