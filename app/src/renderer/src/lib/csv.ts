/**
 * A CSV, held as text and written back as text.
 *
 * The file is the truth and the grid is a view over it. That is not a stylistic
 * preference: a data file in this system is *evidence*, registered in
 * `sources.yml` under a sha256, and the moment an editor owns the data rather
 * than the bytes it starts producing files that differ from the ones it opened.
 * A spreadsheet component that reformats quoting, normalises line endings or
 * rewrites a semicolon dialect on open would change the checksum of a file
 * nobody edited — turning E011, the one rule standing between a refreshed export
 * and a signed-off report, into noise people learn to clear.
 *
 * So the round trip is defended twice. The dialect — delimiter, quote
 * character, newline, trailing newline, BOM — is read off the file and written
 * back unchanged. And every record keeps its own `raw` text: a row nobody
 * touched is re-emitted byte for byte rather than re-encoded, so an untouched
 * file serialises to exactly the bytes it was read from, and a one-cell edit
 * shows up in git as a one-line diff.
 *
 * Nothing here decides anything the engine decides. `report-maker data check`
 * owns W007/W008/W009, `data status` owns what `sources.yml` records, and
 * `data revise` is the only thing in the system allowed to move a recorded
 * checksum. This module reads a file, writes a file, and asks those commands.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Run } from '../../../shared/types'
import { relative } from '@/lib/report'
import { describeError } from '@/lib/sources'

// ── The dialect ──────────────────────────────────────────────────────────────

/** The separators worth sniffing, in the order a tie is broken. Matches the set
 *  `engine/data.py:sniff` hands to `csv.Sniffer`. */
export const DELIMITERS = [',', ';', '\t', '|'] as const

export type Dialect = {
  delimiter: string
  /** Always `"` in practice; kept a field so the parser and the writer read the
   *  same value rather than each hard-coding it. */
  quote: string
  newline: '\n' | '\r\n'
  /** The file ended with a line terminator. Most tools write one; a file that
   *  did not must not grow one, or its first save is a whitespace-only diff. */
  finalNewline: boolean
  /** A UTF-8 BOM led the file. Spreadsheets on Windows write one, and dropping
   *  it silently would change every byte offset in the file. */
  bom: boolean
}

/** One record of the file. */
export type Row = {
  cells: string[]
  /**
   * The record verbatim, without its line terminator — or null once the row has
   * been edited. This is what makes an untouched row round-trip exactly: it is
   * re-emitted rather than re-quoted, so a cell somebody wrapped in quotes for
   * their own reasons still is one after a save that never touched it.
   */
  raw: string | null
}

export type Sheet = {
  dialect: Dialect
  rows: Row[]
  /**
   * Whether the first record names the columns. A *view* decision: the engine
   * sniffs this itself when it builds the table (`engine/data.py:_has_header`),
   * so toggling it here changes how the grid reads the file and nothing about
   * the file or the report.
   */
  header: boolean
}

const DELIMITER_NAMES: Record<string, string> = {
  ',': 'comma',
  ';': 'semicolon',
  '\t': 'tab',
  '|': 'pipe'
}

export function delimiterName(delimiter: string): string {
  return DELIMITER_NAMES[delimiter] ?? JSON.stringify(delimiter)
}

/** The dialect in words, for a chip that tells the writer what will be written
 *  back. A dialect nobody can see is a dialect nobody can tell is wrong. */
export function describeDialect(dialect: Dialect): string {
  return [
    `${delimiterName(dialect.delimiter)}-separated`,
    dialect.newline === '\r\n' ? 'CRLF' : 'LF',
    dialect.bom ? 'BOM' : null,
    dialect.finalNewline ? null : 'no trailing newline'
  ]
    .filter(Boolean)
    .join(' · ')
}

/**
 * Count a candidate delimiter on one line, ignoring anything inside quotes.
 *
 * Quote-aware because the failure it prevents is common and silent: a file whose
 * free-text column contains commas would otherwise sniff as comma-separated
 * however it was actually written.
 */
