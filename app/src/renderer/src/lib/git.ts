/**
 * Git, as the engine reports it.
 *
 * The renderer never runs `git`. It cannot: the safety rules that make an
 * automatic commit tolerable — never force-push, never push behind, never push
 * from a detached HEAD, only ever stage paths inside the vault — live in
 * `engine/gitsync.py`, and a second caller reaching around them is exactly the
 * bug those rules exist to prevent. So every question here is `report-maker
 * sync` in one form or another, and the answers arrive as the engine's own JSON.
 *
 * That leaves this module with three jobs, none of which is knowing anything
 * about git:
 *
 * — **Ask, and hold the answer.** `useGitState` and `useLog` are the loading
 *   halves of the timeline, keyed on a `tick` the shell bumps after a build or a
 *   commit.
 * — **Degrade in words.** These commands are newer than the app. A build of the
 *   engine without `sync`, or without `--log`, answers with argparse's own
 *   complaint and exit 2; that is told apart from a real failure here
 *   (`Trouble.kind`) so the screen can say which of the two happened instead of
 *   showing an empty list either way.
 * — **Read a diff for a reader.** `wordDiff` is the one computation in this file,
 *   and it is presentation: which words of a reworded sentence actually moved.
 *   Nothing about a vault is decided here.
 */

import { useCallback, useEffect, useState } from 'react'
import type { Change, GitLogEntry, GitState, ReportDiff, Run } from '../../../shared/types'
import { describeError, sourcesPath } from '@/lib/sources'

// ── What went wrong ──────────────────────────────────────────────────────────

/**
 * argparse's vocabulary for "I have never heard of that".
 *
 * The engine and the app ship separately, and `sync --log` is newer than both.
 * Matching the words argparse prints is how an app that is ahead of its engine
 * can say so, rather than reporting "no commits" for a command that was never
 * run. Paired with exit 2, which is argparse's own code and not one the engine
 * returns for a vault problem.
 */
const NOT_A_COMMAND = /invalid choice|unrecognized arguments?|no such option|unknown command/i

export type Trouble = {
  /** `unsupported` — this build of the engine has no such command or flag. */
  kind: 'unsupported' | 'failed'
  /** The engine's own words. Shown verbatim; it is the part worth reading. */
  message: string
  /** What was run, so the message can be reproduced in a terminal. */
  command: string
}

function troubleFromRun(result: Run, args: string[]): Trouble {
  const message = (result.stderr || result.stdout).trim() || `exited ${result.code}`
  return {
    kind: result.code === 2 && NOT_A_COMMAND.test(message) ? 'unsupported' : 'failed',
    message,
    command: `report-maker ${args.join(' ')}`
  }
}

function troubleFromError(err: unknown, command: string): Trouble {
  const message = describeError(err)
  return { kind: NOT_A_COMMAND.test(message) ? 'unsupported' : 'failed', message, command }
}

/** True when a run failed only because this engine is older than this app. */
export function isUnsupported(result: Run): boolean {
  return troubleFromRun(result, []).kind === 'unsupported'
}

