# Reviewed Qwen3-VL Embedding source

Upstream: https://github.com/QwenLM/Qwen3-VL-Embedding
Commit: 393e2978d27852b0d0230d6994f37f9c15bed73c
Original file: src/models/qwen3_vl_embedding.py (minimal serving hardening patch documented below)
Git blob: a2d4a73349c4648c5d66633eccd57e02aea1279f
SHA256: 0e447b6a80e05527f3fd437ab4f1617a6e7d0df102b8164348bbce787a41b267

The digest above identifies the original upstream bytes. Trailing whitespace in
the copied Python source and original README is normalized; Python AST is unchanged
by this whitespace-only cleanup. Local helper changes fix CPU,
force offline/safetensors/no remote code for both loaders, and replace exception-to-NULL
with a sanitized failure. The model class itself is unchanged; the unused check_model_inputs import/comment
is removed because Transformers 5.2.0 removed that symbol.

The original Apache-2.0 LICENSE and README are retained. Only Qwen3VLForEmbedding,
which defines the pretrained model's forward pass, is used by qwen_encoder.py.
The generic Qwen3VLEmbedder helper is NOT instantiated: its automatic GPU choice,
trust_remote_code flag, URL/path inputs, implicit truncation, and exception-to-NULL
vision fallback are not the XeonoLab serving contract. The wrapper fixes CPU,
local/offline safetensors loading, bounded inputs and fail-closed preprocessing.
No independent reranker model, downloads, inference credentials, or upstream CLI
are used. Real weights and optional runtime dependencies must be separately
provisioned and verified; tests using synthetic adapters are not Qwen inference.
