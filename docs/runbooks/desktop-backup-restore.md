# 桌面数据快照与恢复演练（VM101）

VM101 的桌面服务数据根 `/var/lib/xeonolab/desktop-web` 保存持久状态：`accounts.sqlite`
（账号树、可撤销 API key、审计）、`desktop.sqlite`（偏好与壁纸元数据）以及不可变壁纸文件。
此前没有定时备份（issue #390）。`browser-sessions.sqlite`、`rate-limits.sqlite3` 属于可丢失的
运行时状态，**不**纳入快照。

## 快照策略

- 在线快照，无需停桌面服务：
  - `accounts.sqlite` 用 SQLite backup API 做一致性点拷贝；
  - `desktop.sqlite` + 其引用的壁纸复用受加固的壁纸快照 helper（持共享数据锁，校验 WebP 元数据/解码/大小）；
- 快照目录 `/var/backups/xeonolab/desktop/desktop-<UTC stamp>Z/`：
  - `accounts.sqlite`（0600）；
  - `desktop/`（来自壁纸 helper 的 `desktop.sqlite`、`wallpapers/`、其自身 manifest）；
  - `manifest.json` + `manifest.sha256`（data_dir、desktop 标志、逐文件 size/SHA-256）；
- 保留最新 **7** 份（`DESKTOP_BACKUP_KEEP`），只修剪 `desktop-<stamp>Z` 目录；
- 校验：manifest 校验和、accounts 完整性 + 必要表、壁纸 helper 的完整校验、逐文件哈希。

**限制**：同机本地副本，不是 offsite 备份；不包含 OIDC 运行时凭据、服务配置与 ephemeral session。

## 安装/验证（VM101 是发布目标）

`infra/systemd/stage.sh` 在存在 `/var/lib/xeonolab/desktop-web/accounts.sqlite` 的节点上自动安装
`/usr/local/libexec/xeonolab-desktop-backup.sh`、`xeonolab-desktop-backup.service/.timer` 并启用 timer；
其它目标上该 timer 是 no-op。首次或手动安装可执行一次部署即可（工具来自 `/opt/xeonolab/current`）。

## 运行与验证

```sh
sudo -n systemctl start xeonolab-desktop-backup.service
sudo -n journalctl -u xeonolab-desktop-backup.service -n 10 --no-pager
sudo -n find /var/backups/xeonolab/desktop -maxdepth 1 -mindepth 1 -type d
latest=$(sudo -n find /var/backups/xeonolab/desktop -maxdepth 1 -mindepth 1 -type d -printf '%T@ %p\n' | sort -rn | head -1 | cut -d' ' -f2-)
sudo -n /opt/xeonolab/current/.venv/bin/python \
  /opt/xeonolab/current/scripts/desktop_backup_online.py verify --dir "$latest"
sudo -n systemctl list-timers xeonolab-desktop-backup.timer --no-pager
```

预期：`snapshot ok: ... (attempt 1, N accounts bytes, M wallpapers, K files, pruned 0)`；
verify 输出 accounts bytes/wallpapers/files；timer 下次 04:15 UTC 左右。

## 恢复演练（隔离目录，不触碰生产数据）

```sh
latest=$(sudo -n find /var/backups/xeonolab/desktop -maxdepth 1 -mindepth 1 -type d -printf '%T@ %p\n' | sort -rn | head -1 | cut -d' ' -f2-)
drill=/var/tmp/desktop-restore-drill-$(date -u +%Y%m%d%H%M%S)
sudo -n /opt/xeonolab/current/.venv/bin/python - "$latest" "$drill" <<'PY'
import pathlib, shutil, sqlite3, sys
from xeonolab.wallpaper_backup import restore_desktop_wallpaper_backup
snapshot, drill = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
restore_desktop_wallpaper_backup(snapshot / "desktop", drill)
with sqlite3.connect(snapshot / "accounts.sqlite") as restored:
    print("accounts:", restored.execute("select count(*) from accounts").fetchone()[0])
with sqlite3.connect(drill / "desktop.sqlite") as restored:
    print("preferences:", restored.execute("select count(*) from preferences").fetchone()[0])
    print("desktop_wallpapers:", restored.execute("select count(*) from desktop_wallpapers").fetchone()[0])
PY
```

预期：restore 无错误；把打印的 accounts/preferences/desktop_wallpapers 数量与生产库同样查询对比
（`sudo -n sqlite3 /var/lib/xeonolab/desktop-web/accounts.sqlite 'select count(*) from accounts'`）。
演练后 `sudo -n rm -rf "$drill"`；不要覆盖 `/var/lib/xeonolab/desktop-web`。

## 真实恢复（仅在灾难恢复时，需维护窗口）

1. 先手动触发一次新快照保留现场；
2. `systemctl stop xeonolab@desktop-web.service`；
3. 把选定快照恢复到新路径（`accounts.sqlite` + `desktop.sqlite` + `wallpapers/`），校验完整性；
4. 以部署流程切换服务数据根（或按 runbook 恢复），启动服务；
5. 验证 LAN/公网入口、登录、账号列表、偏好与自定义壁纸，记录 RPO/RTO 到 #390。

## 剩余风险（属 #390 范围）

- 同机本地副本；未决 offsite/独立存储（关联 #113）；
- 备份失败/年龄告警尚未实现；
- 未做启动式完整恢复演练（步骤已在上方给出）；
- `accounts.sqlite` 的高频写入由 SQLite backup API 保证一致，但无跨库事务语义（账号与桌面偏好分别快照）。
