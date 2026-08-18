/**
 * Preferences, kept in a JSON file next to the vault list.
 *
 * They are global, not per-vault: they describe how you like to work, and a
 * vault has to stay a plain folder that any copy of the app — or a terminal, or
 * a colleague — can open without inheriting somebody's editor font.
 *
 * A patch is merged *over the defaults*, one level at a time. That is the whole
 * reason this file is not two lines: a settings file written by an older build
 * is missing keys a newer one reads, and reading `undefined` for
 * `editor.fontSize` would be a worse bug than any it could cause.
 */

import { mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { app } from 'electron'
import type { DeepPartial, Settings } from '../shared/types'

export const DEFAULTS: Settings = {
  appearance: { theme: 'system', accent: '#2E5A88', density: 'comfortable' },
  editor: {
    // Empty means "whatever the stylesheet already uses" — the editor falls back
    // to the app's mono stack rather than to a font this machine may not have.
    fontFamily: '',
    fontSize: 13,
    lineHeight: 1.6,
    lineNumbers: true,
    wordWrap: true,
    tabSize: 2,
    highlightActiveLine: true,
    bracketMatching: true,
    evidenceRail: true,
    lintGutter: true,
    // Follow the chrome. A default that names its own polarity is wrong on half
    // the machines that install the app, and it is wrong in the way nobody
    // reports: a light desktop with a dark editor looks like a design decision.
    syntaxTheme: 'auto'
  },
  build: { autoSaveMs: null, buildOnSave: false, watch: false, checkOnIdleMs: 600 },
  git: {
    // Both default off. Committing on your behalf is surprising; pushing is
    // outward-facing and irreversible from the app's point of view, so it is
    // something you turn on deliberately, once, having read what it does.
    autoCommit: false,
    autoPush: false,
    debounceMs: 4000,
    messageTemplate: 'report-maker: {n} file(s) — {date}'
  },
  vaults: { lastTarget: {} },
  // On by default: the app is a place you come back to, and an editor that
  // makes you re-pick the folder you were in yesterday is a chore, not a
  // choice. A vault named on the command line still wins over this.
  startup: { reopenLast: true, lastVault: null },
  // `panes` is an open map keyed by panel id, so a layout written by a build
  // with more panes than this one survives being read by this one.
  layout: { panes: {}, sidebar: true, viewer: true, problems: false }
}

function file(): string {
  return join(app.getPath('userData'), 'settings.json')
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * `patch` over `base`, recursing into objects and replacing everything else.
 * Keys the patch has and the defaults do not are kept — `vaults.lastTarget` is
 * an open map, and a key from a newer build should survive a downgrade.
 */
function merge<T>(base: T, patch: unknown): T {
  if (patch === undefined) return base
  if (!isRecord(base) || !isRecord(patch)) return patch as T

  const merged: Record<string, unknown> = { ...base }
  for (const [key, value] of Object.entries(patch)) {
    merged[key] = merge(merged[key], value)
  }
  return merged as T
}

export async function read(): Promise<Settings> {
  try {
    return merge(DEFAULTS, JSON.parse(await readFile(file(), 'utf8')))
  } catch {
    // No file yet, or an unreadable one. Either way the defaults are the answer;
    // refusing to start over a corrupt preferences file would be absurd.
    return DEFAULTS
  }
}

async function write(settings: Settings): Promise<Settings> {
  await mkdir(dirname(file()), { recursive: true })
  await writeFile(file(), JSON.stringify(settings, null, 2) + '\n', 'utf8')
  return settings
}

export async function update(patch: DeepPartial<Settings>): Promise<Settings> {
  return write(merge(await read(), patch))
}

export async function reset(): Promise<Settings> {
  // Delete rather than write the defaults back, so a later build's new defaults
  // reach a user who once pressed Reset.
  await rm(file(), { force: true })
  return DEFAULTS
}
