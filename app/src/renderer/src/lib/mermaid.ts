/**
 * The diagram, as the build sees it.
 *
 * There is one rule in this file and everything else follows from it: **the
 * preview renders the prepared source, never the text in the editor.**
 *
 * `engine/diagrams.py` records why. mermaid writes presentation into inline
 * `style` attributes on every node, and Typst's SVG renderer honours those over
 * any rule in a stylesheet — so a diagram styled from `style.css` alone looks
 * right in a browser and arrives in the PDF unstyled. The engine's answer is to
 * generate mermaid `classDef`s from the brand pack and inject them into the
 * `.mmd` before rendering. A preview that skipped that step would be *prettier
 * than the truth*: green in the pane, wrong in the document. That is worse than
 * no preview at all, because nobody goes looking for a bug the preview says is
 * not there.
 *
 * So nothing here assembles mermaid input. `report-maker diagrams --prepare
 * <path> --json` hands back the prepared source, the generated config, the
 * generated stylesheet, the classDefs it injected and the pack they came from,
 * and this module renders exactly that. Preview and build then share their whole
 * input, and the only remaining difference between them is which mermaid ran —
 * which is why {@link divergence} exists rather than being assumed away.
 *
 * The engine prepares a *file*, so the file is what gets previewed. That is the
 * reason the editor writes the buffer before each run (see `settle` on
 * {@link usePrepared}) and the reason {@link isPreparedFrom} exists: the one
 * thing this pane must never do is show a picture of a file nobody can see.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'
import type { MermaidConfig } from 'mermaid'
import mermaidPackage from 'mermaid/package.json'
import type { Run } from '../../../shared/types'
import { describeError } from '@/lib/sources'

// ── what the engine hands back ───────────────────────────────────────────────

/** `diagrams --prepare <path> --json`, one for one with `diagrams.prepared_json`. */
export type Prepared = {
  /** The `.mmd` as mermaid should see it: the author's text plus any classDefs
   *  the engine injected. Byte-identical to what the build feeds mermaid-cli. */
  source: string
  /** Absolute path of the generated config, for the record. */
  config: string
  /** Absolute path of the generated stylesheet, for the record. */
  css: string
  /** The config's contents, so nothing has to read a file to render. */
  configJson: Record<string, unknown>
  /** The stylesheet's contents — what mermaid-cli passes as `--cssFile`. */
  cssText: string
  /** The version of `@mermaid-js/mermaid-cli` this vault has installed, or null
   *  when it has never rendered a diagram. */
  mermaidVersion: string | null
  /** Only the classDefs that were injected — empty when the source used no
   *  emphasis class, or defined them itself. */
  classDefs: Record<string, string>
  /** The brand pack the owning report's design names. */
  pack: string
}

/** The version of mermaid the *app* renders with. Read from the package rather
 *  than written down, because a number in a source file is a number that goes
 *  stale on the next `npm install` and lies from then on. */
export const APP_MERMAID: string = mermaidPackage.version

/** How long the buffer has to stop moving before the engine is asked again. */
export const DEBOUNCE = 300

export function prepareArgs(path: string): string[] {
  return ['diagrams', '--prepare', path, '--json']
}

/**
 * What one attempt to prepare came back with.
 *
 * `unsupported` is its own state rather than an error string, because the CLI
 * subcommand lands in a later integration pass and an app that crashes on an
 * engine one commit behind is an app nobody can run mid-refactor. It degrades to
 * a sentence naming the command, and the editor still edits.
 */
export type PrepareOutcome =
  | { state: 'ok'; prepared: Prepared }
  | { state: 'unsupported'; message: string; command: string }
  | { state: 'failed'; message: string; command: string }

/** argparse's vocabulary for "this build has never heard of that flag". Matched
 *  rather than guessed from the exit code, because exit 2 also means a bad
 *  target, and those two need different sentences. */
const UNKNOWN_FLAG = /unrecognized arguments?:|invalid choice:|no such option|unknown option/i

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

/**
 * The JSON, read defensively.
 *
 * `source` is the only field that cannot be defaulted: without it there is
 * nothing to render, and rendering the buffer instead is precisely the failure
 * this module exists to prevent. Everything else degrades — an older engine that
 * omits `classDefs` still previews, it just cannot draw the legend swatches.
 */
function absorb(raw: unknown): Prepared | null {
  if (!isRecord(raw) || typeof raw.source !== 'string') return null
  const version = raw.mermaidVersion
  return {
    source: raw.source,
    config: str(raw.config),
    css: str(raw.css),
    configJson: isRecord(raw.configJson) ? raw.configJson : {},
    cssText: str(raw.cssText),
    mermaidVersion: typeof version === 'string' ? version : null,
    classDefs: isRecord(raw.classDefs)
      ? Object.fromEntries(
          Object.entries(raw.classDefs).filter((entry): entry is [string, string] => typeof entry[1] === 'string')
        )
      : {},
    pack: str(raw.pack, 'default')
  }
}

