/**
 * The PATH a login shell would have had.
 *
 * Under `npm run dev` the app inherits the terminal that started it, so
 * `report-maker`, `typst` and the node that drives the mermaid renderer are all
 * already on PATH and none of this matters. Launched from Finder or from
 * /Applications, a packaged app gets launchd's default —
 * `/usr/bin:/bin:/usr/sbin:/sbin` — and nothing else. Homebrew is not on it, and
 * neither is anything a version manager installed.
 *
 * The failure that produces is the worst kind available. A spawn fails with
 * ENOENT two layers down, which surfaces as "typst crashed" rather than "typst
 * is not on this app's PATH", while `report-maker doctor` run from a terminal
 * reports green the entire time — because the terminal has the PATH the app
 * does not. Nothing about the symptom points at the cause.
 *
 * So before the first spawn, ask the user's login shell what PATH it would have
 * built, and merge the answer in. Two things about that are deliberate.
 *
 * **The probe is best-effort.** A login shell runs the user's rc files, which
 * can print a banner, block on a network mount, wait on a prompt, or belong to
 * a shell that does not take `-ilc` at all. It gets one budget of five seconds
 * and its stdin closed, and if it produces nothing usable the app carries on
 * without it.
 *
 * **The fallback list is not the backup — it is the load-bearing half.** Those
 * five directories are where the tools actually are on a Mac, and unlike the
 * probe a hardcoded list cannot hang, cannot be restricted and cannot be
 * sabotaged by somebody's `.zshrc`. It is merged whether or not the probe
 * worked.
 */

import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { delimiter, join } from 'node:path'
import type { PathProbe } from '../shared/types'

/**
 * Where a Mac keeps the things this app spawns: Homebrew on Apple silicon,
 * Homebrew on Intel, the `~/.local/bin` the README tells you to symlink the CLI
 * into, cargo's shims, and the older Homebrew prefix some machines still carry.
 */
const FALLBACK = [
  '/opt/homebrew/bin',
  '/usr/local/bin',
  join(homedir(), '.local', 'bin'),
  join(homedir(), '.cargo', 'bin'),
  '/usr/local/homebrew/bin'
]

/** Printed immediately before the PATH, so the value can be found in output a
 *  chatty rc file has scribbled over. */
const SENTINEL = '__report_maker_path__'

const BUDGET_MS = 5000

function split(value: string | undefined): string[] {
  return (value ?? '').split(delimiter).filter(Boolean)
}

/**
 * Append the entries that are not already there, keeping the existing order.
 * Existing entries win because they were set deliberately — by a launcher, by a
 * developer's terminal, or by `REPORT_MAKER_ROOT`-style local surgery — and a
 * recovered PATH is a correction for what is *missing*, not a replacement for
 * what somebody chose. Returns the entries it actually added.
 */
function extend(extra: string[]): string[] {
  const current = split(process.env.PATH)
  const added = extra.filter((dir) => dir && !current.includes(dir))
  if (added.length) process.env.PATH = [...current, ...added].join(delimiter)
  return added
}

/**
 * Ask the login shell. Resolves to its PATH entries, or to null with a reason —
 * a probe that fails is an ordinary outcome here, not an error to propagate.
 */
function askShell(shell: string): Promise<{ entries: string[] } | { error: string }> {
  return new Promise((done) => {
    // stdin is closed rather than inherited: an interactive shell that decides
    // to read from it would otherwise sit there until the budget expires.
    const child = spawn(shell, ['-ilc', `echo -n "${SENTINEL}:$PATH"`], {
      stdio: ['ignore', 'pipe', 'ignore']
    })

    let out = ''
    let settled = false
    const finish = (result: { entries: string[] } | { error: string }): void => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      done(result)
    }

    const timer = setTimeout(() => {
      child.kill('SIGKILL')
      finish({ error: `${shell} did not answer within ${BUDGET_MS} ms` })
    }, BUDGET_MS)

    child.stdout.on('data', (chunk) => (out += chunk.toString()))
    child.on('error', (err) => finish({ error: err.message }))
    child.on('close', () => {
      // Take the *last* sentinel: an rc file that echoes the command line would
      // otherwise hand us its own copy of the marker instead of the real value.
      const at = out.lastIndexOf(`${SENTINEL}:`)
      if (at < 0) return finish({ error: `${shell} printed no PATH` })
      const tail = out.slice(at + SENTINEL.length + 1).split('\n')[0]
      const entries = split(tail.trim())
      finish(entries.length ? { entries } : { error: `${shell} printed an empty PATH` })
    })
  })
}

let pending: Promise<PathProbe> | null = null
let last: PathProbe | null = null

async function recover(): Promise<PathProbe> {
  const shell = process.platform === 'darwin' || process.platform === 'linux'
    ? (process.env.SHELL ?? null)
    : null

  // The probe runs before the fallback so that the shell's own ordering
  // survives: if somebody's PATH puts a version manager ahead of Homebrew, the
  // app should resolve `node` the same way their terminal does.
  let fromShell: string[] = []
  let detail = 'no login shell to ask'
  if (shell) {
    const answer = await askShell(shell)
    if ('entries' in answer) {
      fromShell = extend(answer.entries)
      detail = `${shell} answered with ${answer.entries.length} entries`
    } else {
      detail = answer.error
    }
  }

  // Only directories that exist: a PATH padded with paths that are not there
  // costs a stat on every lookup and tells a reader of `doctor` nothing.
  const fromFallback = extend(FALLBACK.filter((dir) => existsSync(dir)))

  last = {
    shell,
    ok: fromShell.length > 0,
    detail,
    fromShell,
    fromFallback,
    path: process.env.PATH ?? ''
  }
  return last
}

/**
 * Recover the PATH, once. Callers await this before spawning anything; the
 * window is created without waiting for it, because a shell that hangs must
 * cost a slow first build and never a blank screen.
 */
export function hydrate(): Promise<PathProbe> {
  pending ??= recover()
  return pending
}

/** What the last recovery found, for the diagnostics panel. Null until it has
 *  finished — the caller there awaits `hydrate()` first. */
export function probed(): PathProbe | null {
  return last
}