function countOutsideQuotes(line: string, delimiter: string, quote: string): number {
  let count = 0
  let inside = false
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i]
    if (ch === quote) {
      if (inside && line[i + 1] === quote) {
        i += 1
        continue
      }
      inside = !inside
      continue
    }
    if (!inside && ch === delimiter) count += 1
  }
  return count
}

/**
 * The delimiter the file actually uses.
 *
 * The winner is the candidate that appears the same number of times on the most
 * lines — a real separator is regular, a comma inside prose is not — and the
 * extension is the fallback, because a single-column file has no delimiter to
 * find. Same shape of answer as `engine/data.py:sniff`, and the same fallback,
 * so the grid and the built table read one file the same way. When they still
 * disagree the toolbar's delimiter control is the way out; a sniffer with no
 * override is a guess the writer cannot correct.
 */
export function sniffDelimiter(text: string, path?: string | null): string {
  const fallback = /\.(tsv|tab)$/i.test(path ?? '') ? '\t' : ','
  const lines = text.split(/\r?\n/).slice(0, 20).filter((line) => line.trim().length > 0)
  if (lines.length === 0) return fallback

  let best = fallback
  let bestScore = 0
  for (const candidate of DELIMITERS) {
    const counts = lines.map((line) => countOutsideQuotes(line, candidate, '"'))
    const first = counts[0]
    if (first === 0) continue
    const agreeing = counts.filter((count) => count === first).length
    // Weighted by how many separators each line carries, so a file that is both
    // comma- and pipe-consistent resolves to the one doing the actual work.
    const score = agreeing * 100 + first
    if (score > bestScore) {
      bestScore = score
      best = candidate
    }
  }
  return best
}

// ── Reading ──────────────────────────────────────────────────────────────────

/**
 * Parse a CSV into records, keeping each record's original text.
 *
 * RFC 4180 quoting, leniently: a quote in the middle of an unquoted field is a
 * literal quote rather than an error, because a data file exported by somebody
 * else's script is not something to refuse to display. Anything the parser is
 * unsure about survives anyway — the row keeps its `raw`, and an untouched row
 * is written back from that rather than from the cells.
 */
export function parseCsv(text: string, path?: string | null, delimiter?: string): Sheet {
  const bom = text.charCodeAt(0) === 0xfeff
  const body = bom ? text.slice(1) : text
  const quote = '"'
  const dialect: Dialect = {
    delimiter: delimiter ?? sniffDelimiter(body, path),
    quote,
    newline: body.includes('\r\n') ? '\r\n' : '\n',
    finalNewline: body.length > 0 && (body.endsWith('\n') || body.endsWith('\r')),
    bom
  }

  const rows: Row[] = []
  let cells: string[] = []
  let field = ''
  let quoted = false
  let start = 0
  let i = 0

  while (i < body.length) {
    const ch = body[i]
    if (quoted) {
      if (ch === quote) {
        if (body[i + 1] === quote) {
          field += quote
          i += 2
          continue
        }
        quoted = false
        i += 1
        continue
      }
      field += ch
      i += 1
      continue
    }
    if (ch === quote && field === '') {
      quoted = true
      i += 1
      continue
    }
    if (ch === dialect.delimiter) {
      cells.push(field)
      field = ''
      i += 1
      continue
    }
    if (ch === '\n' || ch === '\r') {
      const width = ch === '\r' && body[i + 1] === '\n' ? 2 : 1
      cells.push(field)
      rows.push({ cells, raw: body.slice(start, i) })
      cells = []
      field = ''
      i += width
      start = i
      continue
    }
    field += ch
    i += 1
  }
  // A final record with no terminator. The guard is on the offset rather than on
  // the buffers, so a file ending in a newline does not gain a phantom empty row.
  if (start < body.length) {
    cells.push(field)
    rows.push({ cells, raw: body.slice(start) })
  }

  return { dialect, rows, header: looksLikeHeader(rows.map((row) => row.cells)) }
}

