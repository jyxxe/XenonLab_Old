# XeoLibrary local backup and isolated restore

This procedure creates a **local snapshot** of a XeoLibrary data root. A copy
on the same host or disk is not an offsite backup and does not satisfy the
independent-storage requirement. PostgreSQL, independent backup storage,
automated retention/monitoring, and a production restore drill are not
implemented by this tool.

## Automatic online snapshots (issue #390)

The production XeLibrary target (VM104) runs a nightly systemd timer that takes
a **verified online snapshot** without pausing the service:

- `xeonolab-xelibrary-backup.timer` (daily 03:30 + randomized delay, persistent)
  → `xeonolab-xelibrary-backup.service` → root wrapper
  `/usr/local/libexec/xeonolab-xelibrary-backup`;
- the wrapper runs the active release's
  `scripts/xelibrary_backup_online.py snapshot` against
  `/srv/xelibrary/core/xeonolab`, writing
  `/var/backups/xeonolab/xelibrary/xelibrary-<UTC stamp>Z`;
- each SQLite payload is copied with the online backup API, content-addressed
  objects are copied, and the existing manifest verification
  (format/checksum/payload SHA-256/SQLite integrity/catalogue↔table identity)
  must pass. If a concurrent table create/move/delete or attachment binding
  races the copy, verification fails, the partial directory is removed and the
  attempt is retried (3 attempts); a passing snapshot is internally consistent;
- retention keeps the newest **7** snapshots (`XELIBRARY_BACKUP_KEEP`), pruning
  older `xelibrary-*Z` directories only; other entries are never touched.

Operate/verify it:

```sh
sudo -n systemctl list-timers xeonolab-xelibrary-backup.timer --no-pager
sudo -n systemctl start xeonolab-xelibrary-backup.service
sudo -n journalctl -u xeonolab-xelibrary-backup.service -n 20 --no-pager
sudo -n ls -lt /var/backups/xeonolab/xelibrary | head
sudo -n /opt/xeonolab/current/.venv/bin/python \
  /opt/xeonolab/current/scripts/xelibrary_backup_online.py verify \
  --root /var/backups/xeonolab/xelibrary
```

Timing: a ~1 GB data root takes seconds on local disk; the service is a no-op
on targets without a XeLibrary data root (units are installed fleet-wide but
guarded by `ConditionPathExists`).

**Limits (must stay explicit):** this is still a same-host local copy — it does
not survive host/disk loss and is not offsite. The snapshot is not a quiesced
point-in-time: it is consistent as verified by the manifest, but a write that
lands after a table was copied simply belongs to the next snapshot. A daily
timer is not a substitute for the isolated restore drill below, or for the
offsite-storage decision tracked in #390.

## Preconditions

Before a production backup, the service coordinator must explicitly confirm
that all XeoLibrary writes are paused and keep them paused through snapshot
creation. The `--quiesced` option records an operator acknowledgement; it does
not stop or independently detect writers. Do not run the production procedure
without that confirmation. Ensure the destination is a new directory on the
intended protected storage. The tool creates private directories (0700) and
private files (0600).

## Create and validate a snapshot

```sh
uv run python scripts/xelibrary_backup.py backup \
  --source /path/to/xelibrary-data \
  --destination /path/to/protected-storage/xelibrary-YYYYMMDD \
  --quiesced
uv run python scripts/xelibrary_backup.py verify \
  --backup /path/to/protected-storage/xelibrary-YYYYMMDD
```

The manifest records the source root, catalogue table mappings, every
content-addressed object with a `referenced` flag, and each payload file's size
and SHA-256. Verification checks the manifest checksum, all payload hashes,
SQLite integrity, catalogue-to-`.xl` identity/path mapping, and that every
referenced or unreferenced object has the correct content-addressed path and
hash. Missing referenced objects, corrupt objects, and orphan `.xl` files fail
validation. Correct unreferenced objects are retained and listed; this tool
does not delete them. Existing `uploads.sqlite` and `objects/.uploads` files
are included in the payload manifest and restore, but upload-session/part
cross-file consistency is not yet validated. A failure means the snapshot is
not usable; preserve the diagnostic and do not treat it as completed.

SQLite database snapshots use SQLite's backup API, which incorporates
committed WAL frames. Recognized `catalogue.sqlite`, `uploads.sqlite`, and
mapped `.xl` `-wal`/`-shm` sidecars are validated as present and are not copied
as separate files. Sidecars without their corresponding database are rejected.

## Isolated restore rehearsal

Use a dedicated, existing, empty restore parent on a filesystem with sufficient
space. The restore name must be one safe directory component, and the target
must not exist. The tool refuses targets that overlap the recorded original
data root or the backup directory.

```sh
mkdir -m 700 /tmp/xelibrary-restore-drill
uv run python scripts/xelibrary_backup.py restore \
  --backup /path/to/protected-storage/xelibrary-YYYYMMDD \
  --restore-root /tmp/xelibrary-restore-drill --name restored
```

The isolated restore is revalidated before success. It does not start the
service or rewrite production paths. Remove only the isolated rehearsal
directory after retaining the validation result according to local policy.

## Current limits

The source must be quiescent across catalogue, table databases, and object
store; SQLite's backup API handles database snapshots, but cannot provide a
distributed transaction across those files. This command does not arrange the
write pause, upload copies, encrypt external media, enforce retention, or
provide independent storage. Those operational dependencies still need an
owner and an end-to-end production exercise.
