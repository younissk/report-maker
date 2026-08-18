# app/

A desktop app over the engine: vault switcher, file tree, editor with an evidence
rail, sources panel, problems drawer, dashboard, brand studio and PDF viewer.

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
npm run smoke                                # launch, drive, screenshot each screen
npm run dist                                 # package: macOS dmg + zip, unsigned
node scripts/smoke.mjs none out/welcome.png  # the first-run screen, no vault
```

Electron + React + Tailwind v4 + [shadcn/ui](https://ui.shadcn.com) components,
with CodeMirror 6 as the editor. Chromium's own PDF viewer renders the report.

## What it is not

It is not where the logic lives. Every question about a vault — what reports
exist, what a build produces, whether the citation rule holds, whether a source
still says what it said — is answered by shelling out to `report-maker`, exactly
as a terminal would:

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
  settings.ts   preferences, global rather than per-vault
  watch.ts      the live-rebuild child process, streamed to the window
  env.ts        the PATH a login shell would have had — see below
  fonts.ts      the families this machine actually has, for the font pickers
src/preload/    contextBridge: the only channel the renderer gets
src/renderer/   React — App plus the panels below
  lib/          one module per subject: commands, lint, rail, sources, csv,
                mermaid, notes, search, designs, brand, git — the renderer's
                logic lives here and the components stay components
scripts/smoke.mjs   launch, drive, capture: the app's own smoke test
build/icon.png      the app icon (electron-builder's `buildResources`)
electron-builder.yml  how a distributable is assembled
```

## The panels

| panel | what it is |
|---|---|
| **File tree** | the vault as folders. `⌘1` hides it |
| **Editor** | CodeMirror over `main.typ` / `sources.yml`, with a lint gutter fed by `check --json` and an evidence rail on the right, one block per line, coloured cited / assessed / unmarked from `score --json` |
| **CSV editor** | a `data/*.csv` as a grid, opened in place of the text editor. The file is the truth and the grid is a view over it — dialect and untouched rows are written back byte for byte, so a one-cell edit is a one-line diff |
| **Mermaid editor** | a `.mmd` beside its live render, drawn from `diagrams --prepare --json`, so the preview and the build share their whole input |
| **Find** | `find --json` across the vault — reports, sources, snapshots, diagrams. A tab beside the tree, `⌘⇧F` |
| **Notes** | the report's pad: `todos.md`, `notes.md`, and the `// TODO:` comments harvested out of `main.typ`. A tab beside the tree, `⌘⇧T` |
| **Designs** | the templates available here, where each came from, and which reports use it |
| **Sources** | the report's `sources.yml` as rows — key, title, type, use count, snapshot state. Orphans are muted with a `W001` chip. "Add source…" runs `cite`, and typing `@` in the editor completes against the same list |
| **Problems** | a bottom drawer with three tabs — Findings (`check`), Build (the last Typst stderr) and Evidence (`verify` drift). Clicking a row opens the file at that line |
| **Viewer** | the built PDF, reloaded after every build. `⌘2` hides it |
| **Dashboard** | one card per report, from `manifest.json` + `list --json`: cover thumbnail, design, date, built/stale, evidence density, findings. Shown when nothing is open |
| **Brand studio** | the brand pack as a form — colours, fonts, sizes, spacing, logo — beside a live specimen rebuilt by `brand preview` a moment after every edit |
| **Timeline** | the commits touching the open report, and the semantic `diff` between any two of them |
| **Palette** | `⌘K` over every file and every command; `⌘⇧P` for commands alone |
| **Settings** | `⌘,` — appearance, editor, build, git, and an About page naming the engine it found |

The evidence rail and the lint gutter both read the **saved** file. While the
buffer is dirty they fade rather than lie: what you are looking at is the last
thing the engine actually saw.

## Shortcuts

| key | does |
|---|---|
| `⌘S` | save the open file |
| `⌘B` | save, then `report-maker all <report> --warn-only`, then reload the PDF |
| `⌘⇧C` | check the citation rule on the open report |
| `⌘N` | new report — title, group, design, date |
| `⌘K` | palette: files and commands |
| `⌘⇧P` | palette: commands only |
| `⌘⇧D` | vault dashboard |
| `⌘,` | settings |
| `⌘1` / `⌘2` | toggle the file tree / the PDF pane |
| `⌘⇧M` / `⌘3` | toggle the problems panel |
| `⌘⇧E` / `⌘⇧F` / `⌘⇧T` | the Sources / Find / Notes tab in the sidebar |
| `⌘⇧B` | brand studio |