// ── Writing ──────────────────────────────────────────────────────────────────

/** Quote only when the value would otherwise not survive the round trip. Excel's
 *  rule, and the one that keeps a diff small. */
export function encodeCell(value: string, dialect: Dialect): string {
  const needs =
    value.includes(dialect.delimiter) ||
    value.includes(dialect.quote) ||
    value.includes('\n') ||
    value.includes('\r')
  if (!needs) return value
  const escaped = value.split(dialect.quote).join(dialect.quote + dialect.quote)
  return dialect.quote + escaped + dialect.quote
}

export function encodeRow(cells: string[], dialect: Dialect): string {
  return cells.map((cell) => encodeCell(cell, dialect)).join(dialect.delimiter)
}

/**
 * The sheet as bytes-to-be.
 *
 * Untouched rows come back from `raw`, which is what makes an open-and-save with
 * no edits a no-op on disk — and therefore leaves the registered checksum alone.
 */
/** The UTF-8 byte order mark, spelled as an escape: a literal one in the
 *  source is invisible, and this file is about not losing bytes silently. */
const BOM = '\ufeff'

export function serialiseCsv(sheet: Sheet): string {
  const lines = sheet.rows.map((row) => row.raw ?? encodeRow(row.cells, sheet.dialect))
  let text = lines.join(sheet.dialect.newline)
  if (sheet.dialect.finalNewline && text.length > 0) text += sheet.dialect.newline
  return (sheet.dialect.bom ? BOM : '') + text
}

// ── Editing, as pure functions over a sheet ──────────────────────────────────
//
// Every one of these drops `raw` for the rows it touches and only for those, so
// the "an untouched row is written back verbatim" promise survives an edit
// session rather than only a fresh open.

export function width(sheet: Sheet): number {
  return sheet.rows.reduce((most, row) => Math.max(most, row.cells.length), 0)
}

function padded(cells: string[], to: number): string[] {
  if (cells.length >= to) return cells
  return [...cells, ...Array<string>(to - cells.length).fill('')]
}

export function cellAt(sheet: Sheet, row: number, column: number): string {
  return sheet.rows[row]?.cells[column] ?? ''
}

export function setCell(sheet: Sheet, row: number, column: number, value: string): Sheet {
  const rows = sheet.rows.map((existing, index) => {
    if (index !== row) return existing
    // A row that ran out of separators early is padded rather than left short:
    // the missing cell is a value that is not there, and the grid says so.
    const cells = padded([...existing.cells], column + 1)
    cells[column] = value
    return { cells, raw: null }
  })
  return { ...sheet, rows }
}

export function insertRow(sheet: Sheet, at: number): Sheet {
  const blank: Row = { cells: Array<string>(Math.max(width(sheet), 1)).fill(''), raw: null }
  const rows = [...sheet.rows]
  rows.splice(Math.max(0, Math.min(at, rows.length)), 0, blank)
  return { ...sheet, rows }
}

export function removeRow(sheet: Sheet, at: number): Sheet {
  if (at < 0 || at >= sheet.rows.length) return sheet
  const rows = [...sheet.rows]
  rows.splice(at, 1)
  return { ...sheet, rows }
}

export function insertColumn(sheet: Sheet, at: number): Sheet {
  const target = Math.max(0, Math.min(at, width(sheet)))
  return {
    ...sheet,
    rows: sheet.rows.map((row) => {
      const cells = padded([...row.cells], target)
      cells.splice(target, 0, '')
      return { cells, raw: null }
    })
  }
}

export function removeColumn(sheet: Sheet, at: number): Sheet {
  return {
    ...sheet,
    rows: sheet.rows.map((row) => {
      if (at >= row.cells.length) return row
      const cells = [...row.cells]
      cells.splice(at, 1)
      return { cells, raw: null }
    })
  }
}

// ── What the columns look like ───────────────────────────────────────────────

