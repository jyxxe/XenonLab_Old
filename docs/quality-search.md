# Quality search and hybrid fusion

`POST /api/v1/search/semantic` with `mode=quality` runs a second stage over a
D100M shortlist, rather than encoding the whole library with Qwen. The query
and candidate documents use `Qwen/Qwen3-VL-Embedding-2B` through the authenticated
embedding service API. The separate Qwen Reranker model is not introduced.
Fast and auto continue to use the fast path; responses report `meta.actual_mode`.

Production keeps ADR0005's Qdrant `fast_text` requirement. Quality requests use
`min(max(limit * 5, 100), 500)` chunk hints; fast requests retain their existing
100–1000 hint budget. Hints must match current table/path, row revision, column,
fast model/chunker revision and local index references. Current `.xl` facts,
path/field filters, and table/row ACLs are checked before any document encoding.
A hint's supplied payload text is never sent to the quality worker. Development
alone can use the existing SQLite exact bootstrap index.

Quality scores are cosine similarities within one declared Qwen embedding
space. Chunks aggregate to the best quality match per row. Final semantic
ranking uses reciprocal rank fusion, `2/(60 + quality_rank) + 1/(60 + fast_rank)`;
`score_type=quality_rrf` reports this rank score, not a cosine. Stable row IDs
break ties. The returned matched chunk and snippet correspond to the best
quality chunk. Fast and Qwen cosine values are never added together.

`POST /api/v1/search/hybrid` collects structured-filtered, permission-checked
FTS and semantic results, then fuses their ranks with equal weights and k=60
(`score_type=hybrid_rrf`). It keeps the internal semantic candidate budget until
fusion, rather than applying the caller's final limit before merging. Quality
hybrid uses the same second stage; fast/auto hybrid does not call Qwen. Missing
semantic dependencies return an explicit error, rather than silently turning
hybrid into keyword-only search. `partial` signals stale/source changes or
exhausted candidate/FTS scan budgets; this is a bounded shortlist, not a promise
of complete global recall.

## Worker contract and failure behavior

The existing `GET /v1/models` quality descriptor must report the actual model
ID, `status=ready`, `model_revision`, configured `dimensions`,
`preprocessing_revision`, `normalized`, `max_batch_size`, and `max_tokens`.
`POST /v1/embeddings` sends model=quality, input strings, purpose=query/document,
and the pinned model revision/dimensions/preprocessing revision. The response
must echo the same space and contain exactly one finite, nonzero vector per
unique input index. The adapter orders by index and normalizes before cosine.
Preprocessing covers query instructions, document formatting and truncation
policy; any change to the encoded representation requires a new revision.
The worker enforces actual token limits: character length is not a tokenizer.

The upstream 2B embedding model defaults to 2048 dimensions and supports
configurable MRL dimensions. The deployed descriptor determines the actual
space; the D100M 256D dimension does not apply to Qwen. See the
[official project](https://github.com/QwenLM/Qwen3-VL-Embedding) and
[official model card](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B).

Absent or unavailable quality workers return `503 QWEN_UNAVAILABLE`;
malformed/incompatible responses return `503 QWEN_PROTOCOL_ERROR`;
worker/pipeline contention returns `429 QUALITY_BUSY`; explicit input rejection
returns `422 QUALITY_INPUT_REJECTED`. There is a shared 120-second cooperative
quality request budget, checked between stages and before/after every encoding
batch, and passed to the HTTP adapter as a deadline. Exhaustion returns
`503 QUALITY_TIMEOUT` and releases the local concurrency gate. It does not
forcibly cancel work already executing on a remote worker. The legacy fast
shortlist operations retain their individual HTTP timeouts; a consumed overall
budget prevents further quality encoding. Quality has one in-flight request per
app process, no waiting queue, and a separate rate-limit bucket. Fast requests
do not acquire the quality gate. No quality failure is reported as fast success.

## Disposable cache and reauthorization

The private per-process cache stores only candidate vectors, with limits of
2048 entries, 32 MiB accounted storage and a 300-second TTL. Oversize entries
are not retained. Keys isolate catalogue instance, caller/ancestor/scope
context, table/row/source revision, path/table/schema context, chunk/content
hash, fast index/chunker revision, and Qwen model/revision/dimensions/preprocessing.
Query vectors and result lists are not cached. Restarts cause harmless misses.
Row/table cleanup invalidates cached candidates; edits, moves, restores and
reindex/model changes also miss through source/index/version keys.

Cache presence never grants permission. Fresh table/row ACL and current source
checks run after query encoding, before each document batch, after worker
responses before cache publication, and before returning results. They do not
reuse the pre-worker request ACL snapshot. Candidates deleted, changed, moved,
reindexed or made unreadable while waiting are discarded. SQLite connections
are closed before network waits; only short cache locks protect local entries.
Successfully encoded and reauthorized entries may remain cached even if a later
batch makes the overall request fail. `encoded_chunks` counts attempted chunks
whose encoding call completed within the budget, including chunks discarded by
a subsequent source/permission check; it does not count cache hits.

Metrics include `xelibrary_quality_requests_total`, `xelibrary_quality_errors_total`,
`xelibrary_quality_stage_seconds`, `xelibrary_quality_candidates_total`,
`xelibrary_quality_cache_total`, `xelibrary_quality_cache_evictions_total`,
`xelibrary_quality_cache_entries`, and `xelibrary_quality_in_flight`.
Labels are bounded stage/outcome/error values. Error logs contain request IDs
and error codes, not query/document text, caller identity, tokens or endpoints.

## Deployment acceptance still required

The local embedding service still advertises Qwen as not configured until #23
provides its real worker/facade. This change supplies the API adapter, pipeline,
cache, fusion, typed search responses and tests. Simulated test vectors verify
HTTP contracts and control flow; they do not establish model quality or
production throughput. Actual pinned weights, worker serving configuration,
service authentication, CPU/memory capacity and end-to-end Qdrant → Qwen
quality/latency/permission/fault acceptance must be verified separately before
claiming deployment completion. No weights are downloaded by these changes.
