# XeoLibrary local backup and isolated restore

This procedure creates a **local snapshot** of a XeoLibrary data root. A copy
on the same host or disk is not an offsite backup and does not satisfy the
independent-storage requirement. PostgreSQL, independent backup storage,
automated retention/monitoring, and a production restore drill are not
implemented by this tool.

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

The manifest records the source root, catalogue table mappings, and each
payload file's size and SHA-256. Verification checks the manifest checksum,
all payload hashes, SQLite integrity, catalogue-to-`.xl` identity/path mapping,
and that attachment references resolve to correctly hashed objects with no
missing or orphan table/object files. A failure means the snapshot is not
usable; preserve the diagnostic and do not treat it as a completed backup.

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
