# XeLibrary Qdrant fast-text operations

XeLibrary uses Qdrant's named `fast_text` vector in 256D cosine space. The
`.xl` file remains the source of row, revision and ACL facts. The service reads
Qdrant candidates, then checks each candidate against current catalogue ACLs
and `.xl` state before returning it.

## Production configuration

`scripts/prepare_deployment.py` emits the following variables only for the
XeLibrary service on VM104:

```text
QDRANT_URL=https://192.168.0.109:6333
QDRANT_API_KEY_FILE=/etc/xeonolab/qdrant-api-key
QDRANT_CA_FILE=/etc/xeonolab/lan-root-ca.crt
```

The API key file is provisioned separately by the coordinator and is never
copied into the release bundle. The network policy permits VM104 to reach
VM109 TCP/6333; do not broaden the Qdrant listener or disable TLS verification.
Production semantic search fails with a service error when Qdrant is missing or
unavailable; it does not silently switch to the development exact-scan index.

## Live service acceptance

Only the coordinator runs this against the real service, because creating,
writing and deleting remote Qdrant collections are coordinator-managed writes.
Run from the approved VM104 release checkout with the configured URL, key-file
path and CA path:

```bash
QDRANT_URL=https://192.168.0.109:6333 \
QDRANT_API_KEY_FILE=/etc/xeonolab/qdrant-api-key \
QDRANT_CA_FILE=/etc/xeonolab/lan-root-ca.crt \
uv run python scripts/accept_qdrant_fast_text.py
```

The script generates a unique temporary collection, verifies create/upsert/ANN
query and cosine score, then deletes and verifies removal of only that
collection in `finally`. It creates no `.xl` table and uses no customer row
data. Keep the sanitized result and request ID in the release/acceptance record;
never include the API key or its contents.

## Failure handling

- `QDRANT_NOT_CONFIGURED`, `QDRANT_TLS_REQUIRED` or
  `QDRANT_SCHEMA_MISMATCH`: verify the generated env file references the
  coordinator-provisioned key path, CA file and expected named vector schema.
- `QDRANT_UNAVAILABLE`: verify VM109 service health, VM104→VM109 firewall path,
  TLS chain and key-file permissions. API responses intentionally omit remote
  details and credentials; inspect service logs without dumping secret files.
- An index job marked failed does not advance indexed revisions. Correct the
  dependency and re-run the admin-only reindex. If cleanup failed, the row
  mutation is rolled back; retry after restoring Qdrant availability.
- Search always validates candidate ACL, path, row revision and current `.xl`
  values. Stale or unauthorized points are dropped and never supply snippets.

Prometheus counters include `xelibrary_qdrant_cleanup_total`,
`xelibrary_qdrant_cleanup_failures_total`, and
`xelibrary_index_failures_total`. The exact bootstrap backend is development
only and is not evidence of production ANN acceptance.