// ── Reading the engine's JSON ────────────────────────────────────────────────
//
// The contract pins the shape of a row — GitLogEntry, ReportDiff — but not the
// envelope the command prints them in, and `diff --json` in fact wraps its rows
// in `{rev, count, counts, diffs: [...]}`. Unwrapping tolerantly costs a dozen
// lines here and saves the timeline from going blank the day a command grows a
// summary field. Nothing below invents a value: a field the engine did not print
// reads as empty, never as a guess.

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function rowsOf(payload: unknown, keys: string[]): Record<string, unknown>[] {
  const raw = Array.isArray(payload)
    ? payload
    : isRecord(payload)
      ? (keys.map((key) => payload[key]).find(Array.isArray) ?? [])
      : []
  return (raw as unknown[]).filter(isRecord)
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function toLogEntry(row: Record<string, unknown>): GitLogEntry {
  const sha = text(row.sha)
  return {
    sha: sha || text(row.short),
    short: text(row.short) || sha.slice(0, 7),
    subject: text(row.subject),
    author: text(row.author),
    date: text(row.date)
  }
}

function toChange(row: Record<string, unknown>): Change {
  return {
    kind: text(row.kind),
    key: text(row.key),
    before: typeof row.before === 'string' ? row.before : null,
    after: typeof row.after === 'string' ? row.after : null,
    line: typeof row.line === 'number' ? row.line : null
  }
}

function pickDiff(payload: unknown, reportId: string): ReportDiff | null {
  const rows = rowsOf(payload, ['diffs', 'reports'])
  // `diff` takes a target, so a folder prefix can legitimately return several
  // reports; the timeline is looking at one of them.
  const found = rows.find((row) => row.id === reportId) ?? rows[0]
  if (!found) return null
  return {
    id: text(found.id) || reportId,
    rev: text(found.rev),
    changes: Array.isArray(found.changes)
      ? (found.changes as unknown[]).filter(isRecord).map(toChange)
      : [],
    counts: isRecord(found.counts) ? (found.counts as ReportDiff['counts']) : {}
  }
}

// ── The repository ───────────────────────────────────────────────────────────

export type UseGitState = {
  /** null while loading, and when the command could not be run at all. */
  state: GitState | null
  loading: boolean
  trouble: Trouble | null
  reload: () => void
}

/**
 * Whether the vault is a repository, and what state it is in.
 *
 * `tick` is the shell's "something happened" counter — a build, a commit, a
 * vault switch. Re-asking is one spawn, which is cheap enough that caching it
 * would only buy a chance to show a branch that has since moved.
 */
export function useGitState(vault: string | null, tick = 0): UseGitState {
  const [state, setState] = useState<GitState | null>(null)
  const [loading, setLoading] = useState(Boolean(vault))
  const [trouble, setTrouble] = useState<Trouble | null>(null)
  const [nonce, setNonce] = useState(0)

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    if (!vault) {
      setState(null)
      setTrouble(null)
      setLoading(false)
      return
    }
    let stale = false
    setLoading(true)
    window.api.git
      .state(vault)
      .then((next) => {
        if (stale) return
        setState(next)
        setTrouble(null)
      })
      .catch((err) => {
        if (stale) return
        setState(null)
        setTrouble(troubleFromError(err, 'report-maker sync --status --json'))
      })
      .finally(() => {
        if (!stale) setLoading(false)
      })
    return () => {
      stale = true
    }
  }, [vault, tick, nonce])

  return { state, loading, trouble, reload }
}

/**
 * Make the vault a repository.
 *
 * One command, and the app does not run it: `report-maker sync --init` is the
 * engine's verb for `git init`, and if this engine has no such verb the caller
 * says so and tells the writer what to type. Spawning `git` from the renderer to
 * paper over that would put a second author of vault history in the app, which
 * is the whole thing this file refuses to be.
 */
export function initRepo(vault: string): Promise<Run> {
  return window.api.engine.run(vault, ['sync', '--init'])
}

// ── The commits touching one report ──────────────────────────────────────────

export type UseLog = {
  log: GitLogEntry[]
  loading: boolean
  trouble: Trouble | null
  reload: () => void
}

/** A path that names a file rather than a folder. */
const HAS_EXTENSION = /\.[a-z0-9]+$/i

async function logOnce(vault: string, path: string): Promise<{ log: GitLogEntry[]; trouble: Trouble | null }> {
  const args = ['sync', '--log', path, '--json']
  const result = await window.api.engine.run(vault, args)
  if (result.code !== 0) return { log: [], trouble: troubleFromRun(result, args) }
  try {
    return { log: rowsOf(JSON.parse(result.stdout), ['log', 'commits', 'entries']).map(toLogEntry), trouble: null }
  } catch {
    return {
      log: [],
      trouble: {
        kind: 'failed',
        message: `sync --log printed something that is not JSON:\n${result.stdout.trim()}`,
        command: `report-maker ${args.join(' ')}`
      }
    }
  }
}

