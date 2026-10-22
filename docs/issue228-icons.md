# WebUI icons — Issue 228

The desktop and native Library interface use a shared set of original SVG icons
instead of platform-dependent text glyphs. Familiar book, chat, code, task, monitor,
network and settings shapes identify the seven native applications consistently in
the Dock, LaunchPad, command palette, shortcuts and app settings.

The design follows the Apple [Icons guidance](https://developer.apple.com/design/human-interface-guidelines/icons):
simple recognizable metaphors, consistent detail and stroke weight, optical padding,
and vector rendering. No Apple artwork or external icon dependency is included.

## Rendering contract

`apps/desktop-web/src/design-system/Icon.tsx` uses a 24-unit viewBox, 1.75-unit
rounded strokes and `currentColor`, so icons inherit theme and interaction colors.
Interface symbols use 14–20px sizes; larger empty states and app launchers retain
their existing icon plates and padding. Dock symbols follow small/medium/large
preferences and the compact breakpoint.

Known legacy glyphs map to semantic vectors at render time. The backend registry's
`icon: string` field and stored values are unchanged. Unknown strings retain their
literal text and intrinsic width, including emoji and strings such as `constructor`;
React nodes passed to existing primitives remain React nodes. This is a local
rendering mapping, not an image URL loader or HTML interpreter.

Icons are decorative by default (`aria-hidden`, nonfocusable SVG). Existing button
names, keyboard actions, selected states and shortcut notation remain available.
An explicit `label` creates a named image when the icon itself carries meaning.
The Library's icon-only new-table action now has an explicit accessible name.

## Local acceptance

Run from `apps/desktop-web` with an installed Chromium:

```sh
CHROME_BIN=/usr/bin/google-chrome node --test tests/browser-acceptance-icons.test.mjs
CHROME_BIN=/usr/bin/google-chrome npm run test:browser-regression
npm test
npm run build
```

The icon acceptance test exercises production components using the existing
synthetic shell/Library fixtures and a small custom-text fixture. It checks seven
native app icons, keyboard opening/dismissal, named command activation, light/dark
and 390px rendering, attachment and Inspector actions, custom text layout and named
images. Browser evidence is saved under `runtime/verification/issue228`.

No production service or deployment is exercised by these fixtures. This change
does not configure OpenWebUI, model workers, Lean verification or other service
dependencies; their existing unavailable states remain visible.
