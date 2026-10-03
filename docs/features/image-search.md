# Image search control plane

XeLibrary `POST /api/v1/search/image` queries the permanent Qwen `vision`
space. This API is independent of D100M `fast_text` and does not mix their
scores. It requires an authenticated embedding facade with the real Qwen
image endpoint and a ready descriptor, plus configured production Qdrant.
There is no generated-vector or SQLite exact fallback, including in development.
Local synthetic provider tests are not model or production acceptance.

Use multipart form fields with exactly one source:

- `upload`: one original PNG, JPEG or WebP image, at most 16 MiB,
  16,000,000 pixels and one frame; or
- `attachment_id` plus `table_id`: an existing live attachment binding;
  caller needs `xelibrary:read` and current table/row read access; or
- `text`: 1–4096 characters, encoded by Qwen in the same vision space.

All requests need `xelibrary:query`. Optional fields are `path` (default `/**`),
`limit` (default 20, range 1–100), and `filters` (JSON object string, default `{}`).
Binary inputs are validated with Pillow, without the wallpaper's lossy WEBP
conversion. The facade's advertised lower image limits also apply. Arbitrary
URLs and client filesystem paths are not inputs. Attachment bytes come from
the server CAS after authorization and hash validation.

The facade uses the existing `EMBEDDING_URL` and
`service_token("EMBEDDING")` provisioning boundary. `EMBEDDING_CA_FILE` may
identify its trusted CA. Text requests use `/v1/embeddings`; image requests use
`/api/v1/embed/image`, with `model=quality`, explicit `purpose=query|document`
and pinned model revision, dimensions and preprocessing revision. Qwen image
support belongs to the embedding worker implementation; installing this
control plane does not load weights. Neither endpoint nor credential content
belongs in a release artifact.

Deployment must calibrate `RATE_LIMIT_POLICIES_JSON` for the embedding service's
approved CPU capacity and service-identity traffic. The repository default
embedding-quality quota is 6 requests/minute; with Qwen batch size one, it is
not a usable budget for a large vision rebuild or a cold quality rerank.
This implementation keeps limits enabled: a 429 explicitly fails an index job
with its already indexed count, and explicit retry re-encodes bindings rather
than automatically queueing/throttling around the quota. Keep caller-facing
limits and validate service policy under real latency/resource constraints.
The exact image-to-quality route classification is supplied by the #23 common
classifier change when the features are composed; this branch does not edit
that shared classifier. No local synthetic quota setting proves production
capacity.

A ready `/v1/models` descriptor must identify
`Qwen/Qwen3-VL-Embedding-2B` with nonempty `model_revision`, `dimensions`,
`preprocessing_revision`, `normalized=true`, `max_batch_size`, `max_tokens`,
`image_max_bytes`, and `image_max_pixels`. Query and index responses must match
that space, return one finite unit vector with the configured dimension and
index zero. The 2B default is 2048D; explicit MRL configuration may reduce it.
D100M's 256D is unrelated. Preprocessing includes the pinned processor,
query/document instructions, pooling and normalization.

`POST /api/v1/index/vision/reindex`, JSON `{ "table_id": "..." }`, needs
`xelibrary:admin` and returns HTTP 202 with `data.job_id` and `data.space`.
Inspect the existing `/api/v1/jobs/{id}` as its owner. The durable job carries
`kind=vision`, its space, indexed/skipped counts and failure error code.
Unsupported non-image attachments are skipped. Provider failures explicitly
fail the job; already published current references remain usable. A later
explicit reindex is idempotent. Restarted queued/running jobs retain the
existing JobStore interrupted/failed behavior; no automatic retry is implied.
The ordinary `/api/v1/index/reindex` remains the D100M text operation.

Physical Qdrant collections are deterministic `vision_<space-key>` with named
vector `vision`, configured dimensions and cosine distance. `.xl` stores
`__xl_vision_refs` and known `__xl_vision_spaces`; the latter is committed before
remote upserts so cleanup also knows spaces from interrupted publication.
Point IDs include table, row revision, attachment binding/hash and full model
space. Reindex verifies the current binding again under the table write lock.
Row update, soft delete, purge, restore, table move/delete and attachment unbind
remove applicable remote points before committing source changes. Cleanup uses
all stored old spaces without requiring the current worker to be available.
A failed remote cleanup rolls back the source mutation.

Search results follow the standard request-ID envelope and search fields, with
`score_type=qwen3vl_cosine`, current-fact snippets and `matched_attachments`.
Remote candidates are rechecked against current table/row ACL, path, row
revision, attachment binding/hash and committed local reference. Deleted or
unbound images cannot be returned merely because the row revision stayed equal.
`meta.space`, `partial`, `missing_embeddings`, `candidate_limit` and
`coverage_truncated` describe the result. The entire accepted hit set and attachment query source are rechecked after
worker/candidate work before response. Coverage counts are a per-table ACL
snapshot during the bounded scan (`coverage_snapshot=true`), not an atomic
whole-request authorization snapshot. Coverage counts currently visible,
image-eligible bindings (bounded PNG/JPEG/WebP CAS header check after ACL), up to
10,000 such bindings, and excludes deleted/denied rows. This also covers legacy
images with generic MIME or filenames; decoding/limits are verified by reindex. Candidate search is bounded at
100–1000 points. It does not scan model vectors in Python.

Failures use the standard APIError envelope: missing/not-ready model 503
`MODEL_NOT_CONFIGURED`; unavailable/timeout embedding 503 `EMBEDDING_UNAVAILABLE`;
busy embedding 429 `EMBEDDING_BUSY`; incompatible vector 503
`EMBEDDING_PROTOCOL_ERROR`; input/space rejection 422 `MODEL_SPACE_MISMATCH`;
changed attachment query source 409 `STALE_ATTACHMENT`;
invalid image 422 `INVALID_IMAGE`; image too large 413 `UPLOAD_TOO_LARGE`;
source ACL/not found 403/404; Qdrant errors retain existing 503 codes.
Metrics expose bounded operation/result/error-code labels and search latency;
error logs include request/job ID and code, never query or image content.

Production acceptance remains separate: provision the real pinned offline
Qwen snapshot/CPU resources, facade authentication/TLS and Qdrant; run genuine
image-to-image and text-to-image examples, current ACL/lifecycle checks,
resource/latency/busy measurements and real API/OpenAPI verification. Do not
close the image-search Issue on local synthetic evidence. Rolling back removes
these routes/modules; source rows and attachment objects are unchanged, and
vision collections/local derivative tables can be rebuilt or retired separately.