/**
 * Every commit that touched `path`, newest first.
 *
 * `path` is normally a report folder, because a commit that only rewrote
 * `sources.yml` is part of that report's history too. `git log -- <dir>` takes a
 * folder happily, but `log(cfg, path, limit)` is specified over a path and may
 * yet insist on a file, so a folder that is refused is retried as its `main.typ`
 * rather than reported as no history at all. One extra spawn, only on failure.
 */
export async function loadLog(
  vault: string,
  path: string
): Promise<{ log: GitLogEntry[]; trouble: Trouble | null }> {
  const first = await logOnce(vault, path)
  if (!first.trouble || HAS_EXTENSION.test(path)) return first
  const retry = await logOnce(vault, `${path.replace(/\/$/, '')}/main.typ`)
  return retry.trouble ? first : retry
}

export function useLog(vault: string | null, path: string | null, tick = 0): UseLog {
  const [log, setLog] = useState<GitLogEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [trouble, setTrouble] = useState<Trouble | null>(null)
  const [nonce, setNonce] = useState(0)

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    if (!vault || !path) {
      setLog([])
      setTrouble(null)
      setLoading(false)
      return
    }
    let stale = false
    setLoading(true)
    loadLog(vault, path)
      .then((outcome) => {
        if (stale) return
        setLog(outcome.log)
        setTrouble(outcome.trouble)
      })
      .catch((err) => {
        if (stale) return
        setLog([])
        setTrouble(troubleFromError(err, `report-maker sync --log ${path} --json`))
      })
      .finally(() => {
        if (!stale) setLoading(false)
      })
    return () => {
      stale = true
    }
  }, [vault, path, tick, nonce])

  return { log, loading, trouble, reload }
}

// ── The change list ──────────────────────────────────────────────────────────

/**
 * How this engine's `diff` takes a revision, settled on the first call.
 *
 * A13 fixes the verb and A7 fixes the function — `diff(cfg, target, rev)` — but
 * not how the CLI spells the revision, and a timeline that is permanently blank
 * because the flag turned out to be positional is a bad way to find that out. So
 * the first comparison of a session asks in both spellings, keeps the one the
 * engine answered to, and never probes again.
 *
 * The same memo records whether `diff` accepts a *second* revision. It very
 * likely does not: A7 compares a revision against the working tree, which is
 * "what changed since this commit" but not "compare these two commits". Asking
 * once is what tells the screen whether to show the comparison it was asked for
 * or to say plainly which one it is showing instead.
 */
type RevStyle = 'flag' | 'positional'

let revStyle: RevStyle | null = null
let twoRevisionDiff: boolean | null = null
/** Set when argparse rejected the subcommand itself, so no spelling can help. */
let noDiffVerb: Trouble | null = null

function diffArgs(reportId: string, from: string, to: string | null, style: RevStyle): string[] {
  return style === 'positional'
    ? ['diff', reportId, from, ...(to ? [to] : []), '--json']
    : ['diff', reportId, '--rev', from, ...(to ? ['--to', to] : []), '--json']
}

/** Said when two commits were picked and only one could be used. */
function degradedNote(from: string, to: string): string {
  return (
    `This engine compares a revision against the working tree, so ${to.slice(0, 7)} ` +
    `could not be the right-hand side. Showing ${from.slice(0, 7)} against the working tree.`
  )
}

