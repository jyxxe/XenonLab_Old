# Issue 146: coalesce wallpaper slider saves

Baseline: `37dcb5ab678040b77e69d71342a6009990dae652`.

Only the gradient-angle and dim range inputs defer persistence. Each input still previews immediately through the shell's optimistic preference state. Keyboard steps share a 350 ms quiet-period debounce; keyup does not flush. During pointer dragging the timer is paused, and pointerup/cancel or blur flushes the final values. Leaving the appearance tab or closing Settings also attempts a flush. A hidden-page visibility event attempts a keepalive PATCH. This is best effort, not a guarantee on forced exit or failed networking.

The existing preference queue and version fence remain responsible for writes. Optional apply options distinguish preview-only updates from persistence and carry keepalive to the existing API. The UI displays pending/saving until the API resolves, shows saved only on confirmation, and keeps failed slider values for an explicit retry while the view remains open. A failed save after closing is reported through the existing shell toast; there is no durable background retry queue or request abortion.

Account/API lifecycle checks reject stale pending work before dispatch, suppress old responses after account changes, and discard pending range values on settings lifecycle changes. Parent shell layout cleanup prevents a child's close-flush from writing after the shell/account unmounts. API changes invalidate the current preference owner until the new account has loaded. Already sent requests are not cancelled; their responses cannot replace the new account's UI.

Other preference controls continue their immediate-save behavior. No backend/API schema, permissions, dependencies or migrations change. The pending widget-order PR167 and WorkWidget link PR165 use independent regions; integration must preserve them.

## Validation

From repository root:

```sh
uv run pytest
uv run ruff check xeonolab tests
uv run python scripts/export_openapi.py
node scripts/verification/wallpaper-slider-save/verify.mjs
node scripts/verification/wallpaper-slider-save/combinations/verify.mjs
```

Run `npm test` and `npm run build` inside `apps/desktop-web` after installing its locked dependencies.

Results: 479 Python tests passed with 19 existing warnings; 62 frontend tests passed; scoped Ruff, unchanged OpenAPI regeneration, build and diff check passed.

The tracked browser runner renders the actual DesktopShell, Settings and DesktopPreferences in loopback Google Chrome. API responses and per-account sessionStorage are explicitly synthetic; no production credentials/network/data are used. It verifies 14 checks with zero page errors:

- Thirty separate ArrowRight presses produce immediate 190-degree preview and exactly one final PATCH.
- Repeated keydown produces one PATCH after silence; keyup does not trigger per-step writes.
- A real pointer drag has no PATCH before release, previews the current dim value, and flushes one final PATCH on pointerup.
- Blur flushes pending values.
- A held response remains saving and does not claim confirmation.
- An older failed request cannot resurrect a value superseded by a newer queued save.
- An explicitly synthetic failure retains the final patch for successful retry.
- Leaving appearance flushes.
- A synthetic hidden visibility event records a keepalive attempt; it does not demonstrate reliable browser termination/network delivery.
- A single range click saves, and an ordinary theme control still saves immediately.
- Closing Settings flushes.
- Remount reloads the acknowledged final value from synthetic storage.
- Switching account/API cancels pending A slider values and B sends only B's new value.
- A held, already-sent B response cannot overwrite A after switching back.

Evidence is under `runtime/verification/issue146-wallpaper-slider/`: `browser-report.json`, `browser.log`, `slider-keyboard-preview.png`, `slider-saved.png`, `slider-account-isolation.png` and gate logs. Screenshots are visually reviewed. The runner is a manual acceptance command, not a new CI job. Repeated keydown is generated through real Chrome keyboard events; it is a controlled repeat sequence, not a measured OS repeat rate.

## Delivery and rollback

Local implementation only; no production deployment or production persistence guarantee. No new production dependency. Rollback reverts the four frontend files and acceptance artifacts; no data migration/rollback is required.

## Cross-control save confirmation

Slider pending/failure status is independent of ordinary settings confirmations. A failed angle save followed by a successful theme save retains the angle preview and retry entry until the angle itself is acknowledged. Full preference responses omit range fields that the responding PATCH did not write, while preserving derived server fields such as custom image availability. Per-field edit tokens distinguish successive changes even when their numeric values repeat; explicit same-field defaults replace pending values. Stale account failures no longer emit a toast into the new account.

The additional tracked combinations runner covers eight groups: theme success after slider failure (including another failed retry and successful retry/remount), both orders of two-slider interleaving, same-valued newer edits against old success/failure, restoring defaults against both outcomes, and normal image upload/removal with derived availability and actual desktop Blob background. API responses/storage/image conversion are synthetic, using a valid tiny PNG and WebP fixture. Only the debounce callbacks in the interleaving tests are explicitly controlled; the fixture honors timer cancellation and tests old outcomes before the new debounce runs. It reports component hashes and zero page errors. These checks supplement the original fourteen, without claiming production persistence or image conversion validation.
