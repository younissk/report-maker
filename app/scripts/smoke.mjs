/**
 * Launch the built app, drive it through its surfaces, capture each, exit.
 *
 * A desktop shell has no equivalent of curling a page, so this is how a change
 * here gets verified without a human looking at it. The app is launched with
 * Chromium's remote debugging port open and driven over the DevTools protocol:
 * the same keystrokes a person would press, then a screenshot of what appeared.
 *
 *   node scripts/smoke.mjs                      # the demo vault
 *   node scripts/smoke.mjs ~/Documents/Reports  # another vault
 *   node scripts/smoke.mjs none out/welcome.png # no vault: the first-run screen
 *
 * Two rules make this worth running:
 *
 *   1. **It can fail.** A renderer console error or an uncaught exception fails
 *      the run, as does a surface that never appeared. A smoke test that only
 *      ever writes a PNG proves nothing beyond "electron starts".
 *   2. **It drives the real UI.** Surfaces are reached through the shortcuts,
 *      the command palette and the buttons a user has, not through a back door,
 *      so a shortcut that stops firing shows up here rather than in a bug
 *      report. That includes opening a report at all: the app's empty state is
 *      the dashboard, so the first thing this does is ⌘K a file open.
 *
 * Each run gets a throwaway user-data directory, so it neither inherits nor
 * clobbers the vault list of a real install. That has to be one argv entry —
 * `--user-data-dir=<path>`, not two — because Chromium parses switches, not
 * arguments: passed as a pair the flag takes no value, the path becomes a
 * positional, and the run silently uses the developer's real profile. This
 * script did exactly that until it was noticed, which is why
 * `node scripts/smoke.mjs none out/welcome.png` used to open the last vault and
 * still report "smoke ok".
 *
 * Escape hatches, for when the app is mid-rebuild and you only want the launch
 * checked: RM_SMOKE_SURFACES=0 skips the driving, RM_SMOKE_IGNORE=<regex> drops
 * matching console errors.
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:net'
import { basename, dirname, extname, join, resolve } from 'node:path'
import { setTimeout as sleep } from 'node:timers/promises'
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

// ── the surfaces, and how a person reaches each one ──────────────────────────

// Chromium's modifier bitmask: Alt 1, Ctrl 2, Meta 4, Shift 8.
const META = 4
const SHIFT = 8

/**
 * One screen per row: the keys that open it, and the question "is it actually
 * on screen?" asked of the live DOM.
 *
 * The probes deliberately look for something only that surface renders — a
 * placeholder, a heading — rather than a test id, because a test id is a thing
 * the app can keep while the screen behind it rots. `data-surface` is honoured
 * first when the shell provides it.
 */
const SURFACES = [
  // The three sidebar tabs come first, because the sidebar only exists on the
  // editor route: reach a route screen and every tab shortcut becomes a no-op.
  {
    name: 'notes',
    // ⌘⇧T — `view.notes`. The pad: todos.md, notes.md and `// TODO:` in source.
    keys: [{ key: 'T', code: 'KeyT', keyCode: 84, modifiers: META | SHIFT }],
    probe: `[...document.querySelectorAll('span')].some((el) => el.textContent.trim() === 'Todos')`
  },
  {
    name: 'sources',
    // ⌘⇧E — `view.sources`.
    keys: [{ key: 'E', code: 'KeyE', keyCode: 69, modifiers: META | SHIFT }],
    probe: `[...document.querySelectorAll('span')].some((el) => el.textContent.trim() === 'Sources')`
  },
  {
    name: 'find',
    // ⌘⇧F — `view.search`.
    keys: [{ key: 'F', code: 'KeyF', keyCode: 70, modifiers: META | SHIFT }],
    probe: `!!document.querySelector('input[placeholder="Find in this vault"]')`
  },
  {
    name: 'csv',
    // A .csv opens as a grid rather than as text. Reached the way any file is:
    // ⌘K, type, Enter.
    open: 'rule-coverage.csv',
    probe: `!!document.querySelector('[data-editor="csv"]')`
  },
  {
    name: 'mermaid',
    // A .mmd opens beside a preview of the engine's own prepared source.
    open: 'example-flow.mmd',
    probe: `!!document.querySelector('[data-editor="mermaid"]')`
  },
  {
    // Back to the report, so the surfaces below start where they used to.
    name: 'editor',
    open: 'main.typ',
    probe: `!!document.querySelector('[data-editor="text"]')`
  },
  {
    name: 'dashboard',
    // ⌘⇧D — `view.dashboard` in lib/commands.ts.
    keys: [{ key: 'D', code: 'KeyD', keyCode: 68, modifiers: META | SHIFT }],
    probe: `!!document.querySelector('[data-surface="dashboard"], input[placeholder^="Filter by title"]')`
  },
  {
    name: 'designs',
    // Typed, and this row is a regression test as much as a screenshot.
    //
    // The palette ranks globally and draws grouped, and cmdk selects the first
    // row in the DOM — so while the group order was fixed, "designs" highlighted
    // View ▸ Designs and ran Build ▸ Stage the designs, because Build is printed
    // first. `selected` is what makes that visible here: the row cmdk has
    // highlighted must be the row this is asking for, checked before Enter is
    // pressed rather than inferred from where we ended up.
    palette: 'designs',
    // Not `cmd:build.stage`, which is what this used to run.
    selected: 'cmd:view.designs',
    probe: `!!document.querySelector('[data-surface="designs"]')`
  },
  {
    name: 'brand',
    // No shortcut of its own: the palette is how it is reached.
    palette: 'brand studio',
    probe: `!!document.querySelector('[data-surface="brand"]') ||
            [...document.querySelectorAll('span,h1,h2')].some((el) => el.textContent.trim() === 'Brand')`
  },
  {
    name: 'settings',
    // ⌘, — `view.settings`.
    keys: [{ key: ',', code: 'Comma', keyCode: 188, modifiers: META }],
    probe: `[...document.querySelectorAll('[data-surface="settings"], [role="dialog"]')]
              .some((el) => /Appearance/.test(el.textContent) && /Editor/.test(el.textContent))`,
    // A dialog left open would sit over whatever is captured next.
    after: [{ key: 'Escape', code: 'Escape', keyCode: 27, modifiers: 0 }]
  }
]