async function diffOnce(
  vault: string,
  reportId: string,
  args: string[]
): Promise<{ diff: ReportDiff | null; trouble: Trouble | null }> {
  const result = await window.api.engine.run(vault, args)
  if (result.code !== 0) return { diff: null, trouble: troubleFromRun(result, args) }
  try {
    return { diff: pickDiff(JSON.parse(result.stdout), reportId), trouble: null }
  } catch {
    return {
      diff: null,
      trouble: {
        kind: 'failed',
        message: `diff --json printed something that is not JSON:\n${result.stdout.trim()}`,
        command: `report-maker ${args.join(' ')}`
      }
    }
  }
}

export type DiffOutcome = {
  diff: ReportDiff | null
  trouble: Trouble | null
  /** Set when the comparison shown is not the one that was asked for. */
  note: string | null
}

/**
 * The comparison, in the first spelling this engine understands.
 *
 * Two-revision forms are tried before one-revision ones, so a capable engine
 * answers the question actually asked; within each, a spelling already known to
 * work is the only one tried. Anything but "I have never heard of that" ends the
 * search — a bad revision or a report that did not exist yet is a real answer
 * and belongs on screen, not behind three more spawns.
 */
export async function loadDiff(
  vault: string,
  reportId: string,
  from: string,
  to: string | null
): Promise<DiffOutcome> {
  if (noDiffVerb) return { diff: null, trouble: noDiffVerb, note: null }

  const styles: RevStyle[] = revStyle ? [revStyle] : ['flag', 'positional']
  const plan: { style: RevStyle; withTo: boolean }[] = [
    ...(to && twoRevisionDiff !== false ? styles.map((style) => ({ style, withTo: true })) : []),
    ...styles.map((style) => ({ style, withTo: false }))
  ]

  let last: Trouble | null = null
  for (const step of plan) {
    const attempt = await diffOnce(
      vault,
      reportId,
      diffArgs(reportId, from, step.withTo ? to : null, step.style)
    )
    if (attempt.trouble?.kind === 'unsupported') {
      last = attempt.trouble
      if (step.withTo) twoRevisionDiff = false
      // "invalid choice" is argparse rejecting the subcommand itself; no
      // spelling of its arguments will help, so stop probing this session.
      if (/invalid choice/i.test(attempt.trouble.message)) {
        noDiffVerb = attempt.trouble
        break
      }
      continue
    }
    revStyle = step.style
    if (step.withTo) twoRevisionDiff = true
    return { ...attempt, note: to && !step.withTo ? degradedNote(from, to) : null }
  }

  return { diff: null, trouble: last, note: null }
}

export type UseDiff = DiffOutcome & { loading: boolean }

/**
 * What changed between `from` and `to`, in the report's own terms.
 *
 * `to === null` means the working tree — the uncommitted file on disk — which is
 * both the engine's own default and the question a writer asks most: what have I
 * done since I last committed.
 */
export function useDiff(
  vault: string | null,
  reportId: string | null,
  from: string | null,
  to: string | null,
  tick = 0
): UseDiff {
  const [outcome, setOutcome] = useState<DiffOutcome>({ diff: null, trouble: null, note: null })
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!vault || !reportId || !from) {
      setOutcome({ diff: null, trouble: null, note: null })
      setLoading(false)
      return
    }
    let stale = false
    setLoading(true)
    loadDiff(vault, reportId, from, to)
      .then((next) => !stale && setOutcome(next))
      .catch(
        (err) =>
          !stale &&
          setOutcome({
            diff: null,
            trouble: troubleFromError(err, `report-maker diff ${reportId} --rev ${from} --json`),
            note: null
          })
      )
      .finally(() => {
        if (!stale) setLoading(false)
      })
    return () => {
      stale = true
    }
  }, [vault, reportId, from, to, tick])

  return { ...outcome, loading }
}

// ── Grouping a change list ───────────────────────────────────────────────────
//
// The buckets and their order are the engine's, mirrored from the keys of
// `ReportDiff.counts` so a section heading and the count printed beside it can
// never disagree: what the document claims to be, then the evidence, then what
// rests on it.

const BUCKETS: Record<string, string> = {
  meta: 'metadata',
  source: 'sources',
  claim: 'claims',
  assessment: 'assessments',
  figure: 'figures'
}

