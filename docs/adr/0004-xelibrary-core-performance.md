# ADR 0004 — XeLibrary core performance: fix the measured bottlenecks, no wholesale Rust/C++ rewrite

Status: Proposed (2026-09-30) — delivered for issue #40 on branch `opencode/issue-40`; requires review/acceptance.

## Context

Issue #40 asks to consider rewriting the XeLibrary core in Rust/C++ to improve
efficiency. The V1 core is a Python FastAPI control plane over `.xl` SQLite
files. Several heavy layers are already native code: SQLite and FTS5, the
Hugging Face `tokenizers` chunker (Rust), and D100M inference (PyTorch C++
kernels). plan.md §66–68 makes `.xl` + attachments + metadata the facts and
treats indexes as rebuildable derivatives; §101 requires an ADR before
architecture changes, and §102 requires every new production dependency to be
documented.

The decision therefore needs measurements of the current implementation, not
language preference.

## Measured evidence

Reproduce on a developer host:

```bash
uv run python scripts/benchmark_xelibrary_core.py --rows 5000 --vectors 20000 \
    --dims 256 --repeats 3 --profile
```

Environment: Python 3.12.13, SQLite 3.53.1, in-process FastAPI TestClient,
development identity with read/write/query/attach scopes and **no** admin scope,
so per-resource ACL checks really execute. Synthetic table: 5,000 rows
(~700-byte body, int64/float64/string fields) plus FTS5. Results JSON:
`runtime/verification/issue-40/core-bench-5000x20000.json`.

| Operation | Best | Result |
|---|---:|---|
| `POST /api/v1/query` selective full scan (`WHERE category=... LIMIT 50`) | 1,117 ms | 50 rows |
| `POST /api/v1/query` late window (`WHERE n >= 4950 LIMIT 50`) | 1,170 ms | 50 rows |
| `POST /api/v1/query` `ORDER BY score DESC LIMIT 100` | 1,230 ms | 100 rows |
| `GET /api/v1/tables/{id}/rows?limit=100` | 1,165 ms | 100 rows |
| `POST /api/v1/search/text` (FTS5 + ACL revalidation) | 1,248 ms | 20 rows |
| Component: SQLite scan + `json.loads` of all 5,000 rows | 12.0 ms | — |
| Component: AST predicate evaluation of all 5,000 rows | 4.2 ms | — |
| Component: DSL parse (`lark`) | 27 µs | — |

cProfile attribution of one query call (1.266 s total):

| Function | Cumulative | Calls |
|---|---:|---:|
| `library.allowed` (row/table/path ACL resolution) | 1.188 s — **94%** | 4,768 |
| `common.connect` (opens a new SQLite connection per check) | 0.712 s | 4,771 |
| `sqlite3.Connection.execute` (ACL SELECTs) | 0.634 s | 33,384 |
| `json.loads` + row decode | 0.034 s | 4,769 |
| `query.evaluate` | 0.022 s | 4,767 |

Exact-vector scan kernel, 20,000 × 256-dim vectors on the same host:

| Implementation | Best | Relative |
|---|---:|---:|
| Current: JSON-text vectors + Python dot product | 986 ms | ~300× Rust kernel |
| f32 binary vectors + Python dot product | 180 ms | ~57× Rust kernel |
| Rust prototype (`experiments/xl-vector-scan`, zero dependencies) | 3.1 ms | 1× (13 ms process wall) |

All three implementations produce the same best index, score and checksum; the
benchmark asserts this so the comparison is not self-reported.

## Analysis

1. **The API-bound path is I/O-architecture bound, not interpreter bound.**
   `allowed()` opens a fresh SQLite catalogue connection and runs several
   SELECTs per checked row (N+1). Row scan, JSON decode and AST evaluation
   together account for about 1% of the query at this scale. A Rust/C++ core
   that kept the same per-row checks would issue the same SQLite calls; a
   rewrite would not remove the dominant cost and could add FFI/serialization
   overhead.
2. **The one genuinely CPU-heavy Python loop is a development bootstrap.**
   Exact-vector scan is O(N·chunks·dims) per query with JSON text parsing.
   plan.md §13/§28 replaces it with Qdrant; a Qdrant service is already staged
   on VM 109 (TLS and API-key verified) but production semantic search is not
   connected yet. Simply storing vectors as f32 blobs is a 5.5× improvement
   inside Python; connecting Qdrant removes the O(N) scan entirely.
3. **The tokenizer/chunker and model inference are already native.** A
   rewrite decision mostly concerns control-plane logic — exactly the code
   where the tests encode ACL, revision and idempotency rules (ADR 0003).
4. **Deployment cost is real.** A compiled extension adds a build matrix,
   wheel/artifact inventory, and Deploy Gateway artifact review to a
   single-node system (plan.md §48/§51), while plan.md already prescribes
   PostgreSQL and Qdrant as the scaling path.

## Decision

Do **not** rewrite the XeLibrary core in Rust or C++ for V1. Keep the Python
FastAPI control plane and `.xl` SQLite facts, and fix the measured bottlenecks
in this order:

1. **Batch ACL resolution per request.** Resolve the row/table/path ACL set and
   owner for a table once per request instead of one SQLite connection per
   checked row, preserving row → table → nearest-ancestor-path precedence and
   response-time revalidation (plan.md §68). The benchmark should gate it:
   full scans should approach the raw scan+decode component (~15 ms at 5,000
   rows versus ~1,120 ms today).
2. **Stop storing vectors as JSON text.** Use f32 blobs for the bootstrap and
   connect the staged Qdrant service for production semantic search instead of
   optimizing the bootstrap scan further.
3. **Keep an optional native kernel as a fallback, not a dependency.** If,
   after (1) and (2), a measured CPU-bound kernel still exceeds the interactive
   budget at target scale (single-node exact scan or chunking throughput),
   prototype it as an optional PyO3/maturin extension with a pure-Python
   fallback. `experiments/xl-vector-scan` is the measured template. C++/
   pybind11 remains possible but shows no measured advantage here and would add
   manual memory-safety review.

## Consequences

- No new production language, toolchain, wheel matrix or deploy-inventory
  change; Deploy Gateway approval/lease paths are untouched.
- Python orchestration overhead remains for hot loops; acceptable while the
  planned PostgreSQL/Qdrant migrations are the scaling path.
- Revisit trigger: after (1) and (2), if profiling still shows a CPU-bound
  kernel above the interactive budget, or if single-node scale-out is chosen
  over Qdrant, re-open the Rust-extension decision with the same benchmark.
- Evidence limits: one development host, in-process transport, one synthetic
  dataset, no production query mix and no embedding-service latency. These
  numbers are benchmark comparisons, not production SLOs.