// ── a minimal DevTools protocol client ───────────────────────────────────────

/**
 * Just enough CDP to press keys, ask the page a question and take a picture.
 *
 * Node ships a WebSocket client, and the protocol is request/response JSON over
 * it, so a dependency here would buy nothing but a lockfile entry.
 */
class Debugger {
  #socket
  #next = 1
  #pending = new Map()
  #events = []

  constructor(socket) {
    this.#socket = socket
    socket.addEventListener('message', (message) => {
      const frame = JSON.parse(message.data)
      if (frame.id !== undefined) {
        const waiting = this.#pending.get(frame.id)
        this.#pending.delete(frame.id)
        if (!waiting) return
        if (frame.error) waiting.reject(new Error(`${frame.error.message} (${frame.method})`))
        else waiting.resolve(frame.result)
        return
      }
      for (const listener of this.#events) listener(frame)
    })
  }

  static async attach(url) {
    const socket = new WebSocket(url)
    await new Promise((done, fail) => {
      socket.addEventListener('open', done, { once: true })
      socket.addEventListener('error', () => fail(new Error(`cannot attach to ${url}`)), {
        once: true
      })
    })
    return new Debugger(socket)
  }

  on(listener) {
    this.#events.push(listener)
  }

  send(method, params = {}) {
    const id = this.#next++
    this.#socket.send(JSON.stringify({ id, method, params }))
    return new Promise((resolve_, reject) => {
      this.#pending.set(id, { resolve: resolve_, reject })
      // A hung request means the renderer stopped answering, which is itself a
      // failure worth reporting rather than waiting out.
      setTimeout(() => {
        if (!this.#pending.has(id)) return
        this.#pending.delete(id)
        reject(new Error(`${method} timed out`))
      }, 20_000)
    })
  }

  close() {
    try {
      this.#socket.close()
    } catch {
      /* the app is already gone */
    }
  }
}

async function freePort() {
  return new Promise((done, fail) => {
    const server = createServer()
    server.on('error', fail)
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address()
      server.close(() => done(port))
    })
  })
}

