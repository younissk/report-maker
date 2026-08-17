# app/

A desktop app over the engine: vault switcher, file tree, editor, PDF viewer.

It ships with no vault, the way an editor ships with no document. First run shows
**Open a vault…** / **Create a vault…**; a vault is any folder holding
`report-maker.toml`, anywhere on the disk, and the app remembers the ones you have
opened (a JSON file in userData — nothing else).

A vault can also be named at launch — `npm run open -- ~/Documents/Reports`, or
`make open V=~/Documents/Reports` from the repo root, or `RM_OPEN_VAULT` in the
environment. That is the path the smoke test drives, so it stays working.

```bash
cd app
npm install
npm run dev                                  # electron-vite dev, hot reload
npm run build && npm run open -- <vault>     # launch it on a vault
npm run build                                # typecheck both projects + bundle
npm run smoke                                # launch against examples/demo-vault, screenshot
node scripts/smoke.mjs none out/welcome.png  # the first-run screen, no vault
```

Electron + React + Tailwind v4 + [shadcn/ui](https://ui.shadcn.com) components,
with CodeMirror 6 as the editor. Chromium's own PDF viewer renders the report.

## What it is not

It is not where the logic lives. Every question about a vault — what reports
exist, what a build produces, whether the citation rule holds — is answered by
shelling out to `report-maker`, exactly as a terminal would:

```
renderer  ──IPC──▶  main  ──spawn──▶  report-maker -C <vault> …
```

`engine.locate()` finds the CLI in the order a user would expect to win:
`REPORT_MAKER_BIN`, then `REPORT_MAKER_ROOT`, then a copy bundled in a packaged
build (`resources/engine-src`), then the repo this app was built from, then
`report-maker` on `PATH`. The first-run screen prints which one it found.

That is deliberate. A desktop app that reimplemented any of it would be the
thing that drifts, and the engine would stop being the single answer to "what
does this vault contain".

## Layout

```
src/shared/     the IPC vocabulary — types both sides compile against
src/main/       engine bridge, vault list, guarded file access
  engine.ts     spawns report-maker, parses --json output
  vaults.ts     which folders you have opened (a JSON file, no database)
  tree.ts       the vault walked as a tree, and the path guard
src/preload/    contextBridge: the only channel the renderer gets
src/renderer/   React — App, FileTree, Editor, Viewer, VaultSwitcher
scripts/smoke.mjs   launch-and-capture, the app's own smoke test
```

## Three things worth knowing

**The renderer can only do what preload exposes.** Context isolation is on, node
integration is off, and every path the renderer sends back is checked to sit
inside the active vault before it is read or written (`tree.within`).

**The viewer takes the PDF as a blob, not a `file://` URL.** In dev the renderer
is served over http, where a `file://` frame is blocked; reading the bytes over
IPC and handing Chromium a blob behaves the same in dev and in a packaged build.

**A report's design is read from its own import line.** `locate()` matches the
open file against the report ids the engine reports, so the viewer knows which
PDF belongs to the file being edited without the app keeping its own index.

## Shortcuts

| key | does |
|---|---|
| `⌘S` | save the open file |
| `⌘B` | save, then `report-maker all <report> --warn-only`, then reload the PDF |

`Check` runs the citation rule on the open report and prints the findings tail in
the header. The full output is what the CLI prints — this is a status line, not a
replacement for reading it.

## Verifying a change

There is no page to curl, so the app screenshots itself:

```bash
npm run smoke                            # → out/smoke.png, against this repo's vault
node scripts/smoke.mjs ~/vaults/work     # against another vault
```

A non-zero exit or a missing PNG means the window never finished loading.
