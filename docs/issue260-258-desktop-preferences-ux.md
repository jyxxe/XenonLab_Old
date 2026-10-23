# Desktop preferences UX fixes (#260, #258)

Two small, user-visible defects in the desktop preference panel, fixed together
because they share the same component and the same acceptance surface.

## #260 — readable preference save errors

`DesktopPreferences` rendered save failures with `String(error)`, so an
`APIRequestError` produced `保存失败：APIRequestError: invalid preference or
timezone`: the server's readable message was prefixed with an internal class
name. The same leak existed in the wallpaper upload/remove messages, the layout
save message and the shell's preference-save toast.

`api-error.js` now exports `readableErrorMessage(error)`, which returns the
server-provided message (or a plain string / generic fallback) and never the
class name. The preference panel and the shell toast use it. Server validation
and persistence behaviour are unchanged: an unsupported IANA timezone is still
rejected with 422 and is not stored, and the API error object keeps its status,
code and request id for callers that need them.

## #258 — visible 12-item shortcut cap

The shortcut-app selector saved at most 12 entries but neither showed the cap nor
stopped a 13th selection: checking another app silently dropped the oldest saved
one. `desktop-shortcut-limit.js` now owns the rule:

- `toggleShortcutApp(selected, id)` removes an existing id, appends a new one, or
  reports `blocked: true` when the list is already at `SHORTCUT_LIMIT` (12) and
  leaves the saved list unchanged in that case.
- `shortcutLimitHint(selected)` returns `已选 n/12 项；最多 12 项，满额时请先取消一个已选应用。`
- The panel shows that hint with the live count, disables unchecked options when
  the list is full, explains that an existing entry must be removed first, and
  reports a blocked toggle in the preference status line.

Behaviour kept: the cap itself, the saved ordering, the server-side constraints,
the "no selection shows the first four available apps" default, and the fact that
opening the settings panel never writes preferences.

## Validation

```sh
cd apps/desktop-web
npm test                     # includes the new helper and error-message tests
npm run build                # tsc --noEmit && vite build
CHROME_BIN=... node --test tests/browser-acceptance-shell.test.mjs
CHROME_BIN=... npm run test:browser-regression
```

The shell acceptance test now also asserts that the shortcut hint shows
`已选 0/12` before a selection and `已选 1/12` after selecting an app, and the
existing failed-save/recovery assertions keep passing with the readable message.
