# Desktop preferences and wallpaper backup/restore

`scripts/desktop_wallpaper_backup.py` snapshots only `desktop.sqlite` and the
immutable UUID-named images referenced by its `desktop_wallpapers` table. It
does not include account/session databases, OIDC state, runtime credentials,
or any other service data. Store the output on protected independent storage;
the script creates private directories/files but does not encrypt, rotate, or
monitor backups.

## Snapshot, verify, and isolated restore

```sh
uv run python scripts/desktop_wallpaper_backup.py backup \
  --data-dir /var/lib/xeonolab/desktop-web \
  --destination /protected-backups/desktop-YYYYMMDD \
  --max-total-bytes 536870912 --max-assets 1000 \
  --free-space-reserve-bytes 67108864
uv run python scripts/desktop_wallpaper_backup.py verify \
  --backup /protected-backups/desktop-YYYYMMDD \
  --max-total-bytes 536870912 --max-assets 1000
uv run python scripts/desktop_wallpaper_backup.py restore \
  --backup /protected-backups/desktop-YYYYMMDD \
  --destination /var/tmp/desktop-restore-drill \
  --max-total-bytes 536870912 --max-assets 1000 \
  --free-space-reserve-bytes 67108864
```

By default, total snapshot size (database plus referenced assets) is capped at
512 MiB, at most 1,000 assets are accepted, and backup/restore require the
destination filesystem to report at least 64 MiB more free space than the
estimated copy size. Change `--max-total-bytes`, `--max-assets`, or
`--free-space-reserve-bytes` for a deliberately larger local operation. Every
individual image is rechecked as a single-frame WebP no larger than 5 MiB and
16 megapixels. Encoded uploads also have a hard 5 MiB output cap.

The application and backup helper coordinate wallpaper upload/replacement and
deletion with `.desktop-wallpaper.lock`. While holding that lock, the helper
uses SQLite's backup API for a consistent `desktop.sqlite` point-in-time
snapshot and copies exactly the UUID assets referenced by that snapshot. It
checks database integrity, metadata owner/asset mapping, actual WebP decode,
file sizes and hashes, and rejects symlinks, missing files, unlisted files,
unsafe IDs, or preferences selecting a missing custom image. Restore refuses
an existing destination and installs only the desktop database and wallpaper
directory into a new root. Run it first as an isolated restore drill; for a
production restore, stop the desktop service, restore to a new path, verify it,
then switch the service data root under the normal deployment procedure.

Image decode, metadata normalization, and WebP encoding run in a worker thread.
Each service process accepts at most two wallpaper processing requests at once;
when full, additional uploads receive HTTP 429 `WALLPAPER_PROCESSING_BUSY`
before their bodies are read. In a multi-process server, the total bound is two
requests per process.

The free-space check is a preflight estimate, not a reservation: unrelated
writers can consume space after the check and during copying. Keep the stated
reserve and monitor the destination filesystem; on a copy failure the helper
removes its incomplete staging directory and does not publish the target.

Do not roll back to an application version whose `Preferences` schema does not
accept the `custom` wallpaper value before restoring the matching pre-feature
desktop snapshot. Older code may fail to load that preference. Preserve the
current database and image directory as a separate rollback point before any
restore. The backup helper does not modify live data or perform a service
switch.

This helper's local snapshot and isolated restore tests are not a production
backup drill. Production deployment still needs an owned schedule, independent
storage, retention/monitoring, account/session database coverage, and an
end-to-end restore rehearsal.
