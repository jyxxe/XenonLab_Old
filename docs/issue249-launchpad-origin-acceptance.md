# LaunchPad external app origins (#249)

LaunchPad reads the existing DesktopApp origin supplied by mergeApps. Each
external card adds a short host label (including a nondefault port), retaining
its title and existing external/recent category. Its accessible option name
includes that host; hover and aria-description give the complete canonical
origin. URL parsing excludes user-info, path, query and fragment. Missing or
malformed/non-web origins retain the generic card metadata; native apps get
no new source row. This display helper does not modify registered origins,
entry URLs, permissions, iframe policy or launch behavior.

Hosts wrap within the existing grid/card width. Long titles keep their current
ellipsis. Existing search still matches title/id/description, including origin
keywords, and clicks/keyboard launch the original app ID. CommandPalette and
its existing description subtitle remain unchanged. No new API or request,
bitmap, dependency, persistence or production configuration is introduced.

In apps/desktop-web:

```sh
npm test
npm run build
CHROME_BIN=/usr/bin/google-chrome node --test tests/browser-acceptance-launchpad-origin.test.mjs
```

The Chrome fixture uses actual mergeApps, LaunchPad, CommandPalette, design
tokens/primitives and shell CSS with synthetic registry data. It checks two
same-title external apps with different hosts, exact host-to-ID mapping,
accessible/hover origins, recent labels, mouse and keyboard launch, title/ID/
origin search, a single/differently titled app, native apps, hidden-permission
registry filtering, and 360px long-host/title layout. Palette retains origin
subtitles and launches the intended ID. No external origin or production
registry/identity is accessed. Unit cases additionally verify safe canonical
URL display and malformed/non-web/native fallbacks.
