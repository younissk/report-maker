/**
 * Vault-wide search, over `GET /api/find`.
 *
 * The engine owns the index and every question about it: which folders are
 * reports, which file is a bibliography, which archived page belongs to which
 * `@key`, and what counts as a match. Nothing here opens a file or matches text.
 *
 * That extends to the highlighting. `highlight()` places its `<mark>`s from the
 * character offsets the server returned rather than searching the excerpt again
 * in the browser, because a matcher written here would eventually disagree with
 * the engine's about case folding, accents or word boundaries — and the
 * disagreement shows up as a highlight in the wrong place, the one bug in a
 * search panel that makes a reader stop trusting the results.
 *
 * The kind chips filter the hits that came back rather than narrowing the
 * query, because `api.find(q)` takes no `kind`. That is a display filter over
 * the engine's own answer, not a second search; see `needs` for the route
 * parameter that would make it the engine's filter instead.
 */

import { createElement, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  abortable,
  api,
  ApiError,
  errorText,
  isAbort,
  type SearchHit,
  type SearchResult,
} from './api'
import { guard } from './session'

export type { SearchHit } from './api'

export type Failure = { message: string; detail: string | null }

function describe(error: unknown): Failure {
  if (error instanceof ApiError) return { message: error.message, detail: error.detail }
  return { message: errorText(error), detail: null }
}

// ── The four things a vault holds ────────────────────────────────────────────

export const SEARCH_KINDS = ['report', 'snapshot', 'source', 'diagram'] as const

export type SearchKind = (typeof SEARCH_KINDS)[number]

export type KindMeta = {
  /** The filter chip — what you are switching off. */
  chip: string
  /**
   * The results heading — what you are looking at. A snapshot is a filter over
   * "snapshots" and a group of "archived pages"; the reader meets the second
   * phrase first, and it is the one that says what the text actually is.
   */
  title: string
  hint: string
}

export const KIND_META: Record<SearchKind, KindMeta> = {
  report: {
    chip: 'Reports',
    title: 'Reports',
    hint: 'The prose of every main.typ',
  },
  snapshot: {
    chip: 'Archived pages',
    title: 'Archived pages',
    hint: 'The copy kept of each cited page, as it read on the day it was cited',
  },
  source: {
    chip: 'Sources',
    title: 'Sources',
    hint: 'Keys, titles and authors in sources.yml',
  },
  diagram: {
    chip: 'Diagrams',
    title: 'Diagrams',
    hint: 'The mermaid source in diagrams/*.mmd',
  },
}

/**
 * Display order: what you wrote, then what you kept, then how you cited it.
 * Archived pages sit second on purpose — they are the surprising ones, and at
 * the bottom of the list they would read as an afterthought rather than as the
 * thing no other search box can reach.
 */
export const KIND_ORDER: SearchKind[] = ['report', 'snapshot', 'source', 'diagram']

export function isKnownKind(kind: string): kind is SearchKind {
  return (SEARCH_KINDS as readonly string[]).includes(kind)
}

// ── Highlighting ─────────────────────────────────────────────────────────────

/** A `[start, end)` span of `excerpt` that matched, in characters. */
export type Mark = [number, number]

/** The accent at low opacity reads as a highlight in both themes. */
const MARK_CLASS = 'rounded-[2px] bg-rail-cited/25 px-px text-foreground'

/**
 * The server's spans, clamped to the excerpt and made non-overlapping.
 *
 * Two `<mark>`s that overlap would nest, and nested marks render the shared
 * characters twice. This is arithmetic over the answer — it never asks the
 * string where a match is.
 */
export function normaliseMarks(marks: readonly Mark[] | undefined, excerpt: string): Mark[] {
  if (!Array.isArray(marks) || marks.length === 0) return []

  const clamped: Mark[] = []
  for (const mark of marks) {
    if (!Array.isArray(mark) || mark.length < 2) continue
    const start = Math.max(0, Math.min(excerpt.length, Math.trunc(mark[0])))
    const end = Math.max(0, Math.min(excerpt.length, Math.trunc(mark[1])))
    if (end > start) clamped.push([start, end])
  }
  clamped.sort((a, b) => a[0] - b[0] || a[1] - b[1])

  const merged: Mark[] = []
  for (const [start, end] of clamped) {
    const last = merged[merged.length - 1]
    if (last && start <= last[1]) last[1] = Math.max(last[1], end)
    else merged.push([start, end])
  }
  return merged
}

/**
 * An excerpt with the matched spans wrapped in `<mark>`.
 *
 * Returned as React nodes, never as an HTML string: an excerpt is untrusted
 * text out of somebody's report or out of an archived page somebody else wrote,
 * and `dangerouslySetInnerHTML` here would let an archived page script the app.
 * `createElement` rather than JSX because this is a `.ts` module.
 */
