/**
 * The vault list, kept in a JSON file in the app's user data.
 *
 * No database here either — the engine's whole premise is that the filesystem is
 * the source of truth, and a desktop shell that introduced its own store would
 * be the first thing to go stale. All this file remembers is which folders the
 * user has opened, when each was last current, and which ones they pinned.
 *
 * Nothing about a vault is stored: not its reports, not its designs, not its
 * name beyond the folder's own. Everything else is a `report-maker` subprocess
 * away, and a cached copy of it here would be a second answer to a question the
 * engine already answers.
 *
 * A vault that has gone missing is **kept**, flagged, and offered a remove
 * action. Dropping it silently — which is what this file used to do — is wrong
 * in the common case: a vault on an unmounted drive or a sleeping network share
 * is not a vault to forget, and a list that quietly shrinks makes the app look
 * like it lost your work.
 */

import { existsSync } from 'node:fs'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { basename, dirname, join, resolve } from 'node:path'
import { app } from 'electron'
import type { VaultEntry, VaultList } from '../shared/types'
import * as settings from './settings'

/** What is actually on disk. The observed fields — name, missing — are not
 *  stored, because they are facts about the folder and belong to the folder. */
type Stored = {
  vaults: Array<{ path: string; pinned?: boolean; openedAt?: string | null } | string>
  current?: string | null
}

function file(): string {
  return join(app.getPath('userData'), 'vaults.json')
}

/** A vault is any folder holding report-maker.toml — nothing more, and the app
 *  never decides this for itself anywhere else. */
function present(path: string): boolean {
  return existsSync(join(path, 'report-maker.toml'))
}

/**
 * Pinned first, then most recently opened. ISO timestamps sort lexicographically
 * in chronological order, which is the reason they are stored as strings.
 */
function order(entries: VaultEntry[]): VaultEntry[] {
  return [...entries].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1
    return (b.openedAt ?? '').localeCompare(a.openedAt ?? '')
  })
}

function shape(entries: VaultEntry[], current: string | null): VaultList {
  const ordered = order(entries)
  return { vaults: ordered.map((entry) => entry.path), entries: ordered, current }
}

/**
 * Read the file, tolerating both shapes it has ever had. The older one was a
 * bare array of paths; migrating it in place would mean writing on read, so it
 * is widened here instead and written back the next time anything changes.
 */
async function load(): Promise<{ entries: VaultEntry[]; current: string | null }> {
  let raw: Stored
  try {
    raw = JSON.parse(await readFile(file(), 'utf8')) as Stored
  } catch {
    return { entries: [], current: null }
  }

  const entries = (raw.vaults ?? []).map((item) => {
    const stored = typeof item === 'string' ? { path: item } : item
    const path = resolve(stored.path)
    return {
      path,
      name: basename(path) || path,
      openedAt: stored.openedAt ?? null,
      pinned: Boolean(stored.pinned),
      missing: !present(path)
    }
  })

  // A current vault that has gone missing is not a vault the window can open,
  // so it is reported as no current vault at all rather than as one that fails
  // on every command.
  const current =
    raw.current && entries.some((e) => e.path === raw.current && !e.missing)
      ? resolve(raw.current)
      : null
  return { entries, current }
}

async function persist(entries: VaultEntry[], current: string | null): Promise<VaultList> {
  const stored: Stored = {
    vaults: order(entries).map((e) => ({
      path: e.path,
      pinned: e.pinned,
      openedAt: e.openedAt
    })),
    current
  }
  await mkdir(dirname(file()), { recursive: true })
  await writeFile(file(), JSON.stringify(stored, null, 2) + '\n', 'utf8')

  // Which vault to reopen is a startup decision, so it is recorded where the
  // startup policy lives. Writing it from the one funnel every change passes
  // through — null included — is what keeps the two files from disagreeing, and
  // is why forgetting the open vault does not reopen it tomorrow.
  await settings.update({ startup: { lastVault: current } })
  return shape(entries, current)
}

export async function read(): Promise<VaultList> {
  const { entries, current } = await load()
  return shape(entries, current)
}

/**
 * The list as the window should first see it.
 *
 * `requested` is a vault named on the command line or in `RM_OPEN_VAULT`, and it
 * wins over everything: naming a vault at launch is what makes the app
 * scriptable, and it is the path the smoke test drives. Otherwise the startup
 * preference decides, and a `lastVault` that no longer resolves means Welcome —
 * opening straight into a folder that is not there would be worse than asking.
 */
export async function opening(requested: string | null): Promise<VaultList> {
  const { entries } = await load()
  if (requested) return shape(entries, resolve(requested))

  const { startup } = await settings.read()
  const last = startup.lastVault
  const reopen = startup.reopenLast && last !== null && present(last)
  return shape(entries, reopen ? resolve(last) : null)
}

/**
 * Remember a vault and make it current. Opening one that is already on the list
 * touches its timestamp rather than duplicating it — the list is a set of
 * folders ordered by recency, not a history of openings.
 */
export async function add(path: string): Promise<VaultList> {
  const target = resolve(path)
  const { entries } = await load()
  const now = new Date().toISOString()
  const known = entries.some((e) => e.path === target)

  const next = known
    ? entries.map((e) =>
        e.path === target ? { ...e, openedAt: now, missing: !present(target) } : e
      )
    : [
        {
          path: target,
          name: basename(target) || target,
          openedAt: now,
          pinned: false,
          missing: !present(target)
        },
        ...entries
      ]
  return persist(next, target)
}

/** Selecting is opening: both make a vault current and both count as having
 *  used it, so there is one implementation and no way for them to diverge. */
export async function select(path: string): Promise<VaultList> {
  return add(path)
}

/**
 * Pin or unpin. Pinning is the answer to a list that reorders itself under you:
 * three vaults you work in daily should not sink below eleven you opened once.
 */
export async function pin(path: string, pinned: boolean): Promise<VaultList> {
  const target = resolve(path)
  const { entries, current } = await load()
  return persist(
    entries.map((e) => (e.path === target ? { ...e, pinned } : e)),
    current
  )
}

/**
 * Drop a vault from the list. Nothing on disk is touched — this forgets that the
 * folder was opened, and that is all it has ever meant.
 *
 * Forgetting the current vault leaves no current vault, deliberately. Jumping
 * the user into a different vault because it happened to be next in the list is
 * a surprise; the Welcome screen is not.
 */
export async function forget(path: string): Promise<VaultList> {
  const target = resolve(path)
  const { entries, current } = await load()
  return persist(
    entries.filter((e) => e.path !== target),
    current === target ? null : current
  )
}