/**
 * Ornament a number can wear and still be one.
 *
 * Kept in step with `ORNAMENT` in `engine/data.py` and `_ORNAMENT` in
 * `engine/templates/base/data.typ`. Here it decides two cosmetic things — which
 * columns are right-aligned, and whether the first row looks like labels — so a
 * disagreement costs an alignment, never a number.
 */
const ORNAMENT = [',', ' ', '\u00a0', '%', '$', '€', '£', '(', ')', "'", '+', '_']

export function looksNumeric(cell: string): boolean {
  let stripped = cell.trim()
  for (const token of ORNAMENT) stripped = stripped.split(token).join('')
  if (!stripped) return false
  return Number.isFinite(Number(stripped))
}

/**
 * Whether the first row names the columns.
 *
 * Shape decides it, the way `engine/data.py:_has_header` does: a row of labels
 * has no bare numbers in it, and a table of numbers has them somewhere below the
 * labels. The engine asks `csv.Sniffer` about the genuinely ambiguous case; here
 * the ambiguous case defaults to "yes, a header", because the toggle is one
 * click away and a mislabelled first row is obvious on screen.
 */
export function looksLikeHeader(rows: string[][]): boolean {
  if (rows.length < 2) return false
  if (rows[0].some(looksNumeric)) return false
  return true
}

export type ColumnStat = {
  index: number
  /** What the engine calls this column in a W007/W008/W009 message: the header
   *  text, or its 1-based position. The join between a finding and a column. */
  name: string
  /** What the grid prints. */
  label: string
  /** Body rows with nothing in this column. The number this editor exists for. */
  empty: number
  /** Body rows considered. */
  rows: number
  /** Every non-empty cell reads as a number — the column is right-aligned. */
  numeric: boolean
}

export function columnStats(sheet: Sheet): ColumnStat[] {
  const total = width(sheet)
  const body = sheet.header ? sheet.rows.slice(1) : sheet.rows
  const headers = sheet.header ? (sheet.rows[0]?.cells ?? []) : []

  const stats: ColumnStat[] = []
  for (let index = 0; index < total; index += 1) {
    const cells = body.map((row) => (row.cells[index] ?? '').trim())
    const filled = cells.filter((cell) => cell.length > 0)
    const header = (headers[index] ?? '').trim()
    stats.push({
      index,
      name: header || `column ${index + 1}`,
      label: header || `column ${index + 1}`,
      empty: cells.length - filled.length,
      rows: cells.length,
      numeric: filled.length > 0 && filled.every(looksNumeric)
    })
  }
  return stats
}

// ── The engine's answers ─────────────────────────────────────────────────────

/** One row of `data check --json` — `engine/data.py:findings_json`. Declared
 *  here rather than in `shared/types.ts` only because that file belongs to
 *  another workflow; it is the same shape as `Finding` without `report`. */
export type DataFinding = {
  level: 'error' | 'warning'
  code: string
  /** Vault-relative POSIX. */
  path: string
  line: number
  message: string
}

/** One dated revision — `engine/datarev.py:to_json`. */
export type RevisionRow = {
  rel: string
  path: string
  date: string
  sha256: string
  rows: number
  columns: number
  size: number
}

/** `data status <report> <csv> --json` — `engine/datarev.py:status`. */
export type DataStatus = {
  report: string
  key: string | null
  rel: string
  exists: boolean
  registered: boolean
  current_sha: string | null
  recorded_sha: string | null
  /** True only when both checksums are known and equal. An unregistered file is
   *  not "matching" — there is nothing for it to match. */
  matches: boolean
  rows: number | null
  columns: number | null
  revisions: RevisionRow[]
}

/** What `data revise` hands back — `engine/datarev.py:reregister`. */
export type ReviseSummary = {
  report: string
  key: string
  rel: string
  date: string
  old_sha: string | null
  new_sha: string
  rows_before: number | null
  rows_after: number
  columns_before: number | null
  columns_after: number
  delta: number | null
  /** Report-relative path of the copy that was preserved, or null when the bytes
   *  were already archived. */
  archived: string | null
  note: string | null
  /** The ready-made sentence: "412 rows → 418 rows, +6". Built in the engine
   *  because the app holds no logic of its own. */
  headline: string
}

