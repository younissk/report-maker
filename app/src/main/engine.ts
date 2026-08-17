/**
 * The bridge to the engine.
 *
 * The app runs no logic of its own: every question about a vault — what reports
 * exist, which designs, whether a build is stale — is answered by shelling out
 * to `report-maker`, the same way a terminal or a CI job would. That is the
 * payoff of a headless engine, and it means the desktop shell can never drift
 * from what the CLI does.
 */

import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { app } from 'electron'

export type Run = { code: number; stdout: string; stderr: string; command: string }

/** Where the engine lives. The app sits in `app/` inside the engine repo. */
function engineRoot(): string {
  if (process.env.REPORT_MAKER_ROOT) return resolve(process.env.REPORT_MAKER_ROOT)
  // Dev: app/out/main → app → repo. Packaged: resources/app.asar/… → keep the env var.
  return resolve(app.getAppPath(), '..')
}

function binary(): { cmd: string; leading: string[] } {
  if (process.env.REPORT_MAKER_BIN) return { cmd: process.env.REPORT_MAKER_BIN, leading: [] }
  const script = join(engineRoot(), 'bin', 'report-maker')
  return { cmd: process.env.PYTHON ?? 'python3', leading: [script] }
}

export function run(vault: string, args: string[]): Promise<Run> {
  const { cmd, leading } = binary()
  const argv = [...leading, '-C', vault, ...args]
  return new Promise((done) => {
    const child = spawn(cmd, argv, { cwd: vault, env: process.env })
    let stdout = ''
    let stderr = ''
    child.stdout.on('data', (b) => (stdout += b.toString()))
    child.stderr.on('data', (b) => (stderr += b.toString()))
    child.on('error', (err) =>
      done({ code: 127, stdout, stderr: `${err.message}\n${stderr}`, command: `${cmd} ${argv.join(' ')}` })
    )
    child.on('close', (code) =>
      done({ code: code ?? 0, stdout, stderr, command: `${cmd} ${argv.join(' ')}` })
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

export function isVault(path: string): boolean {
  return existsSync(join(path, 'report-maker.toml'))
}
