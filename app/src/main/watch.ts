/**
 * A live `report-maker watch` run, streamed to the renderer.
 *
 * `watch` is the one engine command that does not end: it rebuilds a report
 * whenever its folder changes and prints as it goes. That makes it the one
 * command the app cannot run through `engine.run`, which buffers until exit.
 *
 * Exactly one watcher exists at a time. Two of them on one vault would race for
 * the same `out/` directory, and a watcher the user cannot see is a watcher they
 * cannot stop — so starting a new one always kills the old, and so do closing
 * the window and quitting the app. A stray Python process outliving the app is
 * the failure mode this file exists to prevent.
 */

import { type ChildProcess, spawn } from 'node:child_process'
import type { BrowserWindow } from 'electron'
import type { WatchEvent } from '../shared/types'
import * as engine from './engine'

type Running = { child: ChildProcess; command: string }

let running: Running | null = null

function send(win: BrowserWindow, event: WatchEvent): void {
  if (win.isDestroyed()) return
  win.webContents.send('watch:event', event)
}

/**
 * Kill the whole process group, not just the child. The child is `python3
 * bin/report-maker`, which spawns `typst` and `mmdc` of its own; SIGTERM to the
 * parent alone would leave those compiling into a vault nobody is watching.
 */
function kill(child: ChildProcess): void {
  if (child.exitCode !== null || child.signalCode !== null) return
  try {
    if (process.platform !== 'win32' && child.pid) process.kill(-child.pid, 'SIGTERM')
    else child.kill('SIGTERM')
  } catch {
    // Already gone, or the group is not ours — the plain kill is the fallback.
    child.kill('SIGTERM')
  }
}

export function stop(): void {
  if (!running) return
  const { child } = running
  running = null
  kill(child)
}

export function isRunning(): boolean {
  return running !== null
}

export function start(win: BrowserWindow, vault: string, target: string): void {
  stop()

  if (!engine.isVault(vault)) throw new Error(`not a vault: ${vault}`)

  const located = engine.locate()
  if (!located) {
    send(win, { kind: 'exit', text: 'report-maker was not found on this machine.', code: 127 })
    return
  }

  // The same mapping engine.run() makes, repeated here because a long-lived
  // child needs the raw handles that a buffered run does not expose.
  const cmd = located.kind === 'script' ? located.python : located.path
  const args =
    located.kind === 'script'
      ? [located.script, '-C', vault, 'watch', target]
      : ['-C', vault, 'watch', target]
  const command = `${cmd} ${args.join(' ')}`

  const child = spawn(cmd, args, {
    cwd: vault,
    env: process.env,
    // Its own process group, so kill() above can take the children with it.
    detached: process.platform !== 'win32'
  })
  const current: Running = { child, command }
  running = current

  send(win, { kind: 'start', text: command })
  child.stdout.on('data', (chunk: Buffer) => send(win, { kind: 'stdout', text: chunk.toString() }))
  child.stderr.on('data', (chunk: Buffer) => send(win, { kind: 'stderr', text: chunk.toString() }))

  child.on('error', (err) => {
    if (running === current) running = null
    send(win, { kind: 'exit', text: err.message, code: 127 })
  })
  child.on('close', (code) => {
    // A watcher that was already replaced must not report its exit as the
    // current one's: the renderer would show the new run as finished.
    if (running !== current) return
    running = null
    send(win, { kind: 'exit', code: code ?? 0 })
  })
}
