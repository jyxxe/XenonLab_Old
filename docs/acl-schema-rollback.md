# XeLibrary ACL catalogue rollback

`scripts/rollback_acl_schema.py` is an explicit, catalogue-only compatibility downgrade for
releases whose `acl` table has the current five columns (`resource_type`, `resource_id`,
`grants`, `revision`, `explicit`). It removes inherited-state tombstones (`explicit=0`) and
copies every explicit grant into the legacy three-column schema. The catalogue DDL and copied
rows change inside one SQLite transaction; a failure rolls the transaction back. The script
creates and integrity-checks a new SQLite safety backup before opening that transaction and
refuses to overwrite an existing backup file.

## Procedure

1. Stop the XeLibrary service and prevent any other process from writing to its data directory.
2. Ensure there is enough space for a full catalogue copy and choose a new backup path outside
   the live catalogue path.
3. Run the command as the service-data owner:

   ```sh
   uv run python scripts/rollback_acl_schema.py \
     /path/to/xelibrary/catalogue.sqlite \
     --backup /path/to/backups/catalogue-before-acl-rollback.sqlite \
     --service-stopped
   ```

4. Confirm the command reports `state: completed`, keep the backup, and only then start the
   previous release. The script does not stop services or inspect the process manager; the
   operator's `--service-stopped` confirmation is required.

If the command fails, do not start the previous release until the catalogue and service state
have been inspected. Transactional DDL prevents a partially downgraded `acl` table; the backup
is retained if a downgrade validation or transaction fails. Do not rerun with the same backup
path; choose a new path or investigate the existing backup first.

## Restore and compatibility limits

To restore a catalogue backup, keep XeLibrary stopped, preserve the failed/current catalogue
under a different name, and atomically place the saved SQLite backup at the configured catalogue
path. Verify it with `sqlite3 <path> 'PRAGMA integrity_check;'` before starting the matching
release. A backup made by this tool is the pre-downgrade five-column catalogue, so restoring it
recovers ACL revisions and tombstones as well as grants. The earlier three-column backup made
before the ACL feature can also be restored by the matching earlier release; the current release
will add its two columns at startup.

Downgrading to the legacy schema intentionally loses ACL revision numbers and explicit-clear
tombstones. Active explicit grants are preserved exactly. Since tombstones are removed, an older
release will not interpret them as explicit empty ACLs; the normal ancestor inheritance applies.
The old release's positional `INSERT INTO acl VALUES(?,?,?)` works again after downgrade. If the
current release is started afterward, it migrates the three-column table forward with defaults
(`revision=1`, `explicit=1`), so deleted revision history cannot be reconstructed. This procedure
touches only `catalogue.sqlite`; it does not roll back table data, attachments, or service binaries.
