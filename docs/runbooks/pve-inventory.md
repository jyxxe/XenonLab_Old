# PVE inventory — 2026-09-30

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

Storage: `local-lvm` about 816 GiB; `zfs-mirror-18t` about 16.2 TiB;
`fnos-disk` and `hf-cache-disk` each report about 8.97 TiB. Their shared physical backing
must be verified before provisioning: reported per-pool availability must not be added together.

Desired layout: `infra/pve/desired-vms.json`. The existing auth VM is an additional resource
outside the planned 11 VMs; consolidating OIDC into Docker Server is needed before final RAM allocation.
Several system disks require growth, and Dev needs more cores/RAM. Inventory alone does not
establish backup health, VLAN/firewall isolation, installed services, or model availability.

Reinspect with `uv run python scripts/pve_inventory.py`, using environment variables
`PVE_TOKEN_ID`, `PVE_TOKEN_SECRET`, and trusted `PVE_CA_FILE`. `PVE_INSECURE=1` is an explicit
local bootstrap override for a self-signed certificate, not the production default.
