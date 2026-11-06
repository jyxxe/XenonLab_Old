# 数据盘内容快照与恢复演练（VM100/101/106/110/111）

多台 VM 把持久数据放在独立的 PVE 数据盘上，并在 guest 内挂载：

| 主机 | 挂载点 | 实测内容 | 策略 |
| --- | --- | --- | --- |
| VM100 openwebui | `/srv/openwebui` | Open WebUI 生产数据（SQLite 会话/聊天、上传、模型配置，约 7.7 MiB） | **stop-copy**（见下） |
| VM101 web-server | `/srv/control` | caddy-config/data、postgresql 遗留目录、cutover 等（约 113 MiB） | 在线 tar |
| VM106 docker-server | `/srv/docker` | 空（约 20 KiB） | 在线 tar |
| VM110 dev | `/srv/dev` | public-source、archives、bug-reports、workspaces（约 654 MiB） | 在线 tar |
| VM111 dev-agent | `/srv/dev-agent` | 空（约 20 KiB） | 在线 tar |

**Open WebUI 的一致性**：`/srv/openwebui` 上运行 `xeonolab-openwebui-production.service`（rootless Podman），
数据目录含活跃 SQLite 数据库。wrapper 在快照期间 `systemctl stop` 该服务，并由 `EXIT/INT/TERM` trap
**始终重启**，因此每天 04:45 UTC 窗口内聊天服务会有数秒不可用（低峰时段；服务启动失败会打印到 journal）。
其它数据根仍是在线 tar（best-effort，不同文件间不保证事务一致）。

这些盘的虚拟容量为 200 GiB–8 TiB，无法用 VM 级 `vzdump` 覆盖；实际内容很小，因此按**目录 tar 快照**
纳入每日校验备份（#390）。

## 快照策略

- `xeonolab-data-backup.timer` 每日 04:45 UTC（随机 ≤20min，`Persistent`）触发；
- wrapper 按 hostname 选择数据根（openwebui→`/srv/openwebui`、web-server→`/srv/control`、
  docker-server→`/srv/docker`、dev→`/srv/dev`、dev-agent→`/srv/dev-agent`），其它主机直接 no-op；
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

### 非发布目标（VM110 dev / Forgejo、VM100 openwebui）

VM110 与 VM100 不是发布目标，没有 `/opt/xeonolab/current`；VM110 有最大的非空数据根 `/srv/dev`（约 654 MiB），
VM100 有 Open WebUI 生产数据 `/srv/openwebui`（约 7.7 MiB）。wrapper 支持 standalone 安装：
从任一已部署 release 树把工具复制到固定路径，units 也从该 release 安装（把 `SRC` 指向该 release 树）：

```sh
# 在 VM110 上（root 或 sudo -n），SRC 指向任一已部署 release 的路径，例如先 scp 到 /tmp/data-backup-src
SRC=/tmp/data-backup-src
sudo -n install -d -m 755 /usr/local/libexec/xeonolab-data-backup/xeonolab
sudo -n install -d -m 755 /usr/local/libexec/xeonolab-data-backup/scripts
sudo -n install -m 644 "$SRC/xeonolab/__init__.py" /usr/local/libexec/xeonolab-data-backup/xeonolab/
sudo -n install -m 644 "$SRC/xeonolab/data_backup.py" /usr/local/libexec/xeonolab-data-backup/xeonolab/
sudo -n install -m 644 "$SRC/scripts/data_backup_online.py" /usr/local/libexec/xeonolab-data-backup/scripts/
sudo -n install -m 755 "$SRC/infra/systemd/data_backup.sh" /usr/local/libexec/xeonolab-data-backup.sh
sudo -n install -m 644 "$SRC/infra/systemd/xeonolab-data-backup.service" /etc/systemd/system/
sudo -n install -m 644 "$SRC/infra/systemd/xeonolab-data-backup.timer" /etc/systemd/system/
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now xeonolab-data-backup.timer
```

wrapper 优先使用 release 树工具，缺失时回退到上述 standalone 树（系统 `python3`，仅标准库依赖）。

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

## 运行中快照目录

备份进行中会在域目录下创建 `.data-dump-*` 临时目录（写完后原子改名为 `data-<UTCstamp>`）。
用 `find` 选“最新快照”时必须排除隐藏目录（`-name '[!.]*'`），否则会把半成品当快照而误报校验失败；
`scripts/backup_verify.py` 与 `scripts/ops_audit.py` 均已按此过滤。2026-11-06 夜间备份窗口曾出现该误判，
已修复并加契约测试。

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
