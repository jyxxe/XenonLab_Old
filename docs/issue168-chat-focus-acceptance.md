# Issue #168: Ask AI destination focus

Baseline `2aabf623aa88f68c0050774ddb03f2773681abfe`; branch `codex/general-issue168-chat-focus-20261003`.

Before confirmation, Ask AI lives inside a source-window Modal. Previously confirmation closed that Modal and opened Chat synchronously; Modal passive cleanup then restored its previous source focus, causing WindowFrame to raise Library over Chat. Confirmation now queues the draft in local state. A passive effect delivers the existing shell app intent after unmount cleanup restores the source focus. This orders the handoff using React's lifecycle, with no timeout, sleep or changes to generic Modal/useFocusTrap behavior. Cancellation and Escape retain ordinary source-focus restoration.

Only LibraryApp's handoff state/effect/confirmation handler changed. Chat still receives an unsent draft card; the existing iframe sandbox, manual-copy/paste boundary and app-intent payload are unchanged. No automatic input or model sending. Integration must preserve unrelated PR #157/#160/#166/#170 changes; these are not hard dependencies.

## Verification

- `uv run pytest`: 429 passed, 19 existing warnings, 14.02 seconds.
- `uv run ruff check xeonolab tests`: passed.
- `uv run python scripts/export_openapi.py`: passed; schema unchanged.
- Frontend `npm test`: 62 passed, zero skipped.
- Frontend `npm run build`: TypeScript/Vite passed.
- Before: `CHAT_FOCUS_EXPECT=before CHROME_BIN=/usr/bin/google-chrome node scripts/verification/chat-focus/verify.mjs`: passed the expected-defect assertions in all three Chat states.
- After: `CHROME_BIN=/usr/bin/google-chrome node scripts/verification/chat-focus/verify.mjs`: passed all three states and interaction assertions.
- `git diff --check`: passed.

The test renders real DesktopShell, WindowFrame, LibraryApp and IFrameApp in local Chrome, using a contract-shaped synthetic XeLibrary API with UUID table/row IDs and a loopback Chat textarea. The original source was first exercised before editing; the persisted before mode also restores only the legacy synchronous confirmation handler through a test-only Vite transform for reproducible comparison. Before, every case was active=knowledge, copy center hit knowledge, and actual pointer copy was blocked until Dock recovery. After, new/visible/minimized Chat was active and visible, copy center hit Chat, pointer copy and collapse worked without Dock recovery. Native navigator.clipboard content matched the reviewed draft, the iframe input was initially empty, and real manual Ctrl+V pasted the exact draft after collapse. Permissions were granted only to the two loopback test origins. Card text still declared the draft unsent. Cancel and Escape both returned keyboard focus to Ask AI in Library, preserving its active window. Zero page errors.

Evidence: `runtime/verification/issue168-chat-focus/{before,after}/independent-browser-report.json`, before obstruction screenshots, after `*-handoff.png` showing the frontmost draft card, pasted iframe screenshots and gate logs. Final new-chat handoff screenshot was visually reviewed. The coordinator independently reproduced all three states using the original audit measurer as additional evidence under ignored `runtime/verification/issue168-parent-three-state/`.

These are local synthetic data/identity and Chat pages; they do not verify production OpenWebUI login, model availability, inference, user data or usage frequency. Browser verification used no production access or remote writes.

## Operations

No API/schema, permission, dependency or migration changes. Production still needs the existing configured Chat/OpenWebUI service; this patch does not deploy it or automate a send. Production deployment has not been performed. Rollback: revert the local pending handoff state/effect and restore the previous synchronous confirmation handler, plus test/document files.
