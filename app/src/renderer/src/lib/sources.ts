/**
 * The bibliography, as the engine prints it.
 *
 * `report-maker sources <report> --json` is the only reader of `sources.yml` in
 * this app. The renderer never parses the file itself: hayagriva is the engine's
 * grammar, the use counts come from scanning `main.typ` against it, and a second
 * implementation here would be the one that drifts. Everything below is either a
 * call to that command or a way of holding on to its result.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { SourceRow } from '../../../shared/types'

// ── Where a report keeps its evidence ────────────────────────────────────────
//
// Paths are vault-relative POSIX, the same form `Finding.path` uses, so a jump
// from the sources panel and a jump from the problems panel take the same shape.
// The layout itself is the engine's (`engine/snapshot.py`); it is written down
// here once rather than inline at each call site, and it is the only fact about
// a vault this file states on its own authority.

export function sourcesPath(reportId: string): string {
  return `reports/${reportId}/sources.yml`
}

export function snapshotPath(reportId: string, key: string): string {
  return `reports/${reportId}/snapshots/${key}.html`
}

// ── Loading ──────────────────────────────────────────────────────────────────

export function loadSources(vault: string, reportId: string): Promise<SourceRow[]> {
  return window.api.engine.json<SourceRow[]>(vault, ['sources', reportId, '--json'])
}

/**
 * What went wrong, in the words the CLI used.
 *
 * Electron wraps a throw from a handler in `Error invoking remote method 'x': `,
 * which tells the writer nothing they can act on. Strip that wrapper and keep
 * the engine's own message intact — it is the part worth reading.
 */
export function describeError(err: unknown): string {
  const text = err instanceof Error ? err.message : String(err)
  return text.replace(/^Error invoking remote method '[^']+':\s*/, '').replace(/^Error:\s*/, '')
}

export type UseSources = {
  sources: SourceRow[]
  loading: boolean
  /** The engine's stderr when the command failed, else null. */
  error: string | null
  /** Re-run the command — after `cite`, after a save, after a build. */
  reload: () => void
}

/**
 * A report's sources, kept current.
 *
 * `revision` is the parent's "something happened" counter (a build, a save, a
 * vault switch); bumping it reloads. The hook is deliberately owned by the
 * parent rather than by the panel, because the same rows feed the `@`
 * autocompletion and the hover popover, and three copies of one list is three
 * chances for the editor to offer a key the panel says is gone.
 */
export function useSources(
  vault: string | null,
  reportId: string | null,
  revision = 0
): UseSources {
  const [sources, setSources] = useState<SourceRow[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    if (!vault || !reportId) {
      setSources([])
      setError(null)
      setLoading(false)
      return
    }
    let stale = false
    setLoading(true)
    loadSources(vault, reportId)
      .then((rows) => {
        if (stale) return
        setSources(rows)
        setError(null)
      })
      .catch((err) => {
        if (stale) return
        setSources([])
        setError(describeError(err))
      })
      .finally(() => {
        if (!stale) setLoading(false)
      })
    return () => {
      stale = true
    }
  }, [vault, reportId, revision, nonce])

  return { sources, loading, error, reload }
}

/** The day part of an ISO datetime. Snapshot dates are stamped to the second,
 *  and the second is never the thing being read — the date is. */
export function shortDate(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : ''
}

// ── Filtering ────────────────────────────────────────────────────────────────

/** Substring match across the fields a writer would type to find a source.
 *  Not fuzzy: a bibliography is small enough that literal matching is faster to
 *  predict than it is to be clever about. */
export function matches(row: SourceRow, query: string): boolean {
  const needle = query.trim().toLowerCase()
  if (!needle) return true
  return [row.key, row.title, row.author, row.type, row.url ?? '']
    .join(' ')
    .toLowerCase()
    .includes(needle)
}

// ── Dropping a URL on the window ─────────────────────────────────────────────

function droppedUrl(transfer: DataTransfer | null): string | null {
  if (!transfer) return null
  // text/uri-list is the standard form and may carry comment lines; text/plain
  // is what a URL dragged out of a plain text editor arrives as.
  const candidates = [transfer.getData('text/uri-list'), transfer.getData('text/plain')]
  for (const blob of candidates) {
    for (const line of blob.split(/\r?\n/)) {
      const text = line.trim()
      if (!text || text.startsWith('#')) continue
      try {
        const url = new URL(text)
        if (url.protocol === 'http:' || url.protocol === 'https:') return url.toString()
      } catch {
        // Not a URL — a dragged file or a snippet of prose. Ignore it.
      }
    }
  }
  return null
}

/**
 * A URL dropped anywhere on the window.
 *
 * Returns whether a drag is currently over the window, so the caller can show a
 * target rather than leaving the writer guessing whether the drop will land.
 * Window-wide on purpose: the useful gesture is dragging a tab out of a browser
 * onto the app, and asking someone to hit a 200px panel with it is a worse
 * version of typing the URL.
 */
export function useUrlDrop(onUrl: (url: string) => void, enabled = true): boolean {
  const [over, setOver] = useState(false)
  const handler = useRef(onUrl)
  handler.current = onUrl

  useEffect(() => {
    if (!enabled) {
      setOver(false)
      return
    }
    // dragenter/dragleave fire for every element the pointer crosses, so depth
    // is counted rather than toggled — otherwise the hint flickers off the
    // moment the cursor passes over a child.
    let depth = 0

    const onDragEnter = (event: DragEvent): void => {
      if (!event.dataTransfer?.types.some((t) => t === 'text/uri-list' || t === 'text/plain')) return
      depth += 1
      setOver(true)
    }
    const onDragOver = (event: DragEvent): void => {
      if (!event.dataTransfer) return
      // Without preventDefault the browser navigates the window to the URL,
      // which in a packaged app throws away the renderer.
      event.preventDefault()
      event.dataTransfer.dropEffect = 'copy'
    }
    const onDragLeave = (): void => {
      depth = Math.max(0, depth - 1)
      if (depth === 0) setOver(false)
    }
    const onDrop = (event: DragEvent): void => {
      event.preventDefault()
      depth = 0
      setOver(false)
      const url = droppedUrl(event.dataTransfer)
      if (url) handler.current(url)
    }

    window.addEventListener('dragenter', onDragEnter)
    window.addEventListener('dragover', onDragOver)
    window.addEventListener('dragleave', onDragLeave)
    window.addEventListener('drop', onDrop)
    return () => {
      window.removeEventListener('dragenter', onDragEnter)
      window.removeEventListener('dragover', onDragOver)
      window.removeEventListener('dragleave', onDragLeave)
      window.removeEventListener('drop', onDrop)
    }
  }, [enabled])

  return over
}