const BUCKET_ORDER = ['metadata', 'sources', 'claims', 'assessments', 'figures']

/** `claim-changed` → `claims`. An unknown prefix keeps its own name rather than
 *  being dropped, so a kind added to the engine still shows up here. */
export function bucketOf(kind: string): string {
  const cut = kind.lastIndexOf('-')
  const prefix = cut === -1 ? kind : kind.slice(0, cut)
  return BUCKETS[prefix] ?? prefix
}

/** `claim-changed` → `changed`. */
export function actionOf(kind: string): string {
  const cut = kind.lastIndexOf('-')
  return cut === -1 ? kind : kind.slice(cut + 1)
}

export type ChangeGroup = { bucket: string; changes: Change[] }

export function groupChanges(changes: readonly Change[]): ChangeGroup[] {
  const groups = new Map<string, Change[]>()
  for (const change of changes) {
    const bucket = bucketOf(change.kind)
    const found = groups.get(bucket)
    if (found) found.push(change)
    else groups.set(bucket, [change])
  }
  return [...groups.entries()]
    .map(([bucket, rows]) => ({ bucket, changes: rows }))
    .sort((a, b) => {
      const ai = BUCKET_ORDER.indexOf(a.bucket)
      const bi = BUCKET_ORDER.indexOf(b.bucket)
      return (ai === -1 ? BUCKET_ORDER.length : ai) - (bi === -1 ? BUCKET_ORDER.length : bi)
    })
}

/** "2 added · 1 changed", from the engine's own totals when it printed them. */
export function summarise(counts: Record<string, number> | undefined): string {
  if (!counts) return ''
  return Object.entries(counts)
    .filter(([, n]) => n > 0)
    .map(([action, n]) => `${n} ${action}`)
    .join(' · ')
}

/**
 * Which file a change sits in.
 *
 * `engine/diffing.py` numbers source changes against `sources.yml` and every
 * other kind against `main.typ`; a click on a line number has to open the right
 * one of the two. The report's own layout is `lib/sources.ts`'s to state.
 */
export function fileOf(reportId: string, kind: string): string {
  return bucketOf(kind) === 'sources' ? sourcesPath(reportId) : `reports/${reportId}/main.typ`
}

// ── Whether a revision's pages are on disk ───────────────────────────────────

/**
 * Whether `out/pages/<id>/` shows this revision.
 *
 * A vault stores one build, not one per commit: `out/` mirrors the working tree
 * and nothing archives it per revision. So the page images can be attributed to
 * exactly one commit — the newest one touching the report, and only while no
 * file of that report is modified. Every other revision has no built pages, and
 * the timeline says so rather than showing the current build under an old sha,
 * which is the one failure mode a side-by-side comparison must not have.
 */
export function pagesShow(
  rev: string,
  log: readonly GitLogEntry[],
  state: GitState | null,
  reportId: string
): boolean {
  const tip = log[0]
  if (!tip || (tip.sha !== rev && tip.short !== rev)) return false
  return !reportIsDirty(state, reportId)
}

/** Whether the working copy of this report differs from HEAD. */
export function reportIsDirty(state: GitState | null, reportId: string): boolean {
  const inside = `reports/${reportId}/`
  // `includes` rather than `startsWith`: porcelain lines may arrive with their
  // status letters or a rename arrow attached, and a false "clean" would be a
  // page image shown under the wrong sha.
  return (state?.dirty ?? []).some((path) => path.includes(inside))
}

// ── Dates ────────────────────────────────────────────────────────────────────

const RELATIVE = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

const STEPS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['second', 60],
  ['minute', 60],
  ['hour', 24],
  ['day', 7],
  ['week', 4.348],
  ['month', 12],
  ['year', Number.POSITIVE_INFINITY]
]

