# Optional Qwen CPU worker (Issue #23)

This increment implements a real, optional, local-pretrained CPU encoder behind the embedding
service API. Default installation still reports quality `not_configured`/503. No weights were
obtained and no real inference or production activation was performed for this change. Synthetic
process/API tests validate control flow, not model quality. Phase 4 remains incomplete.

## Runtime and snapshot provisioning

The reviewed model source is QwenLM/Qwen3-VL-Embedding commit
`393e2978d27852b0d0230d6994f37f9c15bed73c`; provenance, Apache-2.0 license, original README,
and the minimal CPU/offline/fail-closed patch are in `xeonolab/vendor/qwen3_vl/`. Only the native
pretrained model class is used; the generic upstream URL/path/helper interface is not exposed.
No separate Reranker model is introduced. CPU FP32/eager, query/document instructions, last-token
pooling, MRL truncation and L2 normalization form the versioned encoding space. Default dimensions
are 2048; operator-configured 64–2048 dimensional output is a separate space.

`requirements-qwen-cpu.txt` defines an **optional Linux CPU artifact**, not the default locked env.
Transformers 4.57.3 requires Hub <1, which cannot coexist with this project's Hub >=1. The selected
[5.2.0 metadata](https://github.com/huggingface/transformers/blob/v5.2.0/src/transformers/dependency_versions_table.py)
accepts Hub >=1.3,<2, Torch >=2.4 and tokenizers <=0.23.0. The optional artifact pins tokenizers
0.23.0 (within the project's >=0.20,<1 range), rather than the default lock's 0.23.2. The reviewed
5.2 Qwen model/processor symbols are present; the removed, unused `check_model_inputs` import is
also removed from the vendor copy. This is static source/metadata compatibility, not a tested
heavy-runtime installation. Actual CPU wheels, final dependency lock/hashes and model compatibility
must be built and validated by the coordinator. The loader rejects mismatched pinned dependency
versions before importing model code.

A coordinator-controlled build can resolve the project and optional requirements together:

```bash
uv venv /approved/build/qwen-cpu
uv pip install --python /approved/build/qwen-cpu/bin/python . -r requirements-qwen-cpu.txt
```

This command was **not executed** in this change. Production uses the reviewed offline wheel
inventory/artifact, not an on-demand install or network download. Launch from that artifact's
interpreter so `spawn` uses the same compatible runtime:

```bash
/approved/runtime/qwen-cpu/bin/python -m uvicorn xeonolab.embedding:create_app \
  --factory --host 127.0.0.1 --port 8104
```

Running `uv sync --locked`/ordinary `uv run` on that optional artifact can restore default
0.23.2 or remove optional packages; that is not the Qwen launch path. There is no global
multiprocessing executable override. D100M must also pass its real smoke on the final shared
CPU artifact before activation; no D100M real weights were used here.

The worker requires `QWEN_MODEL_PATH` (immutable read-only snapshot), `QWEN_MODEL_REVISION`
(exact 40-character model Hub commit, distinct from the source commit), and `QWEN_MANIFEST_FILE`.
The manifest defaults to `<snapshot>/.xeonolab-qwen-manifest.json` and contains:

```json
{"model_id":"Qwen/Qwen3-VL-Embedding-2B","model_revision":"<exact model commit>",
 "files":[{"path":"<relative file>","size":123,"sha256":"<64-character digest>"}]}
```

Every snapshot file must be listed and hash/size verified, including safetensors and configuration;
symlinks are rejected. Manifest metadata is excluded from the payload listing. Model and processor
load only local files; pickle weights/dynamic remote code are disabled. The child removes inherited
environment identities before model imports and bounds CPU/BLAS threads independently of D100M.

Existing HF-cache snapshots are **not directly accepted** as this identity manifest: their
`.xeonolab-manifest.json` holds `cache_key/files`. The coordinator obtains the reviewed item from
`GET /api/v1/cache/items/{item_id}`, checks `repo_type=model`, canonical `repo_id`, exact
`resolved_revision`, complete files and successful integrity verification, then creates the identity
manifest using `model_id=item.repo_id`, `model_revision=item.resolved_revision`, `files=item.files`.
Provision it beside a read-only copy/mount. The original HF-cache metadata file may remain and is
excluded from payload listing. If serving a mounted cache item, acquire
`POST /api/v1/cache/items/{item_id}/leases` before mounting/loading, hold that lease for the entire
worker lifetime, and release only after shutdown and unmount; alternatively copy into an immutable
approved release before releasing the cache lease. The worker has no cache credentials and does
not acquire/renew/release leases itself. Partial pattern-filtered snapshots are not ready candidates.

## API and lifecycle

Existing authenticated `/v1/models` and `/api/v1/embed/models` expose the quality descriptor:
canonical `id`, `alias=quality`, `status`, `model_revision`, `dimensions`, `preprocessing_revision`,
`normalized`, `max_batch_size`, `max_tokens`, `image_max_bytes`, `image_max_pixels`, `image_formats`.
Statuses are `not_configured`, `loading`, `ready`, `unavailable`; unknown unconfigured metadata is
null. `ready` means the verified model and processor loaded, not that production throughput passed.
Models require `embedding:read`; text/image encoding requires `embedding:write`.

`POST /v1/embeddings` and `/api/v1/embed/text` retain fast behavior and add quality fields:
`model=quality`, `input` string/list, `purpose=query|document` (default document), optional expected
`model_revision/dimensions/preprocessing_revision`. Quality batches are one item, <=32768 characters;
the actual processor enforces configured token limits including prompt/image tokens (default2048,
maximum8192). Existing text `truncation=reject|truncate` remains; image never silently truncates.
`POST /api/v1/embed/image` accepts multipart `upload`, `model=quality`, `purpose`, and the same
expected space fields. Supported actual codecs are PNG/JPEG/WEBP, one frame, <=16MiB encoded and
<=16,000,000 decoded pixels; preprocessing resizes within `QWEN_PROCESS_PIXELS` (default262144).
No external URL/local path transport exists. Failed or missing image preprocessing returns failure,
never a successful NULL/text-only embedding. Multipart parser/gateway request limits must be
provisioned as well; the worker's accepted-content limit is not a claim of transport admission control.

Quality success returns canonical `model`, all three space fields, `normalized=true`, ordered
`data.index/embedding`, and actual `usage.input_tokens/total_tokens`. Typed OpenAPI declares the
existing common error envelope (including `details/request_id`) for authorization, validation,
busy and unavailable responses. Space mismatch is 422; unavailable/loading is 503; a busy process
is 429 without an application waiting queue. All quality callers share one worker gate. Text,
image facade, Library image search and quality hybrid/semantic routes use the quality rate group.
No numeric quota is raised: existing quality quota is 6 requests/60s **per internal HTTP call**.
Cold candidate rerank and sequential image indexing need coordinator calibration of service-principal
quotas, candidate counts, measured token/batch/resource budgets and client deadlines; a healthy
model alone does not make 100–500 calls under a 120s search deadline viable. Do not disable quotas.

`QWEN_CPU_THREADS` default2, `QWEN_TIMEOUT_SECONDS` default120 and
`QWEN_STARTUP_TIMEOUT_SECONDS` default600 are bounded operator settings. Loading is nonblocking
for API startup. On timeout/crash/model failure the child is stopped before releasing the request
gate. Input422 and busy429 can retry against the same healthy process. Shutdown is idempotent;
terminal failures require a fresh application instance/restart, with no automatic reload/restart.
D100M scopes, encoding behavior and main readiness rule are retained; quality availability is separate.
Metrics expose real loaded/in-flight/queue=0, lifecycle state and per-model requests/errors/latency.
Logs contain lifecycle/error codes or request metadata, not input, image or model exception content.

## Validation still required

After provisioning an approved snapshot and compatible artifact, the opt-in real smoke is:

```bash
/approved/runtime/qwen-cpu/bin/python scripts/qwen_local_smoke.py \
  --snapshot /approved/snapshot --revision <exact-model-commit> --manifest /approved/manifest.json \
  --image /approved/example.png
```

Only CLI help was run here. Actual text/document/image vectors, processor token counts, model quality,
CPU memory peaks, long inputs, fast/quality concurrent latency and throughput, final artifact package
compatibility, Gateway lease/release and online acceptance remain unexecuted. The conservative V1
has no priority scheduler (interactive image > rerank > background), no hot reload/automatic restart,
and does not implement vision indexing or quality rerank/cache itself. Keep Issue #23/Phase4 open
until the real-model and production dependencies have evidence.
