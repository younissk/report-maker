/**
 * Page images, as blob URLs.
 *
 * `report-maker pages` renders every page of a report to
 * `out/pages/<id>/page-N.png` and writes a `pages.json` index beside them. This
 * module turns that into something an `<img>` can take: the renderer has no
 * filesystem, so the bytes come over IPC and become object URLs.
 *
 * Two things make it more than a `useEffect` around `createObjectURL`:
 *
 * — **It refcounts.** A vault of two hundred reports mounts two hundred cards,
 *   and an object URL is a live handle on a decoded PNG that is freed only when
 *   somebody revokes it. An entry is shared while anyone holds it and revoked by
 *   the last holder to leave, so filtering the grid gives the memory back
 *   instead of piling it up.
 * — **It queues.** Those two hundred cards mount in one commit. Two hundred
 *   simultaneous IPC reads would land on the same main process that has to keep
 *   answering the CLI; a handful at a time looks identical and stays responsive.
 *
 * Freshness is the caller's to declare — pass the `revision` the shell bumps
 * after a build. The renderer has no `stat()` and the engine does not print page
 * mtimes, so a revision counter is the honest key: inventing a freshness rule
 * here would be the app holding an opinion about a vault, which is the one thing
 * it must never do.
 */

import { useEffect, useState } from 'react'

/** `out/pages/<id>/pages.json`, written by `engine/pages.py`. */
type PagesIndex = {
  id: string
  slug: string
  ppi: number
  count: number
  /** File names in page order — Typst does not zero-pad, so the order is the
   *  engine's, not the directory's. */
  pages: string[]
}

/** How much of a report to load. The cover is the common case by a long way. */
type Scope = 'first' | 'all'

type Entry = {
  /** Resolves to the blob URLs in page order; empty when there is no build. */
  urls: Promise<string[]>
  /** Mounted hooks holding this entry. The last one out revokes. */
  holders: number
}

const cache = new Map<string, Entry>()

function keyFor(vault: string, id: string, revision: number, scope: Scope): string {
  // JSON rather than a joined string: a vault path may contain anything a
  // filesystem allows, and two different tuples must never produce one key.
  return JSON.stringify([scope, revision, vault, id])
}

// ── the read queue ───────────────────────────────────────────────────────────

const LIMIT = 6
let running = 0
const waiting: (() => void)[] = []

async function queued<T>(work: () => Promise<T>): Promise<T> {
  if (running >= LIMIT) await new Promise<void>((go) => waiting.push(go))
  running += 1
  try {
    return await work()
  } finally {
    running -= 1
    waiting.shift()?.()
  }
}

// ── loading ──────────────────────────────────────────────────────────────────

async function load(vault: string, id: string, scope: Scope): Promise<string[]> {
  const dir = `${vault}/out/pages/${id}`
  const index = `${dir}/pages.json`
  if (!(await queued(() => window.api.files.exists(vault, index)))) return []

  let names: string[]
  try {
    const parsed = JSON.parse(await queued(() => window.api.files.read(vault, index))) as PagesIndex
    names = Array.isArray(parsed.pages) ? parsed.pages : []
  } catch {
    // A half-written index during a build is not an error worth surfacing: the
    // card falls back to its typographic cover until the next revision.
    return []
  }

  const wanted = scope === 'first' ? names.slice(0, 1) : names
  const urls: string[] = []
  for (const name of wanted) {
    try {
      const bytes = await queued(() => window.api.files.bytes(vault, `${dir}/${name}`))
      // .slice() copies into a plain ArrayBuffer, which is what Blob accepts —
      // the same dance the PDF viewer does.
      const buffer = bytes.slice().buffer as ArrayBuffer
      urls.push(URL.createObjectURL(new Blob([buffer], { type: 'image/png' })))
    } catch {
      break
    }
  }
  return urls
}

function acquire(key: string, work: () => Promise<string[]>): Entry {
  const found = cache.get(key)
  if (found) {
    found.holders += 1
    return found
  }
  const entry: Entry = { holders: 1, urls: work() }
  cache.set(key, entry)
  return entry
}

function release(key: string): void {
  const entry = cache.get(key)
  if (!entry) return
  entry.holders -= 1
  if (entry.holders > 0) return
  cache.delete(key)
  // The read may still be in flight; revoke whatever it produces, whenever it
  // lands. Dropping the entry first means a remount reads afresh rather than
  // handing out URLs that are about to be revoked.
  void entry.urls.then((urls) => urls.forEach(URL.revokeObjectURL)).catch(() => undefined)
}

// ── hooks ────────────────────────────────────────────────────────────────────

export type Pages = {
  /** Blob URLs in page order. Empty until loaded, and empty when unbuilt. */
  pages: string[]
  loading: boolean
}

function useBlobs(
  vault: string | null,
  reportId: string | null,
  revision: number,
  scope: Scope
): Pages {
  const wanted = Boolean(vault && reportId)
  const [state, setState] = useState<Pages>({ pages: [], loading: wanted })

  useEffect(() => {
    if (!vault || !reportId) {
      setState({ pages: [], loading: false })
      return
    }
    const key = keyFor(vault, reportId, revision, scope)
    const entry = acquire(key, () => load(vault, reportId, scope))
    let live = true
    setState({ pages: [], loading: true })
    void entry.urls
      .then((urls) => live && setState({ pages: urls, loading: false }))
      .catch(() => live && setState({ pages: [], loading: false }))
    return () => {
      live = false
      release(key)
    }
  }, [vault, reportId, revision, scope])

  return state
}

export type Thumb = {
  /** The first page, or null while loading and when the report is unbuilt. */
  url: string | null
  loading: boolean
}

/**
 * The cover image of one report. Pass `null` for `reportId` to skip the read
 * entirely — an unbuilt vault should cost nothing.
 */
export function useThumb(
  vault: string | null,
  reportId: string | null,
  revision = 0
): Thumb {
  const { pages, loading } = useBlobs(vault, reportId, revision, 'first')
  return { url: pages[0] ?? null, loading }
}

/**
 * Every page of one report, in order — what the timeline shows side by side.
 * Cached separately from `useThumb`: a report showing both pays for page one
 * twice, which is cheaper than teaching the cache to share slices of an entry
 * that may still be loading.
 */
export function usePages(
  vault: string | null,
  reportId: string | null,
  revision = 0
): Pages {
  return useBlobs(vault, reportId, revision, 'all')
}
