# LLM Gateway chat streaming

`POST /v1/chat/completions` keeps the fixed public model ID `deepseek-flash`
and forwards `stream: true` as a live provider SSE stream. The provider key stays
server-side. Normal JSON completions retain their JSON response shape.

The gateway bounds the complete request/response lifetime to 600 seconds by
default, request JSON to 1 MiB, streamed provider bytes to 8 MiB, and concurrent
chat requests to 8 per process. `LLM_STREAM_TIMEOUT_SECONDS` accepts 1–3600,
`LLM_REQUEST_MAX_BYTES` 1–8 MiB, `LLM_STREAM_MAX_RESPONSE_BYTES` 1–64 MiB, and
`LLM_STREAM_MAX_ACTIVE` 1–64. With multiple workers the active limit scales per
process: total concurrency can reach the configured value multiplied by worker
count.

Before response headers, capacity exhaustion returns HTTP 429 with
`Retry-After: 5`, an oversized body returns 413, a slow request body returns
408, a provider failure returns 502, and the overall provider deadline returns
504. Once SSE headers have been sent, provider errors, timeouts, malformed or
oversized streams are represented by a safe OpenAI-style `data: {"error":...}`
event; the gateway does not append a success `data: [DONE]` marker after such an
error. Clients must treat that event as a failed completion.

The fake-provider tests verify incremental SSE forwarding, tool-call chunks,
disconnect cleanup, capacity rejection, and safe error events. Actual
OpenWebUI-version compatibility and a production model-stream acceptance run
remain unverified; complete those checks before treating this as production
ready.
