# Forgejo 数据快照与恢复演练（VM110）

VM110 上的 Forgejo 承载本项目的仓库托管，此前**没有定时备份**（issue #390）。
本 runbook 描述每日 stop-copy-start 快照、校验与恢复演练。

## 快照内容与策略

- 数据根：`/var/lib/forgejo`（SQLite `data/forgejo.db`、`repositories/`、附件等），
  当前约 110 MB；配置 `/etc/forgejo/app.ini`。
- 快照目录：`/var/backups/xeonolab/forgejo/forgejo-<UTC stamp>Z/`
  - `forgejo/`：数据根副本（排除 `log/`）、`app.ini`；
  - `manifest.json` + `manifest.sha256`：每个文件 path/size/SHA-256；
- 一致性方式：快照期间短暂停止 `forgejo.service`（当前数据量下 stop-copy-start 为秒级），
  即使复制失败也会在 finally 中重新启动服务；
- 校验（快照成功后立即执行）：每个 SQLite 库 `PRAGMA integrity_check`、每个
  `repositories/*/*.git` 运行 `git fsck --no-progress`；
- 保留：最新 **7** 份（`FORGEJO_BACKUP_KEEP`），只修剪 `forgejo-<stamp>Z` 目录。

**限制**：同机本地副本，不是 offsite 备份；只覆盖 Forgejo 数据根。数据库/附件/repos 的
一致性由上述校验保证，但快照期间的短停机会让在线用户看到几秒不可用。

## 安装/更新（VM110 不是发布目标，由 operator 执行）

从仓库工作树把文件复制到 VM110（示例）：

```sh
scp infra/systemd/forgejo_backup.py  xenon@192.168.0.110:/tmp/
scp infra/systemd/forgejo_backup.sh  xenon@192.168.0.110:/tmp/xelibrary_forgejo_backup.sh
scp infra/systemd/xeonolab-forgejo-backup.service xenon@192.168.0.110:/tmp/
scp infra/systemd/xeonolab-forgejo-backup.timer   xenon@192.168.0.110:/tmp/
ssh xenon@192.168.0.110 '
  sudo -n install -m 755 /tmp/forgejo_backup.py /usr/local/libexec/xeonolab-forgejo-backup
  sudo -n install -m 755 /tmp/xelibrary_forgejo_backup.sh /usr/local/libexec/xeonolab-forgejo-backup.sh
  sudo -n install -m 644 /tmp/xeonolab-forgejo-backup.service /etc/systemd/system/
  sudo -n install -m 644 /tmp/xeonolab-forgejo-backup.timer /etc/systemd/system/
  sudo -n systemctl daemon-reload
  sudo -n systemctl enable --now xeonolab-forgejo-backup.timer
'
```

## 运行与验证

```sh
# 立即执行一次（会短暂停止/启动 forgejo）
sudo -n systemctl start xeonolab-forgejo-backup.service
sudo -n journalctl -u xeonolab-forgejo-backup.service -n 20 --no-pager
sudo -n find /var/backups/xeonolab/forgejo -maxdepth 1 -mindepth 1 -type d
# 独立校验最新快照
sudo -n /usr/bin/python3 /usr/local/libexec/xeonolab-forgejo-backup verify \
  --dir /var/backups/xeonolab/forgejo/<forgejo-stamp>
# timer
sudo -n systemctl list-timers xeonolab-forgejo-backup.timer --no-pager
# Forgejo 恢复在线
curl -sk --cacert /etc/devagent/lan-root-ca.crt -o /dev/null -w '%{http_code}\n' https://192.168.0.101:9445/api/v1/version
```

预期：journal 输出 `snapshot ok: ... (attempt 1, 1 db, N repos, pruned 0)`；verify 输出
`verified ...: 1 db, N repos`；Forgejo API 200。

## 恢复演练（不覆盖生产）

```sh
sudo -n install -d -m 700 /var/tmp/forgejo-restore-drill
sudo -n cp -a /var/backups/xeonolab/forgejo/<forgejo-stamp>/. /var/tmp/forgejo-restore-drill/
# 校验恢复副本
sudo -n /usr/bin/python3 /usr/local/libexec/xeonolab-forgejo-backup verify --dir /var/tmp/forgejo-restore-drill
# 如需实际启动验证：在隔离端口/目录启动 forgejo（不要指向生产数据根）后核对仓库列表
sudo -n rm -rf /var/tmp/forgejo-restore-drill
```

## 剩余风险（属 #390 范围）

- 快照与源同机、同盘；主机/磁盘级故障不可恢复，offsite/独立存储未实现。
- 未做完整恢复演练（真实启动 Forgejo 读取恢复库）→ 首次演练记录应回填 #390。
- Keycloak PostgreSQL、桌面库仍无定时备份。
