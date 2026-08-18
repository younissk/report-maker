# Installing on a Mac

```bash
make install
```

That is the whole thing. From a fresh clone it ends with **report-maker.app in
Applications** and the `report-maker` command on your PATH, so the morning starts
with ⌘-space rather than a terminal.

```
open -a report-maker
```

## What it does, in the order it does it

Every step announces itself, because the two slow ones look exactly like a hang.

1. **Checks prerequisites** — `typst`, `node`, `python3` 3.11+. It reports what is
   missing and the one command that fixes it (`brew install typst`), and installs
   nothing itself. A script that reaches for Homebrew on your behalf is a script
   that puts things on your machine you did not ask for.
2. **Links the CLI** — `~/.local/bin/report-maker` → `bin/report-maker` in this
   checkout. It creates `~/.local/bin` if it is not there. If that directory is
   not on your PATH it prints the exact line to add and which file to add it to,
   and stops there: it never edits a shell profile. A symlink already pointing
   somewhere else is repointed and the old target named; a *real file* at that
   path aborts the install rather than being overwritten.
3. **Builds the app** — `npm install` only if `node_modules` is absent, then
   `npm run build`.
4. **Packages it** — `electron-builder --mac dir`, for this machine's
   architecture only. The `dir` target, not `dmg`: a disk image exists to be
   downloaded, and nothing is downloaded when the machine that builds it is the
   machine that runs it. It is also much faster, and naming the slice keeps the
   output directory predictable instead of leaving two to choose between.
5. **Copies it in** — `ditto` into a staging folder beside the destination, a
   check that the copy still claims our bundle id, then one `mv`. `ditto` merges
   into an existing directory rather than replacing it, so installing straight
   over the top would leave files from the previous version inside the bundle.

Re-running is the normal case. Every step is a no-op when it is already true, so
`make install` after a `git pull` is how you update.

## What it writes, and nowhere else

| path | what |
|---|---|
| `/Applications/report-maker.app` | the app (or `~/Applications`, see below) |
| `~/.local/bin/report-maker` | a symlink to `bin/report-maker` in this checkout |
| `app/node_modules`, `app/out`, `app/dist` | build output, inside the repo |

The symlink means the CLI is **this checkout**, live. Pull the repo and the
command changes with it; the app is a copy and needs `make install` again.

Nothing is written to your shell profile, your login items, `/usr/local`, or
anywhere requiring a password. Your vaults are not touched — they are folders on
your disk and this installs a tool that reads them.

## What it does not do

- **No sudo.** `/Applications` is writable by an admin user on a stock macOS, so
  it does not need privilege. If yours is not writable the installer says so and
  installs to `~/Applications` instead, which Spotlight and Launchpad index the
  same way. Do not reach for `sudo` to force the other one.
- **No signing, no notarisation.** `identity: null` in
  `app/electron-builder.yml`; electron-builder logs `skipped macOS code signing`
  on every build. The bundle carries only the ad-hoc signature the prebuilt
  Electron binary came with — it is not signed with a Developer ID and Apple has
  never seen it.
- **No deleting things it cannot identify.** Before replacing or removing
  `/Applications/report-maker.app`, the installer reads `CFBundleIdentifier` out
  of its `Info.plist` and requires `com.younisskandah.reportmaker`. Anything else
  — a different app, an unreadable bundle, a symlink — aborts with a message
  rather than an `rm -rf`.

## Gatekeeper

An unsigned app you built yourself **opens normally**. Quarantine is not a
property of signing; it is an extended attribute (`com.apple.quarantine`) that
browsers, mail clients and messaging apps attach to files they download.
`electron-builder` writing to your own disk attaches nothing, so there is nothing
for Gatekeeper to evaluate and no dialog to click through.

If you ever move the app to **another** machine — AirDrop, a zip from a release
page, `make app-dist` output handed to a colleague — that copy *is* quarantined,
and because it is unsigned macOS blocks the first launch. Recent versions no
longer take right-click → Open as consent; it has to be allowed in **System
Settings › Privacy & Security**, or the attribute stripped on the receiving
machine:

```bash
xattr -dr com.apple.quarantine /Applications/report-maker.app
```

That line is for the downloaded case only. Running it after `make install` does
nothing, because there is no attribute there to remove.

## Undoing it

```bash
make uninstall
```

Removes the bundle from `/Applications` **and** `~/Applications` (after the same
identity check in each — it will refuse to delete something that is not ours) and
the `~/.local/bin` symlink, if that symlink still points at this checkout. It
leaves your vaults, your settings and the repository alone.

## Building a real distributable

`make install` is for this machine. When the artefact is for somebody else:

```bash
make app-dist        # → app/dist: report-maker-<version>-<arch>.dmg, and a zip
```

Same configuration, same bundled engine, still unsigned. The five environment
variables a signed, notarised build would take are named in
`app/electron-builder.yml`.
