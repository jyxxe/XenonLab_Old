# Issue 248: synchronize the receiver regression with server worker completion

Baseline: `865a72277008a5773236b50b993bacdecc78d6ab`.

The original regression joined its HTTP client thread, verified 204, then immediately opened the body-timeout connection. A client can receive 204 and close before the server finishes socket cleanup and executes Receiver's semaphore release. The next connection can therefore be correctly rejected by the concurrency limit and return an empty response instead of reaching the timeout handler.

Only tests and a local verification helper change. The test uses an ObservedReceiver subclass that sets an Event after the complete real Receiver worker method returns, including its finally/slot release. A test-only wrapper around the real ThreadingMixIn worker adds a controllable barrier after socket cleanup and before that release. The test now asserts:

1. The sole active request keeps its slot; an excess connection is closed.
2. The first client receives204 and finishes while cleanup is paused.
3. The server completion Event is still unset in that paused state: client completion is insufficient.
4. Releasing the barrier allows the real worker to finish and release its slot; the test waits for that Event.
5. The next connection reaches the real body-timeout handler and satisfies the unchanged408 assertion.

Teardown releases both control barriers and joins the observed server workers before monkeypatch cleanup. No sleeps, relaxed status assertions, expanded existing timeouts, synthetic408 reply, semaphore bypass or production Receiver edits were added. The pause uses a bounded Event wait, not a delay intended to guess scheduling.

## Reproduce

From the repository root:

```sh
uv run python scripts/verification/pool-worker-sync.py --mode before
uv run pytest tests/test_pool_telemetry.py::test_http_receiver_bounds_slow_clients_and_concurrent_requests -q
uv run python scripts/verification/pool-worker-sync.py --repeat 20
```

Before mode loads the exact unmodified865a test source from Git and applies only a diagnostic scheduling pause after real socket cleanup and before slot release. It confirms the original317-line 408 assertion fails with first client204 and an empty next reply. The helper exits successfully when this expected control is observed, then releases the diagnostic worker. It records the exact test and unchanged Receiver hashes in runtime/verification/pool-worker-sync/before.json; before-final.log is the final control log.

Current mode invokes the actual current regression with a fresh temporary directory and monkeypatch fixture on each iteration. All20 iterations passed, each exercising the controlled client/server distinction plus actual concurrency rejection/204/408. current.json and current-final.log preserve outcomes/hashes. The isolated pytest case passed in0.53s. All networking is loopback and data/signing values are synthetic test fixtures; no private credentials, service identities, production endpoints or deployment operations are used.

The helper uses a distinct virtual filename and source line cache for the compiled historical test, so its traceback cannot accidentally show current edited lines. The earlier diagnostic-only linecache assertion failure is retained in before-linecache-failure.log; it is not a product regression or a passing control.

The unchanged Receiver SHA256 is `d8f2ec5070e6b3210f585675c48aeb5146984666b090d79c3397ed74aa053378`. The final test SHA256 is `2a4808f3dabda0d88f966a826a923c9218ec18fa89500ec04f4822c18825b909`.

## Full gates and limits

- uv run pytest:479 passed,1 existing Starlette warning,15.89s; pytest.log.
- Scoped Ruff for tests/test_pool_telemetry.py and scripts/verification/pool-worker-sync.py:passed; ruff.log.
- uv run python scripts/export_openapi.py:passed, no generated API diff; openapi.log.
- Frontend npm test:73 passed; unit.log.
- Frontend npm run build:passed; build.log.
- git diff --check:passed.

There is no frontend behavior change, so no new Chrome or visual acceptance is claimed. No API, scope, dependency, migration or production configuration changes. Rollback reverts this test-only patch.

This controlled local result proves a feasible test synchronization race; it does not establish the unique cause of the externally reported protected-deployment failure. Maintainer reports in224/230 and the historical213 flake remain external evidence. Restoring the failed publication intent remains an independently authorized operator action; this change performs no retry, merge or deployment and claims no production recovery. Refs #248.

## Independent coordinator verification

The coordinator reran the final source: 479 pytest cases passed (one existing warning, 13.17s), 73 frontend tests passed, and the frontend build, Ruff for xeonolab/tests/helper, OpenAPI export and diff whitespace checks passed. OpenAPI has no generated diff. The exact historical control and all 20 current controlled repetitions passed their expected assertions; parent-before.log and parent-current.log record these separate executions. The helper asserts and records that the imported Receiver resolves to this worktree’s infra/hfcache/pool_metrics_receiver.py before either mode executes. The product Receiver remains byte-identical to the baseline. No browser or production validation is claimed.