function parseJson<T>(text: string): T | null {
  try {
    return JSON.parse(text) as T
  } catch {
    return null
  }
}

/** The last thing the process said that is worth showing. */
function tail(result: Run): string {
  const text = (result.stderr || result.stdout || `exit ${result.code}`).trimEnd()
  const lines = text.split('\n').filter((line) => line.trim().length > 0)
  return lines[lines.length - 1] ?? text
}

/**
 * Whether the engine simply does not have this subcommand.
 *
 * The CSV editor is ahead of the CLI wiring for `data revise`, `data status` and
 * `data revisions`, and an argparse "invalid choice" is not a failure worth
 * showing as a stack of stderr. It is a fact about the installed engine, and the
 * caller says so in a sentence with the fallback in it.
 */
const UNSUPPORTED = /invalid choice|unrecognized arguments?|invalid subcommand/i

export function unsupported(result: Run): boolean {
  return result.code !== 0 && UNSUPPORTED.test(result.stderr || result.stdout)
}

/**
 * Run a `--json` subcommand and read stdout even when the exit code is not zero.
 *
 * `window.api.engine.json` throws on a non-zero exit, which is right for most
 * commands and wrong for this one: `data check --json` exits 1 precisely when it
 * has errors to report, so throwing would discard the findings at the moment
 * they matter most. Same reasoning as `App.tsx` uses for `check --json`.
 */
async function jsonOf<T>(vault: string, args: string[]): Promise<{ value: T | null; run: Run }> {
  const run = await window.api.engine.run(vault, args)
  return { value: parseJson<T>(run.stdout), run }
}

// ── Where a report keeps its numbers ─────────────────────────────────────────

/** The path as the engine's `data` commands take it: vault-relative POSIX,
 *  which `engine/datarev.py:_locate` resolves against the vault root. */
export function vaultRelative(vault: string, path: string): string {
  return relative(vault, path)
}

/** The path as `sources.yml` records it — relative to the report folder, which
 *  is the half that stays true when the report is filed somewhere else. */
export function reportRelative(vault: string, path: string, reportId: string | null): string {
  const rel = relative(vault, path)
  const prefix = `reports/${reportId}/`
  return reportId && rel.startsWith(prefix) ? rel.slice(prefix.length) : rel
}

export function isCsvPath(path: string): boolean {
  return /\.(csv|tsv|tab)$/i.test(path)
}

/**
 * What the linter can tell us when `data status` is not available.
 *
 * E011 names the file in its message and points at `sources.yml`; the "not
 * registered" W006 names it too. Neither is as good as `status`, but both are
 * enough to keep the banner honest on an engine that predates the command.
 */
export type DerivedState = 'stale' | 'unregistered' | 'clean' | 'unknown'

export function derivedState(findings: DataFinding[], reportRel: string): DerivedState {
  if (findings.length === 0) return 'unknown'
  const mine = findings.filter((finding) => finding.message.includes(reportRel))
  if (mine.some((finding) => finding.code === 'E011')) return 'stale'
  if (mine.some((finding) => finding.code === 'W006' && /not registered/.test(finding.message)))
    return 'unregistered'
  return 'clean'
}

/** The column a W007/W008/W009 is about, or null for a finding about the file as
 *  a whole. The message opens `<rel>: "<column>" …`; the engine names a column by
 *  its header, or by `column <n>` when the file declares none. */
const COLUMN_IN_MESSAGE = /:\s*"([^"]*)"/

export function columnOfFinding(finding: DataFinding, columns: ColumnStat[]): number | null {
  const match = COLUMN_IN_MESSAGE.exec(finding.message)
  if (!match) return null
  const name = match[1]
  const named = columns.find((column) => column.name === name)
  if (named) return named.index
  const positional = /^column (\d+)$/.exec(name)
  return positional ? Number(positional[1]) - 1 : null
}

