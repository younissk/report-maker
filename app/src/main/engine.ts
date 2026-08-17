/**
 * The bridge to the engine.
 *
 * The app runs no logic of its own: every question about a vault — what reports
 * exist, which designs, whether a build is stale — is answered by shelling out
 * to `report-maker`, the same way a terminal or a CI job would. That is the
 * payoff of a headless engine, and it means the desktop shell can never drift
 * from what the CLI does.
 *
 * The engine is *not* in the vault, and the vault is not in the app: one
 * installation serves every vault on the machine, so this file's other job is
 * finding the engine wherever it happens to be installed.
 */

import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { delimiter, join, resolve } from 'node:path'
import { app } from 'electron'

export type Run = { code: number; stdout: string; stderr: string; command: string }

export type Located =
  | { kind: 'script'; python: string; script: string }
  | { kind: 'binary'; path: string }

function onPath(name: string): string | null {
  for (const dir of (process.env.PATH ?? '').split(delimiter)) {
    if (dir && existsSync(join(dir, name))) return join(dir, name)
  }
  return null
}

/**
 * Where the engine is. Checked in the order a user would expect to win:
 * an explicit override, a copy bundled with a packaged app, the repo this app
 * was built from, then whatever is on PATH.
 */
export function locate(): Located | null {
  const python = process.env.PYTHON ?? 'python3'

  if (process.env.REPORT_MAKER_BIN) {
    return { kind: 'binary', path: process.env.REPORT_MAKER_BIN }
  }

  const candidates = [
    process.env.REPORT_MAKER_ROOT && resolve(process.env.REPORT_MAKER_ROOT),
    // Packaged: the engine ships in resources/engine-src next to the app bundle.
    app.isPackaged ? join(process.resourcesPath, 'engine-src') : null,
    // Dev: app/ sits inside the engine repo.
    resolve(app.getAppPath(), '..')
  ].filter((path): path is string => Boolean(path))

  for (const root of candidates) {
    const script = join(root, 'bin', 'report-maker')
    if (existsSync(script)) return { kind: 'script', python, script }
  }

  const installed = onPath('report-maker')
  return installed ? { kind: 'binary', path: installed } : null
}

function argv(located: Located, args: string[]): { cmd: string; argv: string[] } {
  return located.kind === 'script'
    ? { cmd: located.python, argv: [located.script, ...args] }
    : { cmd: located.path, argv: args }
}

export function describe(): string {
  const located = locate()
  if (!located) return 'not found'
  return located.kind === 'script' ? located.script : located.path
}

export function run(vault: string, args: string[]): Promise<Run> {
  const located = locate()
  if (!located) {
    return Promise.resolve({
      code: 127,
      stdout: '',
      stderr:
        'report-maker was not found. Set REPORT_MAKER_ROOT to the engine checkout, ' +
        'or put report-maker on PATH.',
      command: 'report-maker'
    })
  }

  const { cmd, argv: full } = argv(located, ['-C', vault, ...args])
  return new Promise((done) => {
    const child = spawn(cmd, full, { cwd: vault, env: process.env })
    let stdout = ''
    let stderr = ''
    child.stdout.on('data', (chunk) => (stdout += chunk.toString()))
    child.stderr.on('data', (chunk) => (stderr += chunk.toString()))
    child.on('error', (err) =>
      done({
        code: 127,
        stdout,
        stderr: `${err.message}\n${stderr}`,
        command: `${cmd} ${full.join(' ')}`
      })
    )
    child.on('close', (code) =>
      done({ code: code ?? 0, stdout, stderr, command: `${cmd} ${full.join(' ')}` })
    )
  })
}

/** A run whose stdout is JSON — `list --json`, `templates --json`. */
export async function json<T>(vault: string, args: string[]): Promise<T> {
  const result = await run(vault, args)
  if (result.code !== 0) throw new Error(result.stderr || result.stdout || `exit ${result.code}`)
  return JSON.parse(result.stdout) as T
}

export async function manifest(vault: string): Promise<unknown | null> {
  const path = join(vault, 'out', 'manifest.json')
  if (!existsSync(path)) return null
  return JSON.parse(await readFile(path, 'utf8'))
}

/** A vault is any folder holding report-maker.toml — nothing more. */
export function isVault(path: string): boolean {
  return existsSync(join(path, 'report-maker.toml'))
}