/** Ask the engine to prepare one `.mmd`. Never throws: every failure is a state. */
export async function prepare(vault: string, path: string): Promise<PrepareOutcome> {
  const args = prepareArgs(path)
  let run: Run
  try {
    run = await window.api.engine.run(vault, args)
  } catch (err) {
    return { state: 'failed', message: describeError(err), command: `report-maker ${args.join(' ')}` }
  }

  const said = (run.stderr || run.stdout).trim()
  if (run.code !== 0) {
    const unsupported = UNKNOWN_FLAG.test(said)
    return {
      state: unsupported ? 'unsupported' : 'failed',
      message: said || `exit ${run.code}`,
      command: run.command
    }
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(run.stdout)
  } catch {
    // A zero exit with output that is not JSON is an engine that took `--prepare`
    // as a target and rendered something. Treat it as not supported yet, since
    // that is what it is, and say what it printed.
    return {
      state: 'unsupported',
      message: run.stdout.trim() || 'the command printed nothing that parses as JSON',
      command: run.command
    }
  }

  const prepared = absorb(parsed)
  if (!prepared) {
    return {
      state: 'failed',
      message: 'the engine answered without a prepared source, so there is nothing safe to render',
      command: run.command
    }
  }
  return { state: 'ok', prepared }
}

/**
 * Is this prepared source the one for the text on screen?
 *
 * The engine appends its classDefs to the end of the file and touches nothing
 * else, so the author's own text is a prefix of what came back. Recognising that
 * shape is not re-implementing the injection — it never *produces* a prepared
 * source, it only answers whether the one we were handed still describes the
 * buffer. A false answer here is the difference between a stale preview that
 * says so and one that quietly lies.
 */
export function isPreparedFrom(prepared: Prepared, text: string): boolean {
  if (prepared.source === text) return true
  const body = text.replace(/\n+$/, '')
  return prepared.source.startsWith(`${body}\n\n%% classDefs`)
}

// ── which mermaid ────────────────────────────────────────────────────────────

export type Divergence = {
  /** `warn` when the two could genuinely draw different pictures. */
  tone: 'ok' | 'note' | 'warn'
  text: string
}

/** The leading integer of a version or a range — `^11.4.2` → `11`. */
function major(version: string): number | null {
  const found = /(\d+)/.exec(version)
  return found ? Number(found[1]) : null
}

/**
 * What the preview renders with, against what the vault builds with.
 *
 * They are two different packages: the app carries `mermaid`, the vault installs
 * `@mermaid-js/mermaid-cli`, which wraps a mermaid of its own. So equality is not
 * the test and never will be — the major is, because mermaid-cli's major tracks
 * the mermaid it bundles. A known divergence stated out loud is honest; an
 * unknown one is a trap, and that is the whole reason this function is not a
 * silent `if`.
 *
 * Loading the vault's copy instead is deliberately not on the table: it is a
 * node_modules tree inside somebody's documents folder, and the app does not run
 * code out of a vault.
 */
export function divergence(vaultVersion: string | null): Divergence {
  if (!vaultVersion) {
    return {
      tone: 'note',
      text:
        `Previewing with mermaid ${APP_MERMAID}. This vault has never rendered a diagram, ` +
        'so nothing pins its mermaid yet — the first render installs mermaid-cli and decides it.'
    }
  }

  const ours = major(APP_MERMAID)
  const theirs = major(vaultVersion)
  if (ours !== null && theirs !== null && ours !== theirs) {
    return {
      tone: 'warn',
      text:
        `Previewing with mermaid ${APP_MERMAID}; this vault builds with mermaid-cli ${vaultVersion}. ` +
        'Different majors draw different diagrams — trust the built SVG over this pane.'
    }
  }
  return {
    tone: 'ok',
    text: `mermaid ${APP_MERMAID} here · mermaid-cli ${vaultVersion} in the vault`
  }
}

// ── rendering ────────────────────────────────────────────────────────────────

let sequence = 0

/**
 * One prepared diagram, drawn.
 *
 * The config and the stylesheet arrive with the source and are applied as they
 * are: `--cssFile` is `themeCSS` by another name, which is where mermaid-cli
 * puts it, so passing it anywhere else would be inventing a second way to style
 * a diagram. The only keys added are the ones a browser needs and a CLI does not.
 *
 * `htmlLabels` is not forced off here even though the whole pipeline depends on
 * it: the engine refuses to answer at all when the config it is about to hand
 * over has them on, so a config that reaches this function has already been
 * checked by the half that also checks the build.
 */