/** "3 days ago". An unparseable date is returned as the engine printed it — a
 *  timeline that invents a date is worse than one that quotes a strange one. */
export function relativeDate(iso: string): string {
  const at = Date.parse(iso)
  if (Number.isNaN(at)) return iso
  let delta = (at - Date.now()) / 1000
  for (const [unit, span] of STEPS) {
    if (Math.abs(delta) < span) return RELATIVE.format(Math.round(delta), unit)
    delta /= span
  }
  return iso
}

/** The full date, for the title attribute of a relative one. */
export function exactDate(iso: string): string {
  const at = Date.parse(iso)
  return Number.isNaN(at) ? iso : new Date(at).toLocaleString()
}

// ── The word diff ────────────────────────────────────────────────────────────
//
// The only computation in this file, and it is presentation: "the sentence
// changed" is what the engine says, "these four words changed" is what a reader
// needs to see. It is deliberately not asked of the engine — a word diff is a
// property of how the two strings are being displayed, not a fact about the
// vault.

/** A run of text that is either shared with the other side or not. */
export type Piece = { text: string; changed: boolean }

export type WordDiff = { before: Piece[]; after: Piece[] }

/**
 * Above this many tokens the quadratic table stops being free, and a sentence
 * that long is not being read word by word anyway. A claim is one sentence; this
 * ceiling is reached only by something that is not one.
 */
const MAX_TOKENS = 600

/** Words and the whitespace between them, kept as tokens so the rendered text
 *  keeps the spacing the writer typed. */
function tokenise(value: string): string[] {
  return value.match(/\s+|\S+/g) ?? []
}

function isSpace(token: string): boolean {
  return /^\s+$/.test(token)
}

/**
 * Whitespace inherits emphasis rather than earning it. A lone highlighted space
 * between two unchanged words is noise, and a gap between two changed words that
 * is *not* highlighted breaks the run in half.
 */
function smooth(tokens: string[], changed: boolean[]): void {
  for (let i = 0; i < tokens.length; i++) {
    if (!isSpace(tokens[i])) continue
    const before = i > 0 ? changed[i - 1] : false
    const after = i < tokens.length - 1 ? changed[i + 1] : false
    changed[i] = before && after
  }
}

function coalesce(tokens: string[], changed: boolean[]): Piece[] {
  const pieces: Piece[] = []
  for (let i = 0; i < tokens.length; i++) {
    const last = pieces[pieces.length - 1]
    if (last && last.changed === changed[i]) last.text += tokens[i]
    else pieces.push({ text: tokens[i], changed: changed[i] })
  }
  return pieces
}

/**
 * Which words differ, by longest common subsequence over tokens.
 *
 * Exact comparison, deliberately: a changed comma or a changed case *is* a
 * change to a sentence somebody is being asked to stand behind, and smoothing it
 * away here would hide it from the only screen that shows it.
 */
export function wordDiff(before: string, after: string): WordDiff {
  const a = tokenise(before)
  const b = tokenise(after)
  if (a.length > MAX_TOKENS || b.length > MAX_TOKENS) {
    return { before: [{ text: before, changed: true }], after: [{ text: after, changed: true }] }
  }

  const width = b.length + 1
  const table = new Uint16Array((a.length + 1) * width)
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      table[i * width + j] =
        a[i] === b[j]
          ? table[(i + 1) * width + (j + 1)] + 1
          : Math.max(table[(i + 1) * width + j], table[i * width + (j + 1)])
    }
  }

  const changedA = new Array<boolean>(a.length).fill(true)
  const changedB = new Array<boolean>(b.length).fill(true)
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      changedA[i] = false
      changedB[j] = false
      i++
      j++
    } else if (table[(i + 1) * width + j] >= table[i * width + (j + 1)]) {
      i++
    } else {
      j++
    }
  }

  smooth(a, changedA)
  smooth(b, changedB)
  return { before: coalesce(a, changedA), after: coalesce(b, changedB) }
}