Every shortcut in that table is a row in `src/renderer/src/lib/commands.ts` and
nowhere else, so the palette can never advertise a key that does not fire — the
tab and pane shortcuts included, which is what lets the palette show them.
`⌘K` and `⌘⇧P` belong to the palette itself and are handled in `Palette.tsx`,
since they have to work whatever holds focus. The single exception is `⌘3`: it is
a *second* key for a row that already has `⌘⇧M`, so it lives in `App.tsx`. It is
`⌘3` and not `⌘⇧3` because macOS takes that one for a screenshot.

## Settings

Preferences are **global, not per-vault** — they describe how you like to work,
and a vault has to stay a plain folder anyone can open without inheriting
somebody's editor font. They live in `settings.json` next to the vault list in
userData.

- **Appearance** — theme (system / light / dark), accent colour, density.
- **Editor** — font family and size, line height, line numbers, word wrap, tab
  size, active-line highlight, bracket matching, evidence rail, lint gutter,
  syntax theme. Changes preview live as you drag.
- **Build** — autosave interval (off by default), build on save, live watch, and
  how long the editor waits after your last keystroke before re-running
  `check --json` and `score --json` (600 ms).
- **Git** — auto-commit, auto-push, the debounce, and the commit message
  template.
- **About** — what this window is running, and which `report-maker` it found.

Two more groups live in the file with no screen of their own, because nothing
about them is worth a control. **`startup`** records the vault you were last in
and whether to reopen it — on by default, since the app is a place you come back
to, and a vault named on the command line or in `RM_OPEN_VAULT` still wins over
it. **`layout`** records which panes are showing and how wide each is; it sits
here rather than in localStorage because it is a preference like any other,
which should survive a cleared web store, travel with the rest of the settings
file and be resettable from the same button. `panes` is an open map keyed by
panel id, so a layout written by a build with more panes than this one survives
being read by this one.

The syntax theme defaults to **`auto`**, which follows the app chrome. The two
`report-*` themes name their own polarity, and that is the trap a default must
not walk into: a light machine on `theme: system` used to get light chrome
wrapped around a dark editor, which looks like a design decision rather than a
bug and so never gets reported.

A patch is merged *over the defaults*, one level at a time, because a settings
file written by an older build is missing keys a newer one reads — and reading
`undefined` for `editor.fontSize` would be a worse bug than any it could cause.

## The vaults you have opened

`vaults.json`, beside the settings, and it holds three things per folder: the
path, when it was last current, and whether you pinned it. Nothing about a vault
is stored — not its reports, not its designs, not its name beyond the folder's
own. Everything else is a `report-maker` subprocess away, and a cached copy here
would be a second answer to a question the engine already answers.

The list is ordered pinned first, then most recently opened, and it is the same
list in three places: the Welcome screen, the switcher, and **File › Open
Recent** in the menu bar, which is the reason the app builds a menu at all. The
palette shows the first eight — a palette whose first page is thirty folders is a
palette you stop opening.

A vault that has gone missing is **kept**, flagged, and offered a remove action.
Dropping it silently is wrong in the common case: a vault on an unmounted drive
or a sleeping network share is not a vault to forget, and a list that quietly
shrinks makes the app look like it lost your work.

## PATH, and the failure it causes

Under `npm run dev` the app inherits the terminal that started it, so
`report-maker`, `typst` and node are already on PATH and none of this matters.
Launched from Finder or from `/Applications`, a packaged app gets launchd's
default — `/usr/bin:/bin:/usr/sbin:/sbin` — and nothing else. Homebrew is not on
it, and neither is anything a version manager installed.

That produces the worst failure available. A spawn fails with `ENOENT` two layers
down, which surfaces as "typst crashed" rather than "typst is not on this app's
PATH", while `report-maker doctor` in a terminal reports green the whole time,
because the terminal has the PATH the app does not. Nothing about the symptom
points at the cause.

So `src/main/env.ts` asks the user's login shell what PATH it would have built,
and merges the answer in before the first spawn. Two things about that are
deliberate. The probe is **best-effort**: a login shell runs rc files that can
print a banner, block on a network mount or wait on a prompt, so it gets five
seconds and its stdin closed, and the app carries on without it. And the
hardcoded fallback list — `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`,
`~/.cargo/bin` and the older `/usr/local/homebrew/bin` — is **not the backup, it
is the load-bearing half**: those are where the tools are on a Mac, and unlike
the probe a fixed list cannot hang, cannot be restricted and cannot be sabotaged
by somebody's `.zshrc`. It is merged whether or not the probe worked.

