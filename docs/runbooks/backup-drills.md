# 备份与恢复演练索引（2026-11-03）

本页汇总生产四个数据域的**每日校验备份**、实测 RPO/RTO、恢复演练入口与尚未覆盖的缺口。
各域细节仍以对应 runbook 为准；本页用于发版/巡检时快速核对与审计。

## 1. 四个每日校验备份（均为**同机本地**副本）

| 数据域 | 主机 | systemd 单元 | 计划（UTC） | 保留 | 实测快照大小 | 实测创建耗时 | 快照命名 | 校验/演练 runbook |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| XeLibrary 业务数据 | VM104 | `xeonolab-xelibrary-backup.timer/.service` | 每日 03:30（随机 ≤20min，Persistent） | 7 | ~1.0 GiB/份（2 份共 2.0G） | ~51s | `xelibrary-<UTCstamp>Z` | `xelibrary-backup-restore.md` |
| Forgejo 仓库 | VM110 | `xeonolab-forgejo-backup.timer/.service` | 每日 04:00（随机 ≤20min） | 7 | ~111 MiB/份（2 份共 223M） | ~5.5s（stop-copy-start） | `forgejo-<UTCstamp>Z` | `forgejo-backup-restore.md` |
| Keycloak PostgreSQL | VM109 | `xeonolab-postgres-backup.timer/.service` | 每日 04:30（随机 ≤20min） | 7 | ~470 KiB/份（2 份共 972K） | ~1s（在线 `pg_dump -Fc`） | `keycloak-<UTCstamp>Z` | `keycloak-backup-restore.md` |
| 桌面账号/偏好 | VM101 | `xeonolab-desktop-backup.timer/.service` | 每日 04:15（随机 ≤20min） | 7 | ~1.6 MiB/份（2 份共 3.2M） | ~1.6s | `desktop-<UTCstamp>Z` | `desktop-backup-restore.md` |

共同点：快照目录在 `/var/backups/xeonolab/<域>/`，每份都带 `manifest.json` + `manifest.sha256`
（逐文件 size/SHA-256），**发布前自校验**（完整性/可解码/可列出对象），保留最新 7 份。
所有 timer `Persistent=true`，宿主停机后补跑一次。

## 2. RPO / RTO（实测口径）

- **RPO = 24 小时**（每域每日一次；timer 随机延迟 ≤20min）。实际最近快照时间与年龄用
  `scripts/ops_audit.py` 的 `backup_domains` 查看（见 `ops-audit.md`）。
- **数据级 RTO（实测）**：
  - Keycloak：`pg_restore` 到临时库 < 5s，演练后 public schema 表数 101 == 生产 101（2026-11-03 自动快照演练）；
  - Desktop：隔离目录恢复 `desktop.sqlite` + 壁纸 < 2s，accounts/preferences/wallpapers 计数与生产一致；
  - Forgejo：解压归档后 1 个 SQLite 库 + 1 个仓库通过 `PRAGMA integrity_check` 与 `git fsck`（秒级）；
  - XeLibrary：快照约 51s；隔离恢复后 `create_app` 可列出表与行（历史演练行数 2 / 400,709 一致）。
- **整机/启动式 RTO 未在生产测量**：切换数据根、重启服务与入口验证需要维护窗口，本页**不声明**该指标；
  恢复演练的基础设施事实见 `xelibrary-backup-restore.md` 等 runbook 的“真实恢复”小节。

## 3. 覆盖矩阵与已知缺口（截至 2026-11-03）

| 范围 | 状态 | 说明 |
| --- | --- | --- |
| 业务库数据 / 代码仓库 / 认证数据库 / 桌面账号偏好 | ✅ 每日校验备份 + 独立恢复抽检 | 见上表 |
| VM 级整机（OS、配置、密钥） | ❌ **无定时** | PVE `xeonolab-backup`（ZFS mirror 池，quota 200G）只有 2026-10-02/03 手工归档；`GET /cluster/backup` 为空。见 #113/#390 |
| 大容量数据盘内容 | ❌ 未纳入 VM 级归档 | `/srv/control`(VM101, 115M)、`/srv/docker`(VM106, 2.1M)、`/srv/dev`(VM110, 656M)、`/srv/dev-agent`(VM111, 2.1M)；VM104 2×8T、VM100 200G、VM103 2T 亦未覆盖 |
| offsite / 独立故障域 | ❌ 无自动通道 | #113 只有手工加密离机副本（外部物理终端）与回传恢复演练；durable 离机渠道未决 |
| 备份失败/年龄的外部推送告警 | ❌ 未实现 | 目前为 `ops_audit` finding + journal/退出码；无邮件/IM transport |

## 4. 日常核对（只读）

```sh
# 全部域的快照年龄、timer/unit 状态、磁盘/证书巡检
uv run python scripts/ops_audit.py \
  --hosts 192.168.0.101,192.168.0.104,192.168.0.109,192.168.0.110 \
  --key /tmp/devagent-admin/vm-key --known-hosts /tmp/devagent-admin/known_hosts \
  --ca-file /etc/devagent/lan-root-ca.crt

# 单域手动触发与校验（示例：XeLibrary）
sudo -n systemctl start xeonolab-xelibrary-backup.service
sudo -n journalctl -u xeonolab-xelibrary-backup.service -n 10 --no-pager
```

## 5. 注意

- 全部为**同机本地**副本，不抵御宿主机/机房级故障；不要把本地快照当作灾备证明。
- 恢复演练一律在隔离目录/临时库进行，**不得覆盖**生产数据；真实恢复需维护窗口与 runbook 步骤。
- 容量估算：XeLibrary 约 7 GiB（keep 7）、Forgejo ~0.8 GiB、Keycloak <7 MiB、Desktop ~12 MiB；
  注意 XeLibrary 数据增长会线性放大快照占用。
- 本页数据来自 2026-11-03 的生产实测（PR #396–#402 的交付与验证记录）；变化时请更新对应 runbook 与本页。
