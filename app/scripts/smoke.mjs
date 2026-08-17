/**
 * Launch the built app, capture the window, exit.
 *
 * A desktop shell has no equivalent of curling a page, so this is how a change
 * here gets verified without a human looking at it: the main process honours
 * RM_SCREENSHOT by capturing the window once the renderer has loaded and then
 * quitting. A non-zero exit or a missing PNG means the app did not come up.
 *
 *   node scripts/smoke.mjs                      # the demo vault
 *   node scripts/smoke.mjs ~/Documents/Reports  # another vault
 *   node scripts/smoke.mjs none out/welcome.png # no vault: the first-run screen
 *
 * Each run gets a throwaway user-data directory, so it neither inherits nor
 * clobbers the vault list of a real install.
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, rmSync, statSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import electron from 'electron'

const app = resolve(import.meta.dirname, '..')
const engine = resolve(app, '..')

const requested = process.argv[2] ?? resolve(engine, 'examples/demo-vault')
const vault = requested === 'none' ? null : resolve(requested)
const shot = resolve(process.argv[3] ?? resolve(app, 'out/smoke.png'))

if (vault && !existsSync(resolve(vault, 'report-maker.toml'))) {
  console.error(`not a vault: ${vault}`)
  process.exit(1)
}
mkdirSync(dirname(shot), { recursive: true })

const profile = resolve(app, 'out/smoke-profile')
rmSync(profile, { recursive: true, force: true })

const child = spawn(electron, [app, '--user-data-dir', profile], {
  stdio: 'inherit',
  env: {
    ...process.env,
    REPORT_MAKER_ROOT: engine,
    RM_SCREENSHOT: shot,
    // Chromium's PDF plugin paints late; capture after it has.
    RM_SCREENSHOT_DELAY: process.env.RM_SCREENSHOT_DELAY ?? '6000',
    ...(vault ? { RM_SMOKE_VAULT: vault } : {})
  }
})

child.on('close', (code) => {
  if (code !== 0) {
    console.error(`electron exited ${code}`)
    process.exit(code ?? 1)
  }
  if (!existsSync(shot)) {
    console.error('no screenshot written — the window never finished loading')
    process.exit(1)
  }
  console.log(`  → ${shot} (${statSync(shot).size} bytes, vault: ${vault ?? 'none'})`)
})