export function warningsByColumn(
  findings: DataFinding[],
  columns: ColumnStat[]
): Map<number, DataFinding[]> {
  const found = new Map<number, DataFinding[]>()
  for (const finding of findings) {
    const index = columnOfFinding(finding, columns)
    if (index === null) continue
    const existing = found.get(index)
    if (existing) existing.push(finding)
    else found.set(index, [finding])
  }
  return found
}

// ── The hook ─────────────────────────────────────────────────────────────────

export type UseCsv = {
  sheet: Sheet | null
  /** The grid's rows, header row included at index 0 when there is one. */
  rows: string[][]
  header: boolean
  setHeader: (next: boolean) => void
  columns: ColumnStat[]
  dirty: boolean
  loading: boolean
  /** Reading or writing the file failed, in the words the process used. */
  error: string | null
  /** Replace the sheet — the pure editors above produce the argument. */
  edit: (next: Sheet) => void
  /** Re-read the same text with a different delimiter, for the file the sniffer
   *  got wrong. Refuses while there are unsaved edits, which would be lost. */
  reparse: (delimiter: string) => void
  save: () => Promise<void>
  /** `data revise` — the only path in the system that moves a recorded sha. */
  revise: (note?: string) => Promise<ReviseSummary | null>
  /** `data add` — register a file that no entry stands for yet. */
  register: () => Promise<boolean>
  reload: () => void
  status: DataStatus | null
  /** Why the engine could not answer, when it could not. */
  statusNote: string | null
  derived: DerivedState
  /** Every `data check` finding about this file. */
  warnings: DataFinding[]
  /** True while a `data …` subprocess is in flight. */
  busy: boolean
  /** The last engine failure, verbatim. */
  failure: string | null
  /** The last successful revision, for the receipt the dialog shows. */
  lastRevision: ReviseSummary | null
  paths: { vaultRel: string; reportRel: string }
}

/**
 * One CSV, its dialect, and what the engine says about it.
 *
 * Reading and writing go through `window.api.files`, which is confined to the
 * vault; everything else is a `report-maker` subprocess. The hook deliberately
 * does not re-read after a save it performed — the bytes on disk are the ones it
 * just wrote — but it does re-ask the engine, because saving a registered file
 * is exactly the moment its checksum stops matching.
 */