export async function renderSvg(prepared: Prepared): Promise<string> {
  const id = `rm-mermaid-${(sequence += 1)}`
  const config = prepared.configJson as unknown as MermaidConfig
  const themeCSS = [str(prepared.configJson.themeCSS), prepared.cssText].filter(Boolean).join('\n')

  mermaid.initialize({ ...config, themeCSS, startOnLoad: false })
  try {
    const { svg } = await mermaid.render(id, prepared.source)
    return svg
  } finally {
    // mermaid parks a scratch element on the body while it measures. It usually
    // takes it away again; on a throw it sometimes does not, and one left behind
    // per keystroke is a leak the author would see as a slowly dying window.
    document.getElementById(`d${id}`)?.remove()
    document.getElementById(id)?.remove()
  }
}

export type MermaidFailure = {
  /** mermaid's own words. */
  message: string
  /** 1-based line in the *author's* file, or null when it cannot be placed. */
  line: number | null
  /** False when the failure landed in the generated classDef block rather than
   *  in anything a person wrote — which is a bug in this tool, not in the file. */
  authored: boolean
}

function lineOf(err: unknown): number | null {
  if (isRecord(err)) {
    const hash = err.hash
    if (isRecord(hash) && isRecord(hash.loc) && typeof hash.loc.first_line === 'number') {
      return hash.loc.first_line
    }
  }
  const message = err instanceof Error ? err.message : String(err)
  const found = /on line (\d+)/i.exec(message)
  return found ? Number(found[1]) : null
}

/**
 * A thrown mermaid error, turned into something to print next to the line it is
 * about. mermaid throws — it does not return — and a pane that lets the throw
 * escape goes blank, which tells the author their diagram vanished rather than
 * that they typed one bracket too few.
 *
 * Line numbers survive the translation because the engine *appends* its
 * classDefs: everything the author wrote keeps its number, and anything reported
 * past the end of their text came from the generated block. Without `text` there
 * is nothing to measure that against, so the failure is attributed to the author
 * — the reading that sends someone to look at their own file first.
 */
export function readFailure(err: unknown, prepared: Prepared, text?: string): MermaidFailure {
  const raw = err instanceof Error ? err.message : String(err)
  const line = lineOf(err)
  const authored = line === null || line <= (text ?? prepared.source).split('\n').length
  return { message: raw.trim() || 'mermaid could not render this diagram.', line, authored }
}

// ── the four emphasis classes ────────────────────────────────────────────────

/**
 * The whole vocabulary a `.mmd` may use for colour.
 *
 * A hex code in a diagram is the thing the brand pack exists to prevent: it
 * looks right once and then drifts the first time a colour moves, silently, in
 * one file out of forty. So the legend is not decoration — it is how the tool
 * teaches the rule at the moment somebody is reaching for a colour.
 */
export const EMPHASIS: readonly { name: string; means: string }[] = [
  { name: 'em-accent', means: 'the thing the diagram is about' },
  { name: 'em-muted', means: 'context — present, not the point' },
  { name: 'em-good', means: 'the desired state, or the outcome' },
  { name: 'em-ghost', means: 'absent, proposed, or out of scope' }
]

/** The colours out of one injected classDef, for a swatch in the app's own
 *  chrome. Undefined when the class is not in use — the engine only injects
 *  what the source references, so there is nothing to show yet. */
export function swatch(style: string | undefined): { fill?: string; stroke?: string; dashed: boolean } {
  if (!style) return { dashed: false }
  const pick = (key: string): string | undefined => {
    const found = new RegExp(`(?:^|,)\\s*${key}:([^,]+)`).exec(style)
    return found ? found[1].trim() : undefined
  }
  return { fill: pick('fill'), stroke: pick('stroke'), dashed: /stroke-dasharray/.test(style) }
}

/** The `:::class` form, which is what a node carries. `class A em-accent` on its
 *  own line is equally valid and the engine recognises both; one of them has to
 *  be the one a button types. */
export function classSyntax(name: string): string {
  return `:::${name}`
}

// ── rendering for real ───────────────────────────────────────────────────────

export type Wrote = { path: string; status: string }

/** The `→ …svg (rendered)` lines `report-maker diagrams` prints. Read back
 *  rather than assumed, because "up to date" and "rendered" are different
 *  answers and a button that claims to have written a file it did not is worse
 *  than one that says nothing. */
