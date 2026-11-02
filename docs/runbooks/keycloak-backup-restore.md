# Keycloak PostgreSQL 快照与恢复演练（VM109）

VM109 运行 Keycloak 容器 `xeonolab-keycloak` 与其 PostgreSQL 18 集群（主机服务，`keycloak`
数据库约 13 MB）。此前没有定时备份（issue #390）。本 runbook 描述每日 `pg_dump`、校验与恢复演练。

## 快照策略

- `pg_dump -Fc` 在**在线**数据库上事务一致，无需停 Keycloak；
- 以 `sudo -n -u postgres` peer 认证执行，备份工具**不读取/不写入任何数据库口令**；
- 快照目录 `/var/backups/xeonolab/keycloak/keycloak-<UTC stamp>Z/`：
  - `keycloak.dump`（custom format，0600）；
  - `manifest.json` + `manifest.sha256`（database、size、objects、dump SHA-256）；
- 保留最新 **7** 份（`KEYCLOAK_BACKUP_KEEP`），只修剪 `keycloak-<stamp>Z` 目录；
- 校验：`PGDMP` magic + `pg_restore -l` 可列出对象。

**限制**：同机本地副本，不是 offsite 备份；只覆盖 PostgreSQL 数据库。Keycloak 容器镜像/环境变量
（如 DB 连接、hostname、TLS 相关配置）不在快照内，升级/恢复时仍需按部署记录重建。

## 安装/更新（VM109 不是发布目标，operator 执行）

```sh
scp infra/systemd/postgres_backup.py  xenon@192.168.0.109:/tmp/
scp infra/systemd/postgres_backup.sh  xenon@192.168.0.109:/tmp/xelibrary_postgres_backup.sh
scp infra/systemd/xeonolab-postgres-backup.service xenon@192.168.0.109:/tmp/
scp infra/systemd/xeonolab-postgres-backup.timer   xenon@192.168.0.109:/tmp/
ssh xenon@192.168.0.109 '
  sudo -n install -m 755 /tmp/postgres_backup.py /usr/local/libexec/xeonolab-postgres-backup
  sudo -n install -m 755 /tmp/xelibrary_postgres_backup.sh /usr/local/libexec/xeonolab-postgres-backup.sh
  sudo -n install -m 644 /tmp/xeonolab-postgres-backup.service /etc/systemd/system/
  sudo -n install -m 644 /tmp/xeonolab-postgres-backup.timer /etc/systemd/system/
  sudo -n systemctl daemon-reload
  sudo -n systemctl enable --now xeonolab-postgres-backup.timer
'
```

## 运行与验证

```sh
sudo -n systemctl start xeonolab-postgres-backup.service
sudo -n journalctl -u xeonolab-postgres-backup.service -n 20 --no-pager
sudo -n find /var/backups/xeonolab/keycloak -maxdepth 1 -mindepth 1 -type d
latest=$(sudo -n find /var/backups/xeonolab/keycloak -maxdepth 1 -mindepth 1 -type d -printf '%T@ %p\n' | sort -rn | head -1 | cut -d' ' -f2-)
sudo -n /usr/bin/python3 /usr/local/libexec/xeonolab-postgres-backup verify --dir "$latest"
sudo -n systemctl list-timers xeonolab-postgres-backup.timer --no-pager
```

预期：`snapshot ok: ... (attempt 1, N bytes, M objects, pruned 0)`；verify 输出 size/objects；
timer 下次 04:30 UTC 左右。

## 恢复演练（只对临时库，绝不覆盖 keycloak）

```sh
latest=$(sudo -n find /var/backups/xeonolab/keycloak -maxdepth 1 -mindepth 1 -type d -printf '%T@ %p\n' | sort -rn | head -1 | cut -d' ' -f2-)
drill=keycloak_drill_$(date -u +%Y%m%d%H%M%S)
sudo -n -u postgres createdb "$drill"
sudo -n -u postgres pg_restore -d "$drill" "$latest/keycloak.dump"
sudo -n -u postgres psql -d "$drill" -tAc "select count(*) from information_schema.tables where table_schema='public'"
sudo -n -u postgres dropdb "$drill"
```

预期：restore 无错误，public schema 表数量与生产 keycloak 库一致（可用同样查询对比）。
演练后 drop 临时库，不触碰 `keycloak` 数据库与 Keycloak 容器。

## 真实恢复（仅在灾难恢复时，需维护窗口）

1. 先做一次新的 `pg_dump` 保留现场；
2. `systemctl stop` Keycloak 容器（`docker stop xeonolab-keycloak`）；
3. `dropdb keycloak && createdb -O keycloak keycloak` 后用 `pg_restore` 恢复快照；
4. 启动容器，验证 OIDC discovery、admin 登录、LAN/公网入口与 Forgejo/桌面 session；
5. 记录 RPO/RTO 与操作证据到 #390。

## 剩余风险（属 #390 范围）

- 同机本地副本；未 decide offsite 存储；
- 容器配置/密钥不在快照内；
- 未做完整启动式恢复演练（步骤已在上方给出）；
- 桌面库（VM101）、备份年龄/失败告警仍待做。
