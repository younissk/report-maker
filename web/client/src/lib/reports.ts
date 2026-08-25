/**
 * Everything the Reports tab needs to know about a vault, asked for once.
 *
 * The rule this file exists to keep: **three requests draw the whole screen, not
 * three per card.** `list --json` for the rows, `score --json` for evidence
 * density and `todos --json` for the pads each already walk the entire vault
 * server-side, so a vault of eighty reports costs the same three engine
 * subprocesses as a vault of one. Findings are not fetched here at all — the app
 * shell already holds `check --json --score` for the whole vault and hands it
 * over through `useApp()`, and a second `/api/check` would be a second identical
 * subprocess for an answer already on screen.
 *
 * Nothing in this file decides anything about a report. It indexes what the
 * server said by id and hands it back. Two functions come close and are marked
 * where they sit: {@link tallyFindings}, which groups findings the engine
 * emitted, and {@link slugPreview}, which is the one string this side derives —
 * and only when it can be certain the engine would derive the same one.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  api,
  errorText,
  isAbort,
  type Finding,
  type ReportRow,
  type ReportScore,
  type ScoreResult,
  type TemplateMap,
} from '@/lib/api'
import { guard } from '@/lib/session'

// ── small words ──────────────────────────────────────────────────────────────

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

/** A density, as the engine's own fraction. Never averaged on this side. */
export function percent(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${Math.round(value * 100)}%`
}

/** A report id is a path — `clients/acme/2026-08-12-audit`. This is the folder. */
export function basename(id: string): string {
  const parts = id.replace(/\/+$/, '').split('/')
  return parts[parts.length - 1] || id
}

// ── findings, grouped by the report they were reported against ───────────────

export type Tally = { errors: number; warnings: number }

/**
 * `check --json`'s findings, counted per report.
 *
 * This is grouping, not judging: every finding already names its `report`, its
 * `level` and its code, and none of that is decided here. Reports the check
 * visited and had nothing to say about are seeded at zero so their card can say
 * "clean" — a card with no chip at all would be indistinguishable from one whose
 * check has not run, and silence is the honest answer only in the second case.
 */
export function tallyFindings(
  findings: Finding[] | null | undefined,
  rows: ReportRow[]
): Map<string, Tally> {
  const map = new Map<string, Tally>()
  if (!findings) return map
  for (const finding of findings) {
    const tally = map.get(finding.report) ?? { errors: 0, warnings: 0 }
    if (finding.level === 'error') tally.errors += 1
    else tally.warnings += 1
    map.set(finding.report, tally)
  }
  for (const row of rows) if (!map.has(row.id)) map.set(row.id, { errors: 0, warnings: 0 })
  return map
}

// ── the vault, read once ─────────────────────────────────────────────────────

export type VaultReports = {
  /** `list --json`, in the order the engine printed it. */
  rows: ReportRow[]
  /** `score --json`, indexed by report id. Empty when the score could not be read. */
  scores: Map<string, ReportScore>
  /**
   * The vault's own totals, verbatim from `score --json`. Never recomputed: a
   * density averaged over reports in a browser weights a one-line draft the same
   * as a forty-page audit and then disagrees with what the CLI prints.
   */
  totals: ScoreResult | null
  /** Open tasks on each report's pad, from one `todos --json`. */
  open: Map<string, number>
  /** True until the listing has answered once. */
  loading: boolean
  /**
   * The listing failed. Score and pads failing is not an error — a missing chip
   * is a smaller lie than an error page over a working list.
   */
  error: string | null
  reload: () => void
}

const NO_SCORES: Map<string, ReportScore> = new Map()
const NO_OPEN: Map<string, number> = new Map()

/**
 * The three vault-wide reads, refetched whenever `revision` moves.
 *
 * `revision` comes from `useApp()` and is bumped by anything that wrote to the
 * vault, so a card's density and its thumbnail follow a build without this file
 * having to know what a build is.
 */
export function useVaultReports(revision: number): VaultReports {
  const [rows, setRows] = useState<ReportRow[]>([])
  const [scores, setScores] = useState<Map<string, ReportScore>>(NO_SCORES)
  const [totals, setTotals] = useState<ScoreResult | null>(null)
  const [open, setOpen] = useState<Map<string, number>>(NO_OPEN)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    const controller = new AbortController()
    let live = true
    setLoading(true)

    void guard((signal) => api.listReports(signal), controller.signal)
      .then((listing) => {
        if (!live) return
        setRows(listing)
        setError(null)
      })
      .catch((cause) => {
        if (!live || isAbort(cause)) return
        setRows([])
        setError(errorText(cause))
      })
      .finally(() => {
        if (live) setLoading(false)
      })

    void guard((signal) => api.score(undefined, signal), controller.signal)
      .then((result) => {
        if (!live) return
        setTotals(result)
        setScores(new Map(result.reports.map((entry) => [entry.id, entry])))
      })
      .catch(() => {
        // No density on the cards. The list is still the useful thing.
      })

    void guard((signal) => api.todos(undefined, undefined, signal), controller.signal)
      .then((result) => {
        if (!live) return
        setOpen(new Map(result.reports.map((entry) => [entry.id, entry.open])))
      })
      .catch(() => {
        // A report with nothing on its pad is absent from the map anyway, so a
        // failed read and an empty pad look the same — which is correct here.
      })

    return () => {
      live = false
      controller.abort()
    }
  }, [revision, nonce])

  return { rows, scores, totals, open, loading, error, reload }
}

// ── the designs, for the new-report form ─────────────────────────────────────

export type Templates = {
  /** `templates --json`, sorted by group then title, as `[id, row]` pairs. */
  order: [string, TemplateMap[string]][]
  map: TemplateMap
  loading: boolean
  error: string | null
}

/** `templates --json`, read only while the form that needs it is open. */
export function useTemplates(enabled: boolean): Templates {
  const [map, setMap] = useState<TemplateMap>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    let live = true
    setLoading(true)
    setError(null)

    void guard((signal) => api.templates(signal), controller.signal)
      .then((found) => {
        if (live) setMap(found)
      })
      .catch((cause) => {
        if (!live || isAbort(cause)) return
        setError(errorText(cause))
      })
      .finally(() => {
        if (live) setLoading(false)
      })

    return () => {
      live = false
      controller.abort()
    }
  }, [enabled])

  const order = useMemo(
    () =>
      Object.entries(map).sort(
        ([idA, a], [idB, b]) =>
          (a.group || '').localeCompare(b.group || '') ||
          (a.title || idA).localeCompare(b.title || idB)
      ),
    [map]
  )

  return { order, map, loading, error }
}

// ── the folder a new report will get ─────────────────────────────────────────

/**
 * Every group that exists, plus the folders above them.
 *
 * Filing a report beside `clients/acme` often means filing it in `clients`, and
 * a suggestion list that offers only leaf folders makes that a typing exercise.
 * Read out of the `group` column the engine printed — nothing is invented.
 */
export function groupsIn(rows: ReportRow[]): string[] {
  const seen = new Set<string>()
  for (const row of rows) if (row.group) seen.add(row.group)
  for (const group of [...seen]) {
    const parts = group.split('/')
    for (let i = 1; i < parts.length; i += 1) seen.add(parts.slice(0, i).join('/'))
  }
  return [...seen].sort()
}

/**
 * The author of the most recent report, offered as the default for the next.
 *
 * A vault is usually one person's. This is a suggestion in an editable field,
 * taken from what `list --json` printed — not a fact this side stores.
 */
export function lastAuthor(rows: ReportRow[]): string {
  const written = rows.filter((row) => (row.author ?? '').trim().length > 0)
  const newest = [...written].sort((a, b) => (b.date ?? '').localeCompare(a.date ?? ''))[0]
  return newest?.author?.trim() ?? ''
}

/** How much of the folder name this side can honestly claim to know. */
export type Slug = { text: string; state: 'ready' | 'empty' | 'unknown' }

/** `engine/scaffold.py:slugify`, transcribed. Only ever used behind {@link derivable}. */
function slugify(title: string): string {
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return slug || 'report'
}

/**
 * True when the transcription above is certain to agree with Python's.
 *
 * Lowercasing is identical on both sides for ASCII, and every non-letter — an em
 * dash, a Devanagari digit, an emoji — becomes `-` in both without either
 * implementation consulting a case table. A non-ASCII *letter* is the one place
 * the two could disagree, and one wrong path in the preview costs more trust
 * than admitting that the engine decides.
 */
function derivable(title: string): boolean {
  return ![...title].some((ch) => ch.charCodeAt(0) > 127 && /\p{L}/u.test(ch))
}

export function slugPreview(title: string): Slug {
  if (!title.trim()) return { text: '…', state: 'empty' }
  if (!derivable(title)) return { text: '…', state: 'unknown' }
  return { text: slugify(title), state: 'ready' }
}

/** The engine's `into` normalisation, so preview and argument are one string. */
export function normaliseGroup(group: string): string {
  return group.trim().replace(/^\/+|\/+$/g, '')
}

/**
 * Today, locally. `toISOString()` is UTC and files an evening report under
 * tomorrow's date for anybody east of Greenwich.
 */
export function todayISO(): string {
  const now = new Date()
  const pad = (value: number): string => String(value).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

/** The folder `new` will create, as far as this side can honestly name it. */
export function folderFor(group: string, slug: Slug, date = todayISO()): string {
  const clean = normaliseGroup(group)
  return `reports/${clean ? `${clean}/` : ''}${date}-${slug.text}`
}