export function wrote(run: Run): Wrote[] {
  const out: Wrote[] = []
  for (const line of `${run.stdout}\n${run.stderr}`.split('\n')) {
    const found = /→\s*(\S+\.svg)\s*(?:\(([^)]*)\))?/.exec(line)
    if (found) out.push({ path: found[1], status: (found[2] ?? '').trim() || 'written' })
  }
  return out
}

// ── the first diagram ────────────────────────────────────────────────────────

/**
 * What a new `.mmd` starts as.
 *
 * A copy of the base design's starter in spirit, not by reference: the engine's
 * starters are copied by `report-maker new`, and there is no command that adds
 * one to a report that already exists, so this text has to live somewhere on
 * this side. It is deliberately the smallest thing that demonstrates the rule —
 * a shape, an emphasis class, and the comment saying where colour comes from.
 */
export const SEED = `%% Colour comes from the brand pack, never from this file. The only accent
%% classes a diagram may use are em-accent, em-muted, em-good, em-ghost.
flowchart LR
  source[Source material] --> read[Read and extract]
  read --> fact["Cited fact"]
  read --> call["Marked assessment"]
  fact --> report[Report]
  call --> report

  class fact em-accent
  class call em-muted
  class report em-good
`

/** A file name from what somebody typed, with the extension the engine looks for.
 *  Anything that would climb out of `diagrams/` is flattened rather than
 *  rejected: the field is for a name, so a slash in it is a typo, not a path. */
export function diagramFileName(typed: string): string {
  const cleaned = typed
    .trim()
    .replace(/\.mmd$/i, '')
    .replace(/[^A-Za-z0-9._-]+/g, '-')
    .replace(/^[-.]+|[-.]+$/g, '')
    .toLowerCase()
  return `${cleaned || 'diagram'}.mmd`
}

/** Where a report's diagrams live, as an absolute path. `diagrams/` beside
 *  `main.typ` is the engine's own layout — `Report.diagrams` — not a convention
 *  chosen here. */
export function diagramPath(vault: string, reportId: string, fileName: string): string {
  return `${vault}/reports/${reportId}/diagrams/${fileName}`
}

// ── the hook ─────────────────────────────────────────────────────────────────

export type UsePrepared = {
  outcome: PrepareOutcome | null
  /** A run is in flight. The last good answer stays on screen underneath it. */
  running: boolean
  /** Ask again now, ignoring the debounce. */
  refresh: () => void
}

/**
 * The prepared source, kept in step with the buffer.
 *
 * `text` is a *trigger*, not an input: the engine prepares a file, and this hook
 * never sends it one. Passing the buffer in is how the hook knows the author has
 * stopped typing; `settle` is how the file catches up before the engine reads
 * it. Skipping `settle` would give a preview of the last save — a picture of a
 * file nobody can see, which is the failure mode this pane is built to avoid.
 *
 * Runs coalesce rather than queue. Preparing spawns a process; ten keystrokes
 * during one run must cost one more run, not ten.
 */
export function usePrepared(
  vault: string | null,
  path: string | null,
  text: string,
  settle?: () => void | Promise<void>
): UsePrepared {
  const [outcome, setOutcome] = useState<PrepareOutcome | null>(null)
  const [running, setRunning] = useState(false)
  const [nonce, setNonce] = useState(0)

  const alive = useRef(true)
  const busy = useRef(false)
  const queued = useRef(false)
  const wanted = useRef({ vault, path })
  // The view outlives any one render, so the callback it eventually calls must
  // be read at call time rather than captured.
  const latest = useRef(settle)
  latest.current = settle

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  // Declared before the run effect so it lands first: a run started for the file
  // we are leaving must know its answer is no longer wanted.
  useEffect(() => {
    wanted.current = { vault, path }
    setOutcome(null)
  }, [vault, path])

  const drain = useCallback(async (root: string, file: string) => {
    if (busy.current) {
      queued.current = true
      return
    }
    busy.current = true
    setRunning(true)
    try {
      for (;;) {
        queued.current = false
        await latest.current?.()
        const result = await prepare(root, file)
        if (!alive.current || wanted.current.vault !== root || wanted.current.path !== file) return
        setOutcome(result)
        if (!queued.current) break
      }
    } finally {
      busy.current = false
      if (alive.current) setRunning(false)
    }
  }, [])

  useEffect(() => {
    if (!vault || !path) return
    const timer = window.setTimeout(() => void drain(vault, path), DEBOUNCE)
    return () => window.clearTimeout(timer)
    // `text` is the trigger and is deliberately not read inside: the engine reads
    // the file, and `settle` is what puts the buffer there.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vault, path, text, nonce, drain])

  const refresh = useCallback(() => setNonce((current) => current + 1), [])

  return { outcome, running, refresh }
}