export function highlight(excerpt: string, marks: readonly Mark[] | undefined): ReactNode {
  const spans = normaliseMarks(marks, excerpt)
  if (spans.length === 0) return excerpt

  const nodes: ReactNode[] = []
  let at = 0
  spans.forEach(([start, end], index) => {
    if (start > at) nodes.push(excerpt.slice(at, start))
    nodes.push(
      createElement('mark', { key: index, className: MARK_CLASS }, excerpt.slice(start, end))
    )
    at = end
  })
  if (at < excerpt.length) nodes.push(excerpt.slice(at))
  return nodes
}

// ── Dates ────────────────────────────────────────────────────────────────────

/** `2026-08-18 09:12:33` or an ISO stamp → `18 Aug 2026`. Falls back to the
 *  string it was given: a date nobody can parse is still worth showing. */
export function shortDate(stamp: string | null | undefined): string {
  if (!stamp) return ''
  const day = stamp.split(/[ T]/)[0]
  const parsed = new Date(`${day}T00:00:00`)
  if (Number.isNaN(parsed.getTime())) return stamp
  return parsed.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

// ── Asking the question ──────────────────────────────────────────────────────

/**
 * How long the field sits on a keystroke. A query is a subprocess and a read of
 * the index server-side; one per character would spend the whole command quota
 * on prefixes nobody wanted an answer to.
 */
export const DEBOUNCE_MS = 250

export type UseSearch = {
  query: string
  setQuery: (query: string) => void
  /** Every hit the server returned, unfiltered. */
  all: SearchHit[]
  /** The hits the chips leave showing. */
  hits: SearchHit[]
  /** Kinds switched off. Empty means everything, which is also what "all" means. */
  hidden: SearchKind[]
  toggleKind: (kind: SearchKind) => void
  showAll: () => void
  /** Hits per kind, over `all` — so a chip can say what it is hiding. */
  counts: Record<string, number>
  loading: boolean
  error: Failure | null
  reload: () => void
  clear: () => void
}

export function useSearch(): UseSearch {
  const [query, setQuery] = useState('')
  const [all, setAll] = useState<SearchHit[]>([])
  const [hidden, setHidden] = useState<SearchKind[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<Failure | null>(null)
  const [nonce, setNonce] = useState(0)

  // One flight at a time, and the last keystroke wins. Without this the answer
  // to "pric" can land after the answer to "pricing" and overwrite it.
  const find = useMemo(() => abortable(api.find), [])
  const latest = useRef('')

  useEffect(() => () => find.cancel(), [find])

  useEffect(() => {
    const trimmed = query.trim()
    latest.current = trimmed

    if (!trimmed) {
      find.cancel()
      setAll([])
      setError(null)
      setLoading(false)
      return
    }

    setLoading(true)
    const timer = window.setTimeout(() => {
      // `guard` for the one-shot session repair, `find` for the supersede: the
      // retry after a 401 is a fresh call through the same abortable, so a
      // keystroke during the repair still cancels it.
      void guard<SearchResult>(() => find(trimmed))
        .then((result) => {
          if (latest.current !== trimmed) return
          setAll(result.hits ?? [])
          setError(null)
          setLoading(false)
        })
        .catch((failure) => {
          if (isAbort(failure) || latest.current !== trimmed) return
          setError(describe(failure))
          setAll([])
          setLoading(false)
        })
    }, DEBOUNCE_MS)

    return () => window.clearTimeout(timer)
  }, [query, nonce, find])

  const counts = useMemo(() => {
    const tally: Record<string, number> = {}
    for (const hit of all) tally[hit.kind] = (tally[hit.kind] ?? 0) + 1
    return tally
  }, [all])

  const hits = useMemo(
    () => (hidden.length === 0 ? all : all.filter((hit) => !hidden.includes(hit.kind as SearchKind))),
    [all, hidden]
  )

  const toggleKind = useCallback((kind: SearchKind) => {
    setHidden((current) =>
      current.includes(kind) ? current.filter((k) => k !== kind) : [...current, kind]
    )
  }, [])

  const showAll = useCallback(() => setHidden([]), [])
  const reload = useCallback(() => setNonce((n) => n + 1), [])
  const clear = useCallback(() => {
    setQuery('')
    setAll([])
    setError(null)
  }, [])

  return {
    query,
    setQuery,
    all,
    hits,
    hidden,
    toggleKind,
    showAll,
    counts,
    loading,
    error,
    reload,
    clear,
  }
}

/** The hits of one kind, in the order the server ranked them. */
export function hitsOfKind(hits: readonly SearchHit[], kind: SearchKind): SearchHit[] {
  return hits.filter((hit) => hit.kind === kind)
}

/** Anything the server returned under a kind this build has never heard of. */
export function unknownKindHits(hits: readonly SearchHit[]): SearchHit[] {
  return hits.filter((hit) => !isKnownKind(hit.kind))
}
