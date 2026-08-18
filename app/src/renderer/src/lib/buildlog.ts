/**
 * Typst's output, read as diagnostics.
 *
 * This is the one thing the app parses, and it is not a rule about a vault: a
 * compiler's stderr is prose meant for a human, and the only reason to read it
 * here is to turn "error: … at file:line" into something clickable. Every line a
 * rule matched is still shown verbatim under the entry it belongs to, and every
 * line no rule matched is shown verbatim as text. Nothing is dropped.
 *
 * Typst 0.15 writes codespan-style diagnostics:
 *
 *     error: unknown variable: undefinedvar
 *       ┌─ reports/x/main.typ:3:1
 *       │
 *     3 │ #undefinedvar
 *       │  ^^^^^^^^^^^^
 *
 *       while calling `f` at reports/x/main.typ:9:1
 *         f("a")
 *
 * while other tools — and older typst — write the one-line form
 * `path:line:col: error: message`. Both are parsed.
 *
 * The paths are vault-relative, because the engine compiles with the vault as the
 * working directory (`build.compile_report` passes `cwd=cfg.root`). That is the
 * same shape `check --json` reports its findings in, so one `onReveal(path, line)`
 * serves the Findings tab and the Build tab alike.
 */

import type { Run } from '../../../shared/types'

export type BuildLevel = 'error' | 'warning' | 'info'

/** One diagnostic, with the lines it was parsed from kept alongside it. */
export type BuildEntry = {
  kind: 'entry'
  level: BuildLevel
  message: string
  /** Vault-relative POSIX path, exactly as the compiler printed it. */
  path: string | null
  line: number | null
  col: number | null
  /** Every source line of this entry, head first — the panel shows the rest. */
  raw: string[]
}

/** A run of lines no rule claimed. Shown as-is, in a monospace block. */
export type BuildText = { kind: 'text'; lines: string[] }

export type BuildBlock = BuildEntry | BuildText

export type BuildLog = {
  /** Blocks in the order they were printed, so the log still reads top to bottom. */
  blocks: BuildBlock[]
  /** The entries alone, same order — a convenience view over `blocks`. */
  entries: BuildEntry[]
  errors: number
  warnings: number
}

// ── the shapes ──────────────────────────────────────────────────────────────

/** `error: unknown variable: foo` — the head of a codespan diagnostic. */
const HEAD = /^(error|warning|note|hint):[ \t]*(.*)$/

/** `  ┌─ reports/x/main.typ:3:1` — the pointer line under a head. */
const POINTER = /^\s*┌─\s*(.+?):(\d+):(\d+)\s*$/

/** `reports/x/main.typ:3:1: error: unknown variable` — the whole thing on one line. */
const INLINE = /^\s*(\S.*?):(\d+)(?::(\d+))?:\s*(error|warning|note|hint):[ \t]*(.*)$/

function levelOf(word: string): BuildLevel {
  if (word === 'error') return 'error'
  if (word === 'warning') return 'warning'
  return 'info'
}

function starts(line: string): boolean {
  return HEAD.test(line) || INLINE.test(line)
}

/** Directly under a head, anything indented or drawn with the gutter box art is
 *  part of the same diagnostic. */
function continues(line: string): boolean {
  if (line.trim() === '') return false
  if (POINTER.test(line)) return true
  if (line.includes('│')) return true
  return /^\s/.test(line)
}

/**
 * After a blank line the test has to be stricter. Typst separates a diagnostic
 * from its call trace with a blank line, but the engine's own progress lines
 * (`  → out/x.pdf`) are indented too — a loose rule would file them under
 * whichever error happened to be printed before them.
 */
function continuesAfterBlank(line: string): boolean {
  if (POINTER.test(line)) return true
  if (line.includes('│')) return true
  return /^\s+(?:while\b|hint:|help:|note:)/.test(line)
}

// ── the parse ───────────────────────────────────────────────────────────────

export function parseBuildLog(text: string): BuildLog {
  const lines = text.replace(/\r\n?/g, '\n').split('\n')
  const blocks: BuildBlock[] = []
  let plain: string[] = []

  // A run of unmatched lines only becomes a block once something interrupts it.
  // Blank edges are padding between blocks rather than output, so they go; every
  // line carrying anything at all is kept, including the blanks between them.
  const flush = (): void => {
    let start = 0
    let end = plain.length
    while (start < end && plain[start].trim() === '') start += 1
    while (end > start && plain[end - 1].trim() === '') end -= 1
    if (end > start) blocks.push({ kind: 'text', lines: plain.slice(start, end) })
    plain = []
  }

  let i = 0
  while (i < lines.length) {
    const head = HEAD.exec(lines[i])
    const inline = head ? null : INLINE.exec(lines[i])

    if (!head && !inline) {
      plain.push(lines[i])
      i += 1
      continue
    }
    flush()

    if (inline) {
      blocks.push({
        kind: 'entry',
        level: levelOf(inline[4]),
        message: inline[5],
        path: inline[1],
        line: Number(inline[2]),
        col: inline[3] ? Number(inline[3]) : null,
        raw: [lines[i]]
      })
      i += 1
      continue
    }

    const entry: BuildEntry = {
      kind: 'entry',
      level: levelOf(head![1]),
      message: head![2],
      path: null,
      line: null,
      col: null,
      raw: [lines[i]]
    }
    i += 1

    while (i < lines.length) {
      const next = lines[i]
      if (starts(next)) break

      if (next.trim() === '') {
        // Look past the blank run: the trace after it still belongs to this entry.
        let j = i
        while (j < lines.length && lines[j].trim() === '') j += 1
        if (j >= lines.length || starts(lines[j]) || !continuesAfterBlank(lines[j])) break
        for (; i < j; i += 1) entry.raw.push(lines[i])
        continue
      }

      if (!continues(next)) break
      const pointer = POINTER.exec(next)
      // The first pointer is the primary location; later ones belong to the trace.
      if (pointer && entry.path === null) {
        entry.path = pointer[1]
        entry.line = Number(pointer[2])
        entry.col = Number(pointer[3])
      }
      entry.raw.push(next)
      i += 1
    }

    while (entry.raw.length > 0 && entry.raw[entry.raw.length - 1].trim() === '') entry.raw.pop()
    blocks.push(entry)
  }
  flush()

  const entries = blocks.filter((block): block is BuildEntry => block.kind === 'entry')
  return {
    blocks,
    entries,
    errors: entries.filter((entry) => entry.level === 'error').length,
    warnings: entries.filter((entry) => entry.level === 'warning').length
  }
}

/**
 * The last engine run, parsed.
 *
 * Both streams are read, in the order the shell would have interleaved them:
 * typst's warnings reach us through the engine's own `print`, so they arrive on
 * stdout, while a failed compile is raised and printed to stderr.
 */
export function parseRun(run: Run | null | undefined): BuildLog {
  if (!run) return parseBuildLog('')
  const text = [run.stdout, run.stderr]
    .map((stream) => stream.replace(/\s+$/, ''))
    .filter((stream) => stream !== '')
    .join('\n')
  return parseBuildLog(text)
}
