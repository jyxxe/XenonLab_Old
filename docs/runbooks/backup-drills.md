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

## 2.5 VM 级整机备份（PVE vzdump，2026-11-03 起部分覆盖）

PVE 上新增了每周集群备份任务（不改动任何 VM 配置，仅覆盖单 OS 盘的 VM）：

```sh
# 只读查看任务（PVE API token，或 Web UI → Datacenter → Backup）
curl -s -k -H "Authorization: PVEAPIToken=root@pam!<token-id>=<secret>"   https://192.168.0.10:8006/api2/json/cluster/backup
```

| 字段 | 值 |
| --- | --- |
| job id | `751ca682-95ab-4900-b0bb-ed6deaa427b6` |
| vmid | `101`（web-server）、`105`（network-server）、`106`（docker-server）、`107`（embedding）、`109`（auth）、`110`（dev）、`111`（dev-agent） |
| 计划 | `sun 03:00`（PVE 本地时区 UTC+8，即周六 19:00 UTC） |
| 模式 / 压缩 | `snapshot`（guest-agent freeze/thaw）+ `zstd` |
| 存储 | `xeonolab-backup`（ZFS mirror 池，dir storage，quota 200G；实测 used 59.4G / avail 155.3G） |
| 保留 | `prune-backups=keep-last=2` |
| 首次验证 | 2026-11-03 手动 VM109 → 任务 stopped OK；归档 3,238,882,674 bytes；`zstd -t` 通过；SHA256 `7fe4d264…` |
| 2026-11-04 扩容 | VM101/106/110/111 的数据盘（`scsi1`）在 VM 配置中标 `backup=0`（数据盘内容另由每日 tar 快照覆盖，见 `data-disk-backup.md`），任务 vmid 扩为 7 台；手动 VM106 验证：仅 80 GiB OS 盘、sparse 94%、**1,797,992,031 bytes**、60s、`zstd -t` 通过、SHA256 `563041e3e9d82cfdff88d4bad68d8417861f88a5379b5e0f1e46ce750a7ab6e0` |

手工触发与核对（PVE API；需要 `root@pam` token）：

```sh
# 触发单 VM 备份；返回 UPID
curl -s -k -H "Authorization: PVEAPIToken=..." -X POST   https://192.168.0.10:8006/api2/json/nodes/pve/vzdump   --data-urlencode vmid=109 --data-urlencode storage=xeonolab-backup   --data-urlencode mode=snapshot --data-urlencode compress=zstd
# 轮询任务终态（stopped / exitstatus=OK），再用 API content 列表确认归档
curl -s -k -H "Authorization: PVEAPIToken=..."   'https://192.168.0.10:8006/api2/json/nodes/pve/storage/xeonolab-backup/content?content=backup'
# 宿主只读校验（root SSH 后）：zstd -t <archive> && sha256sum <archive>
```

**数据盘策略（2026-11-04）**：VM101/106/110/111 的大容量数据盘已在 VM 配置中标 `backup=0`，
因此 VM 级归档只含 OS 盘；数据盘内容由每日校验 tar 快照覆盖（`data-disk-backup.md`）。
VM104 2×8T、VM100 200G、VM103 2T 仍未纳入任何 VM 级归档。
该存储与 PVE 同宿主，因此**不是 offsite**；容量：7 台 OS-only 归档约 20 GiB/轮（实测 VM106 1.67 GiB），
quota 200G 下 keep-last 2 余量充足（当前 used 61.2G / avail 153.5G）。

## 3. 覆盖矩阵与已知缺口（截至 2026-11-03）

| 范围 | 状态 | 说明 |
| --- | --- | --- |
| 业务库数据 / 代码仓库 / 认证数据库 / 桌面账号偏好 | ✅ 每日校验备份 + 独立恢复抽检 | 见上表 |
| VM 级整机（OS、配置、密钥） | 🟡 **部分**：VM105/107/109 每周 | PVE 任务 `751ca682-95ab-4900-b0bb-ed6deaa427b6`：`vmid=105,107,109`，`sun 03:00`（UTC+8）→ 周六 19:00 UTC，snapshot+zstd，`xeonolab-backup`，keep-last 2；首份 VM109 归档已实测（3,238,882,674 bytes，`zstd -t` OK，SHA256 `7fe4d264…`）。VM101/106/110/111 因大容量数据盘需先标 `backup=0` 才能加入，见 #113/#390 |
| 大容量数据盘内容 | 🟡 每日 tar 快照（VM100/101/106/110/111），见 `data-disk-backup.md` | 覆盖 `/srv/openwebui`(VM100，stop-copy 4s)、`/srv/control`(VM101)、`/srv/docker`(VM106)、`/srv/dev`(VM110)、`/srv/dev-agent`(VM111)；VM104 2×8T 空闲盘（XeLibrary 数据根已由应用级备份覆盖）、VM103 2T HF-cache（可重建）**仍未覆盖**，tar 对在线数据根为 best-effort |
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
