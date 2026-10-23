

## Synchronization after Tool adapter merge

Rebased onto protected base `529e335` after #321 merged. Independent ADR
sections and Tool manifest entries are retained. Feature-specific focused tests
plus Tool adapters, strict execution registry and service declaration checks pass: **157 tests**. Ruff, OpenAPI export without drift and diff checks pass.
The earlier full regression remains scoped to its recorded source baseline; no
new individual full run is claimed for this synchronization. Production/visual
acceptance and merge remain outstanding.
