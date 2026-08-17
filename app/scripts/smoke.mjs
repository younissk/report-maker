/**
 * Launch the built app against a vault, capture the window, exit.
 *
 * A desktop shell has no equivalent of curling a page, so this is how a change
 * here gets verified without a human looking at it: the main process honours
 * RM_SCREENSHOT by capturing the window once the renderer has loaded and then
 * quitting. A non-zero exit or a missing PNG means the app did not come up.
 *
 *   node scripts/smoke.mjs [vault] [output.png]
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, statSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import electron from 'electron'

const app = resolve(import.meta.dirname, '..')
const repo = resolve(app, '..')
const vault = resolve(process.argv[2] ?? repo)
const shot = resolve(process.argv[3] ?? resolve(app, 'out/smoke.png'))

if (!existsSync(resolve(vault, 'report-maker.toml'))) {
  console.error(`not a vault: ${vault}`)
  process.exit(1)
}
mkdirSync(dirname(shot), { recursive: true })

// The app remembers vaults in userData, so the smoke run gets its own profile —
// otherwise it would either inherit or clobber the real one.
const profile = resolve(app, 'out/smoke-profile')

const child = spawn(
  electron,
  [app, '--user-data-dir', profile],
  {
    stdio: 'inherit',
    env: {
      ...process.env,
      REPORT_MAKER_ROOT: repo,
      RM_SCREENSHOT: shot,
      RM_SCREENSHOT_DELAY: process.env.RM_SCREENSHOT_DELAY ?? '3500',
      RM_SMOKE_VAULT: vault
    }
  }
)

child.on('close', (code) => {
  if (code !== 0) {
    console.error(`electron exited ${code}`)
    process.exit(code ?? 1)
  }
  if (!existsSync(shot)) {
    console.error('no screenshot written — the window never finished loading')
    process.exit(1)
  }
  console.log(`  → ${shot} (${statSync(shot).size} bytes)`)
})