## Auto-commit and auto-push

Off by default, both of them. Turned on, the loop is: a successful save or build
starts a `git.debounceMs` timer (4 s), and when it expires the app runs
`report-maker sync` — `--push` only if **auto-push** is also on.

The rules that make that safe are in the engine's `sync`, not in the app, which
is the point: the same guarantees hold from a terminal, from CI and from here.

- Never `--force`, ever.
- Never pushes without a configured upstream — it says to set one with
  `git push -u origin <branch>` and stops.
- Never pushes from a detached HEAD.
- Never pushes when the branch is behind the remote — it says so and stops,
  rather than merging on your behalf.
- Only ever stages paths inside the vault.
- Default message: `report-maker: <n> file(s) — <YYYY-MM-DD HH:MM>`.

Auto-push is the one setting in this app that does something the outside world
can see. It is a deliberate, one-time decision, and the status bar shows the
branch, the dirty count and the ahead/behind pair so the result is never a
surprise.

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

## Verifying a change

There is no page to curl, so the app drives and screenshots itself. The smoke
test launches the built app with Chromium's remote debugging port open, presses
the same keys a person would, and captures each screen:

```bash
npm run smoke                            # → out/smoke*.png, against this repo's vault
node scripts/smoke.mjs ~/vaults/work     # against another vault
```

It writes eleven PNGs: `out/smoke.png` and one variant per surface — `-notes`,
`-sources`, `-find`, `-csv`, `-mermaid`, `-editor`, `-dashboard`, `-designs`,
`-brand`, `-settings`. The order is not cosmetic. The three sidebar tabs come
first, because the sidebar only exists on the editor route and a tab shortcut is
a no-op once a full-window screen is up; the `-editor` row then puts `main.typ`
back so the screens after it start where they always did. It **fails** on any
of:

- the app never opening a debugging target (it did not start),
- electron exiting on its own,
- a `console.error` or an uncaught exception in the renderer,
- a surface that never appeared after its shortcut,
- the palette highlighting a different row from the one being asked for — typing
  "designs" used to highlight View ▸ Designs and run Build ▸ Stage the designs,
  so the highlighted row is now checked *before* Enter is pressed rather than
  inferred from where the run ended up,
- a missing or empty screenshot.

That list is the whole value of it. A smoke test that only ever writes a PNG
proves nothing beyond "electron starts", so if it cannot fail it is not worth
running. Two escape hatches exist for when the app is mid-rebuild:
`RM_SMOKE_SURFACES=0` skips the driving, and `RM_SMOKE_IGNORE=<regex>` drops
matching console errors.

## Packaging

```bash
npm run dist         # → app/dist: report-maker-<version>-<arch>.dmg, and a zip
npm run dist:dir     # unpacked, for a quick look inside the bundle
make app-dist        # the same thing from the repo root
```

`build/icon.png` is the app icon: 1024², the brand pack's display face
(`fonts.display`, Didot) set in white on `colors.accent-deep`, with the
superscript numeral that is this tool's whole argument. It is derived from
`engine/brand/brand.json` rather than invented, the same rule the reports follow.
`directories.buildResources: build` is how electron-builder finds it; without it
the log reads "default Electron icon is used" and the installed app is a generic
Electron diamond in the Dock.

`electron-builder.yml` builds macOS `dmg` and `zip` for `arm64` and `x64`. The
app is a front end over a Python engine, so the bundle carries one:
`extraResources` copies `engine/` and `bin/` to
`Contents/Resources/engine-src`, which is precisely where `engine.locate()`
looks in a packaged build. It stays a plain checkout rather than a frozen binary
because the engine is standard-library Python — copying the files *is* the build
step — and `REPORT_MAKER_ROOT` still wins over it for anyone running from source.

The build is **unsigned and un-notarised** (`identity: null`). Signing needs
credentials nobody should guess at; `app/electron-builder.yml` names the five
environment variables a real one takes (`CSC_LINK`, `CSC_KEY_PASSWORD`,
`APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`). Until then, the
artefact runs on the machine that made it and Gatekeeper will complain on any
other.
