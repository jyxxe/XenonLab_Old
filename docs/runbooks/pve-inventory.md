# PVE inventory — 2026-10-01

Read-only PVE API inspection succeeded on node `pve`. Host has 36 CPU threads and about
126 GiB visible RAM. Existing VM IDs 100–111 are running. No VM was stopped or deleted.

| VM ID | Name | RAM GiB | System disk GiB |
|---|---|---:|---:|
| 100 | openwebui | 16 | 60 |
| 101 | web-server | 8 | 40 |
| 102 | fnds | 8 | 40 |
| 103 | hf-cache | 4 | 40 |
| 104 | xelibrary | 16 | 80 |
| 105 | network-server | 8 | 40 |
| 106 | docker-server | 16 | 80 |
| 107 | embedding | 16 | 80 |
| 108 | library-agent | 8 | 60 |
| 109 | auth | 4 | 40 |
| 110 | dev | 2 | 60 |
| 111 | dev-agent | 10 | 60 |

Read-only follow-up confirmed physical ZFS pool `aux-10t` is `ONLINE`. `hf-cache-disk` maps to
`aux-10t/hf-cache`; `fnos-disk` maps to `aux-10t/fnos`. Both logical storages share that physical
pool, so their reported free space must not be added together. `zfs-mirror-18t` is a separate
`ONLINE` pool. VM 103's 2 TiB data volume is mounted as ext4 at `/srv/hf-cache`; its guest IP is
`192.168.0.103`. The PVE host IP is `192.168.0.10`.

Desired layout: `infra/pve/desired-vms.json`. The existing auth VM is an additional resource
outside the planned 11 VMs; consolidating OIDC into Docker Server is needed before final RAM allocation.
Several system disks require growth, and Dev needs more cores/RAM. Inventory alone does not
establish backup health, VLAN/firewall isolation, installed services, or model availability.

Reinspect with `uv run python scripts/pve_inventory.py`, using environment variables
`PVE_TOKEN_ID`, `PVE_TOKEN_SECRET`, and trusted `PVE_CA_FILE`. `PVE_INSECURE=1` is an explicit
local bootstrap override for a self-signed certificate, not the production default.

## ZFS scrub 与池健康（2026-11-04 起）

两个物理池此前没有定期 scrub 记录（`zpool status` 无 `scan:` 行），位翻转只能依赖读路径上的镜像自愈。
2026-11-04 在 PVE 宿主安装并启用了月度 scrub：

- `/etc/systemd/system/zfs-scrub@.service`：`Type=oneshot`、`Nice=19`、`IOSchedulingClass=idle`、
  `ExecStart=/sbin/zpool scrub %i`；
- `/etc/systemd/system/zfs-scrub@.timer`：`OnCalendar=monthly`、`RandomizedDelaySec=6h`、`Persistent=true`；
- 已 enable：`zfs-scrub@zfs-mirror-18t.timer`、`zfs-scrub@aux-10t.timer`
  （下次 2026-12-01 00:44 / 03:56 CST，即 2026-11-30 16:44 / 19:56 UTC）。

首次手动 scrub（2026-11-04T06:18Z，只读检查结果）：

| 池 | 结果 |
| --- | --- |
| `aux-10t` | `scrub repaired 0B in 00:00:01 with 0 errors` |
| `zfs-mirror-18t` | `scrub repaired 0B in 00:08:05 with 0 errors` |

日常检查与手动触发：

```sh
zpool status zfs-mirror-18t aux-10t          # state / scan / CKSUM / SLOW 列
systemctl list-timers 'zfs-scrub@*'
systemctl start zfs-scrub@zfs-mirror-18t.service   # 等价于 zpool scrub zfs-mirror-18t
```

注意：

- scrub 是 IO 密集操作，timer 已用 idle IO 优先级 + 随机延迟；**不要在备份/部署高峰手动触发**，
  否则 vzdump/pg_dump/备份 tar 的耗时会被拉长。
- scrub 只检测与修复数据/元数据校验错误，不替代 SMART 监控（`prometheus-node-exporter-smartmon.timer`
  已存在于各 VM；PVE 宿主自身的 SMART 检查另行确认）。
- 池 `ONLINE` + `repaired 0B` + `0 errors` 只是当前时点证据；月度 timer 的历史结果用
  `zpool status` 的 `scan:` 行与 journal（`journalctl -u zfs-scrub@<pool>.service`）核对。