export function useCsv(vault: string, path: string, reportId: string | null): UseCsv {
  const [sheet, setSheet] = useState<Sheet | null>(null)
  const [original, setOriginal] = useState('')
  const [dirty, setDirty] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<DataStatus | null>(null)
  const [statusNote, setStatusNote] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<DataFinding[]>([])
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [lastRevision, setLastRevision] = useState<ReviseSummary | null>(null)
  const [nonce, setNonce] = useState(0)
  const [engineNonce, setEngineNonce] = useState(0)

  const paths = useMemo(
    () => ({
      vaultRel: vaultRelative(vault, path),
      reportRel: reportRelative(vault, path, reportId)
    }),
    [vault, path, reportId]
  )

  // The live sheet, for callbacks that outlive the render that made them.
  const latest = useRef<Sheet | null>(null)
  latest.current = sheet

  const reload = useCallback(() => setNonce((value) => value + 1), [])

  // ── the file
  useEffect(() => {
    let stale = false
    setLoading(true)
    window.api.files
      .read(vault, path)
      .then((text) => {
        if (stale) return
        setOriginal(text)
        setSheet(parseCsv(text, path))
        setDirty(false)
        setError(null)
      })
      .catch((err) => {
        if (stale) return
        setSheet(null)
        setError(describeError(err))
      })
      .finally(() => {
        if (!stale) setLoading(false)
      })
    return () => {
      stale = true
    }
  }, [vault, path, nonce])

  // ── what the engine says about it
  useEffect(() => {
    if (!reportId) {
      setStatus(null)
      setStatusNote(null)
      setWarnings([])
      return
    }
    let stale = false

    void (async () => {
      const check = await jsonOf<DataFinding[]>(vault, ['data', 'check', reportId, '--json'])
      if (stale) return
      const rows = Array.isArray(check.value) ? check.value : []
      setWarnings(
        rows.filter(
          (finding) =>
            finding.path === paths.vaultRel || finding.message.includes(paths.reportRel)
        )
      )

      const asked = await jsonOf<DataStatus>(vault, [
        'data',
        'status',
        reportId,
        paths.vaultRel,
        '--json'
      ])
      if (stale) return
      if (asked.value) {
        setStatus(asked.value)
        setStatusNote(null)
        return
      }
      setStatus(null)
      setStatusNote(
        unsupported(asked.run)
          ? 'This engine has no `data status` yet, so the checksum below is read from ' +
              '`data check` instead.'
          : tail(asked.run)
      )
    })()

    return () => {
      stale = true
    }
  }, [vault, reportId, paths, engineNonce, nonce])

  const edit = useCallback(
    (next: Sheet) => {
      setSheet(next)
      // Compared against the text that was read rather than latched on the first
      // keystroke, so undoing an edit back to the original is honestly clean.
      setDirty(serialiseCsv(next) !== original)
    },
    [original]
  )

  const setHeader = useCallback((next: boolean) => {
    setSheet((current) => (current ? { ...current, header: next } : current))
  }, [])

  const reparse = useCallback(
    (delimiter: string) => {
      if (dirty) return
      setSheet(parseCsv(original, path, delimiter))
    },
    [dirty, original, path]
  )

  const save = useCallback(async () => {
    const current = latest.current
    if (!current) return
    const text = serialiseCsv(current)
    setBusy(true)
    try {
      await window.api.files.write(vault, path, text)
      setOriginal(text)
      setDirty(false)
      setError(null)
      // Saving a registered file is the moment its sha stops matching, so the
      // banner has to be re-asked rather than assumed still true.
      setEngineNonce((value) => value + 1)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }, [vault, path])

  const revise = useCallback(
    async (note?: string): Promise<ReviseSummary | null> => {
      if (!reportId) return null
      setBusy(true)
      setFailure(null)
      const args = [
        'data',
        'revise',
        reportId,
        paths.vaultRel,
        ...(note && note.trim() ? ['--note', note.trim()] : []),
        '--json'
      ]
      const { value, run } = await jsonOf<ReviseSummary>(vault, args)
      setBusy(false)
      if (!value) {
        setFailure(
          unsupported(run)
            ? 'This engine has no `data revise` yet. Re-register from a terminal with ' +
                `\`report-maker -C ${vault} data add ${reportId} ${paths.vaultRel}\` — that ` +
                'moves the checksum, though it keeps no dated copy of the version it replaces.'
            : tail(run)
        )
        return null
      }
      setLastRevision(value)
      setEngineNonce((current) => current + 1)
      return value
    },
    [vault, reportId, paths]
  )

  const register = useCallback(async (): Promise<boolean> => {
    if (!reportId) return false
    setBusy(true)
    setFailure(null)
    const run = await window.api.engine.run(vault, ['data', 'add', reportId, paths.vaultRel])
    setBusy(false)
    if (run.code !== 0) {
      setFailure(tail(run))
      return false
    }
    setEngineNonce((current) => current + 1)
    return true
  }, [vault, reportId, paths])

  const rows = useMemo(() => (sheet ? sheet.rows.map((row) => row.cells) : []), [sheet])
  const columns = useMemo(() => (sheet ? columnStats(sheet) : []), [sheet])
  const derived = useMemo(() => derivedState(warnings, paths.reportRel), [warnings, paths])

  return {
    sheet,
    rows,
    header: sheet?.header ?? false,
    setHeader,
    columns,
    dirty,
    loading,
    error,
    edit,
    reparse,
    save,
    revise,
    register,
    reload,
    status,
    statusNote,
    derived,
    warnings,
    busy,
    failure,
    lastRevision,
    paths
  }
}