/** The renderer's debugging target, once Chromium has published one. */
async function pageTarget(port, deadline) {
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/list`)
      const targets = await response.json()
      const page = targets.find((t) => t.type === 'page' && t.webSocketDebuggerUrl)
      if (page) return page
    } catch {
      /* not listening yet */
    }
    await sleep(150)
  }
  throw new Error('the app never opened a debugging target — it did not start')
}

// ── driving ──────────────────────────────────────────────────────────────────

async function press(cdp, stroke) {
  const shared = {
    key: stroke.key,
    code: stroke.code,
    windowsVirtualKeyCode: stroke.keyCode,
    nativeVirtualKeyCode: stroke.keyCode,
    modifiers: stroke.modifiers
  }
  await cdp.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', ...shared })
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', ...shared })
}

/**
 * Click the middle of whatever `selector` finds, in page coordinates.
 *
 * A real mouse event on the real control, for the surfaces that have no
 * shortcut. Asking the DOM for the box and clicking it is still driving the UI;
 * calling the handler directly would not be.
 */
async function click(cdp, selector) {
  const { result } = await cdp.send('Runtime.evaluate', {
    expression: `(() => {
      const el = document.querySelector(${JSON.stringify(selector)})
      if (!el) return null
      const box = el.getBoundingClientRect()
      return { x: box.left + box.width / 2, y: box.top + box.height / 2 }
    })()`,
    returnByValue: true
  })
  const at = result?.value
  if (!at) return false
  for (const type of ['mousePressed', 'mouseReleased']) {
    await cdp.send('Input.dispatchMouseEvent', {
      type,
      x: at.x,
      y: at.y,
      button: 'left',
      clickCount: 1
    })
  }
  return true
}

async function ask(cdp, expression) {
  const result = await cdp.send('Runtime.evaluate', {
    expression: `(() => { try { return ${expression} } catch { return false } })()`,
    returnByValue: true
  })
  return result.result?.value === true
}

async function capture(cdp, path) {
  const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' })
  writeFileSync(path, Buffer.from(data, 'base64'))
  return statSync(path).size
}

/** `out/smoke.png` → `out/smoke-dashboard.png`, so one run leaves one set. */
function variant(name) {
  const ext = extname(shot) || '.png'
  return join(dirname(shot), `${basename(shot, ext)}-${name}${ext}`)
}

/** Wait for the page to answer `expression` with true, or give up. */
async function until(cdp, expression, budget = 8000) {
  for (let waited = 0; waited < budget; waited += 250) {
    await sleep(250)
    if (await ask(cdp, expression)) return true
  }
  return false
}

/**
 * Open a report, the way somebody would: ⌘K, type a file name, Enter.
 *
 * It has to happen before anything is captured. The app's empty state is the
 * dashboard — a vault with nothing open shows what it holds rather than an empty
 * editor — and with a genuinely throwaway profile there is no remembered report
 * to resume, so nothing is open at launch. Without this step the editor
 * screenshot is a picture of the dashboard, and the three sidebar tabs below
 * have no sidebar to appear in.
 */
async function openAReport(cdp) {
  await press(cdp, { key: 'k', code: 'KeyK', keyCode: 75, modifiers: META })
  await sleep(400)
  await cdp.send('Input.insertText', { text: 'main.typ' })
  await sleep(500)
  await press(cdp, { key: 'Enter', code: 'Enter', keyCode: 13, modifiers: 0 })
  return until(cdp, `!!document.querySelector('[data-surface="editor"]')`)
}

/**
 * The row cmdk has highlighted, by identity, or null when nothing is.
 *
 * `data-value` is the row's own `value` — `cmd:<command id>` for a command,
 * `file:<vault-relative path>` for a file — which is what `Palette.tsx` keys
 * selection on and therefore exactly what Enter will run. Matching on the
 * visible label instead would compare against a title *and* its hint, and would
 * start failing the day somebody rewords either.
 */
async function selectedRow(cdp) {
  const { result } = await cdp.send('Runtime.evaluate', {
    expression: `(() => {
      const el = document.querySelector('[cmdk-item][aria-selected="true"]')
      return el ? el.getAttribute('data-value') : null
    })()`,
    returnByValue: true
  })
  return result?.value ?? null
}

async function reach(cdp, surface, failures) {
  if (surface.palette) {
    // ⌘⇧P opens the palette straight to commands; typing then Enter picks the
    // top match, which is what a person does.
    await press(cdp, { key: 'P', code: 'KeyP', keyCode: 80, modifiers: META | SHIFT })
    await sleep(250)
    await cdp.send('Input.insertText', { text: surface.palette })
    await sleep(350)
    // What Enter is about to run, read off the DOM before it is pressed.
    //
    // cmdk selects the first `[cmdk-item]` in document order and Enter fires
    // that one, so this is the only check that can tell "the right screen
    // appeared" apart from "a different command happened to lead there". The
    // whole of the palette's group ordering exists to keep these two the same
    // row; asserting it after the fact would not notice if they came apart.
    if (surface.selected) {
      const highlighted = await selectedRow(cdp)
      if (highlighted !== surface.selected) {
        failures?.push(
          `palette “${surface.palette}” highlights ${JSON.stringify(highlighted)}, ` +
            `and Enter runs whatever is highlighted — expected ${JSON.stringify(surface.selected)}`
        )
      } else {
        console.log(`  · palette “${surface.palette}” → ${JSON.stringify(highlighted)}`)
      }
    }
    await press(cdp, { key: 'Enter', code: 'Enter', keyCode: 13, modifiers: 0 })
  }
  if (surface.open) {
    // ⌘K searches files as well as commands; a file name and Enter opens it.
    await press(cdp, { key: 'k', code: 'KeyK', keyCode: 75, modifiers: META })
    await sleep(400)
    await cdp.send('Input.insertText', { text: surface.open })
    await sleep(500)
    await press(cdp, { key: 'Enter', code: 'Enter', keyCode: 13, modifiers: 0 })
  }
  if (surface.click && !(await click(cdp, surface.click))) return false
  for (const stroke of surface.keys ?? []) await press(cdp, stroke)

  // Panels load their own data over IPC; give the engine a moment to answer
  // before deciding the screen is not there.
  return until(cdp, surface.probe)
}

// ── the run ──────────────────────────────────────────────────────────────────

const port = await freePort()
const child = spawn(
  electron,
  [
    app,
    ...(vault ? [vault] : []),
    `--user-data-dir=${profile}`,
    `--remote-debugging-port=${port}`,
    // Node's WebSocket client sends no Origin header on some versions and a
    // null one on others; Chromium rejects both unless told otherwise.
    '--remote-allow-origins=*'
  ],
  {
    stdio: 'inherit',
    env: { ...process.env, REPORT_MAKER_ROOT: engine }
  }
)

let exited = null
child.on('close', (code) => (exited = code ?? 0))

const failures = []
const ignore = process.env.RM_SMOKE_IGNORE ? new RegExp(process.env.RM_SMOKE_IGNORE) : null
const seen = new Set()
let cdp = null

/** One complaint from the renderer, reported once however many domains saw it. */
function complain(kind, text) {
  if (!text || seen.has(text)) return
  seen.add(text)
  if (!ignore?.test(text)) failures.push(`${kind}: ${text}`)
}

try {
  const target = await pageTarget(port, Date.now() + 30_000)
  cdp = await Debugger.attach(target.webSocketDebuggerUrl)

  // Everything the renderer complains about, collected from the moment we can
  // hear it. `console.error` and an uncaught exception are the same failure as
  // far as this script is concerned.
  cdp.on((frame) => {
    if (frame.method === 'Runtime.consoleAPICalled' && frame.params.type === 'error') {
      complain('console.error', frame.params.args.map((a) => a.value ?? a.description ?? a.type).join(' '))
    }
    if (frame.method === 'Runtime.exceptionThrown') {
      const details = frame.params.exceptionDetails
      complain('uncaught', details.exception?.description ?? details.text)
    }
    // The Log domain replays what was recorded before we attached, which is the
    // only way to hear about a failure during the very first paint.
    if (frame.method === 'Log.entryAdded' && frame.params.entry.level === 'error') {
      complain(frame.params.entry.source, frame.params.entry.text)
    }
  })

  await cdp.send('Runtime.enable')
  await cdp.send('Log.enable')
  await cdp.send('Page.enable')

  // The other surfaces need a vault to have anything in them, and the first-run
  // screen has none of them by design.
  const drive = vault !== null && process.env.RM_SMOKE_SURFACES !== '0'

  // Nothing is driven until the window has painted. A keystroke sent to a
  // renderer that has not mounted its listeners yet is a keystroke that lands
  // nowhere, and the failure it produces reads exactly like a broken shortcut.
  await sleep(Number(process.env.RM_SCREENSHOT_DELAY ?? 6000))

  if (drive && !(await openAReport(cdp))) {
    failures.push('no report opened from the palette — the editor never appeared')
  }
  // Chromium's PDF plugin paints late; capture after it has.
  if (drive) await sleep(2500)

  console.log(`  → ${shot} (${await capture(cdp, shot)} bytes, vault: ${vault ?? 'none'})`)

  if (drive) {
    for (const surface of SURFACES) {
      const reached = await reach(cdp, surface, failures)
      // The probe fires as soon as the screen exists, which is before the panels
      // on it have heard back from their own subprocesses. Capturing a grid of
      // skeletons proves the screen mounted and nothing else.
      await sleep(1200)
      const path = variant(surface.name)
      const size = await capture(cdp, path)
      console.log(`  → ${path} (${size} bytes, ${surface.name}${reached ? '' : ' — NOT REACHED'})`)
      if (!reached) failures.push(`${surface.name} never appeared after its shortcut`)
      for (const stroke of surface.after ?? []) await press(cdp, stroke)
      await sleep(300)
    }
  } else if (vault) {
    console.log('  · surfaces skipped (RM_SMOKE_SURFACES=0)')
  }
} catch (err) {
  failures.push(String(err.message ?? err))
} finally {
  cdp?.close()
  if (exited === null) {
    child.kill()
    // Give it a beat to go quietly before insisting.
    await sleep(1500)
    if (exited === null) child.kill('SIGKILL')
  }
}

// A crash before we asked it to quit is a failure; the SIGTERM above is not.
if (exited !== null && exited !== 0) failures.push(`electron exited ${exited} on its own`)
if (!existsSync(shot)) failures.push('no screenshot written — the window never finished loading')
else if (statSync(shot).size < 1024) failures.push(`${shot} is empty — nothing rendered`)

if (failures.length > 0) {
  console.error('\nsmoke failed:')
  for (const failure of failures) console.error(`  ✗ ${failure}`)
  process.exit(1)
}
console.log('\nsmoke ok')
process.exit(0)
