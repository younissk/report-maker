/**
 * Vault-wide search, as the engine answers it.
 *
 * `report-maker find "<query>" --json` is the only search in this app. The index
 * lives in the vault and the engine owns it: which folders are reports, which
 * file is a bibliography, which archived page belongs to which `@key` — those
 * are facts about the data model, and a second reading of them in the renderer
 * would be the one that drifts. Nothing here opens a file or matches text.
 *
 * That extends to the highlighting. `highlight` places its `<mark>`s from the
 * character offsets the engine returned rather than searching the excerpt again,
 * because a matcher written here would eventually disagree with the engine's
 * about case folding, accents or word boundaries, and the disagreement would
 * show up as a highlight in the wrong place — the one bug in a search panel that
 * makes the reader distrust the results.
 */

import { createElement, useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import type { Run } from '../../../shared/types'
import { describeError } from '@/lib/sources'

// ── What `find --json` returns ───────────────────────────────────────────────
//
// Declared here rather than in shared/types.ts only because that file has an
// owner; these are the engine's shapes and belong beside the rest of them once
// the two land together.

export const SEARCH_KINDS = ['report', 'source', 'snapshot', 'diagram'] as const

export type SearchKind = (typeof SEARCH_KINDS)[number]

/** A `[start, end)` span of `excerpt` that matched, in characters. */
export type Mark = [number, number]

export type SearchHit = {
  kind: SearchKind
  /** The report id the hit belongs to — every kind of hit belongs to one. */
  report: string
  /** The source key, for `source` and `snapshot` hits; null for prose. */
  key: string | null
  /** Vault-relative POSIX path, the same form `Finding.path` uses. */
  path: string
  line: number
  /** Character offset of the match in the file, for callers that want the span
   *  rather than the line. The panel navigates by line. */
  offset: number
  score: number
  /** The report's title, the source's title, or the file's name. */
  title: string
  excerpt: string
  marks: Mark[]
  /** When the archived copy was taken. Snapshot hits only, and only once the
   *  engine carries it — the row degrades to naming the source alone. */
  fetched?: string | null
}

export type SearchResponse = { hits: SearchHit[] }

// ── Asking the question ──────────────────────────────────────────────────────

/** How long the panel sits on a keystroke. A query is a subprocess and a read of
 *  the index; running one per character would spend the whole budget on prefixes
 *  nobody wanted an answer to. */
const DEBOUNCE_MS = 250

/** The most hits a panel is worth drawing. Past this the writer refines the
 *  query rather than scrolls, and the render cost stops being free. */
export const SEARCH_LIMIT = 200

export function findArgs(
  query: string,
  kinds: readonly SearchKind[],
  limit: number = SEARCH_LIMIT
): string[] {
  const args = ['find', query, '--json', '--limit', String(limit)]
  // Selecting every kind asks the same question as selecting none, and the
  // shorter command line is the one worth sending.
  if (kinds.length > 0 && kinds.length < SEARCH_KINDS.length) {
    for (const kind of kinds) args.push('--kind', kind)
  }
  return args
}

/**
 * The engine's answer, made safe to render.
 *
 * The kind filter is applied a second time over the rows that came back. Not
 * because the flag is doubted — because `--kind` repeated is only a filter if
 * the engine's parser appends, and a parser that keeps the last one would
 * silently answer a narrower question than the chips claim. Filtering rows by
 * the `kind` they carry costs nothing and is right either way.
 */
function readHits(response: SearchResponse | null, kinds: readonly SearchKind[]): SearchHit[] {
  const rows = Array.isArray(response?.hits) ? response.hits : []
  const wanted = kinds.length > 0 ? new Set<string>(kinds) : null
  const hits: SearchHit[] = []
  for (const row of rows) {
    if (wanted && !wanted.has(row.kind)) continue
    hits.push({
      ...row,
      key: row.key ?? null,
      excerpt: row.excerpt ?? '',
      marks: normaliseMarks(row.marks, row.excerpt ?? '')
    })
  }
  return hits
}

// ── The hook ─────────────────────────────────────────────────────────────────

export type UseSearch = {
  query: string
  setQuery: (query: string) => void
  hits: SearchHit[]
  loading: boolean
  /** The engine's own message when `find --json` failed, else null. */
  error: string | null
  /** Which kinds the chips have selected. Empty means all of them. */
  kinds: SearchKind[]
  setKinds: (kinds: SearchKind[]) => void
  /** Re-run the current query — after rebuilding the index, or after a build. */
  reload: () => void
}

/**
 * A live query against the vault.
 *
 * Two things are load-bearing here. The debounce, so typing costs one subprocess
 * rather than one per character. And the generation counter, so a slow answer
 * cannot overwrite a fast one: "acme" started before "acme pricing" and may
 * finish after it, and a promise cannot be cancelled — only disowned.
 */
export function useSearch(vault: string | null): UseSearch {
  const [query, setQuery] = useState('')
  const [kinds, setKinds] = useState<SearchKind[]>([])
  const [hits, setHits] = useState<SearchHit[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  const generation = useRef(0)
  const needle = query.trim()

  useEffect(() => {
    const run = ++generation.current

    if (!vault || needle.length === 0) {
      setHits([])
      setError(null)
      setLoading(false)
      return
    }

    // The spinner starts with the keystroke, not with the subprocess: the pause
    // before the query goes out is still the panel thinking about this query.
    setLoading(true)
    const timer = setTimeout(() => {
      window.api.engine
        .json<SearchResponse>(vault, findArgs(needle, kinds))
        .then((response) => {
          if (run !== generation.current) return
          setHits(readHits(response, kinds))
          setError(null)
        })
        .catch((err) => {
          if (run !== generation.current) return
          setHits([])
          setError(describeError(err))
        })
        .finally(() => {
          if (run === generation.current) setLoading(false)
        })
    }, DEBOUNCE_MS)

    return () => {
      clearTimeout(timer)
      // Anything already in flight belongs to a question that is no longer being
      // asked. Bumping the generation is what makes its answer land nowhere.
      generation.current += 1
    }
  }, [vault, needle, kinds, nonce])

  return { query, setQuery, hits, loading, error, kinds, setKinds, reload }
}

// ── Highlighting ─────────────────────────────────────────────────────────────

/** `<mark>` carries a browser default of black on yellow, which belongs to no
 *  theme this app has. The accent at low opacity reads as a highlight in both. */
const MARK_CLASS = 'rounded-[2px] bg-primary/25 px-px text-foreground'

/**
 * The engine's spans, clamped to the excerpt and made non-overlapping.
 *
 * Two `<mark>`s that overlap would nest, and nested marks render the shared
 * characters twice. Merging is arithmetic over the engine's answer — it never
 * asks the string where a match is.
 */
function normaliseMarks(marks: readonly Mark[] | undefined, excerpt: string): Mark[] {
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
 * Built with `createElement` rather than JSX because this is a `.ts` module, and
 * returned as nodes rather than as an HTML string because a search result is
 * untrusted text out of somebody's report — `dangerouslySetInnerHTML` here would
 * make an archived page able to script the app.
 */
export function highlight(excerpt: string, marks: readonly Mark[] | undefined): ReactNode {
  const spans = normaliseMarks(marks, excerpt)
  if (spans.length === 0) return excerpt

  const nodes: ReactNode[] = []
  let at = 0
  spans.forEach(([start, end], index) => {
    if (start > at) nodes.push(excerpt.slice(at, start))
    nodes.push(createElement('mark', { key: index, className: MARK_CLASS }, excerpt.slice(start, end)))
    at = end
  })
  if (at < excerpt.length) nodes.push(excerpt.slice(at))
  return nodes
}

// ── The index ────────────────────────────────────────────────────────────────

/**
 * Rebuild the vault's index from the files on disk.
 *
 * The one repair the panel offers, and the only reason it exists: a vault edited
 * outside the app — a `git pull`, a report written in another editor — can hold
 * files the index has never seen, and "no results" is indistinguishable from
 * "not indexed yet" from where the reader is sitting.
 */
export function rebuildIndex(vault: string): Promise<Run> {
  return window.api.engine.run(vault, ['index', '--force'])
}
