/**
 * The small, non-vault half of the Evidence tab.
 *
 * Nothing here evaluates the citation rule, parses a report, counts a finding or
 * decides what a code means for *this* report — `check --json` already answered
 * all of that and the answers arrive over `/api`. What lives here is four things
 * that are properties of a browser rather than facts about a vault:
 *
 *   1. `RULES` — one sentence per check code saying what that rule protects.
 *      Prose, written once, keyed by the code the engine printed. It never
 *      decides whether a code fired; it explains one that did.
 *   2. Two tiny buses, `reveal` and `insert`, so a finding can put the cursor on
 *      a line in the Write tab and a freshly minted `@key` can land at it. The
 *      Evidence pane and the editor are separate files with no parent between
 *      them, and a bus is smaller than lifting editor state into the app.
 *   3. Clipboard, with the fallback that a page served over plain HTTP needs.
 *   4. Display formatting — a date, a percentage, a safe href.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { Finding } from '@/lib/api'

// ── what each rule protects ──────────────────────────────────────────────────
//
// A person meeting E012 for the first time is being refused by a tool they have
// owned for four minutes, and the message tells them *what* is wrong without
// telling them *why anyone would care*. These sentences are the why. They are
// deliberately about the failure the rule prevents, not about the syntax that
// satisfies it — the message already carries the syntax.

export const RULES: Record<string, string> = {
  E001:
    'The design was never handed a sources.yml, so no @key in this report can ' +
    'resolve to anything. Nothing here can be cited at all until it is.',
  E002:
    'A bare image() carries no source. srcimage(…) makes a picture name where ' +
    'it came from, exactly as any other piece of evidence has to.',
  E003:
    'A bare figure() sits outside the source contract. srcfig(…) makes a figure ' +
    'either carry a source or say plainly that it is an assessment.',
  E004:
    'A figure or a quotation with no source: is a claim with nothing behind it — ' +
    'the exact gap between a fact about the world and an opinion.',
  E006:
    'A @key that sources.yml does not define looks like a citation and resolves ' +
    'to nothing. It is the most convincing way a report can be wrong.',
  E007:
    'A .mmd with no rendered .svg compiles to an empty space where the diagram ' +
    'should be, and nobody notices until somebody reads the PDF.',
  E008:
    'A locator: promises the words sit at a known place in a page nobody ' +
    'archived, so the promise cannot be checked by anyone, ever.',
  E009:
    'The quoted words are not in the archived page. A verbatim quotation is ' +
    'compared word for word, because misquoting the audited party is the one ' +
    'error no later reader can catch.',
  E010:
    'A srctable(…) reads a file that is not there, which means the numbers in ' +
    'that table came from somewhere other than the file it cites.',
  E011:
    'The data file no longer matches the sha256 in sources.yml. That checksum is ' +
    'the one thing standing between a refreshed CSV and a number the prose ' +
    'around it already explained. Use data revise.',
  E012:
    'Starter residue: a KPI, a cover field or a bibliography entry still says ' +
    'what the scaffold said. Invented placeholders must never reach a reader ' +
    'wearing the clothes of a finding.',
  E013:
    'A bare URL in the prose reaches a page but never became a source, so ' +
    'nothing archived it and nothing will notice when it changes or goes.',
  E014:
    'The report calls itself final while errors stand. final is a claim the ' +
    'report makes about itself on its own cover, and it has to be true.',
  W001:
    'Nothing cites this source. Not an error — it still reaches the References ' +
    'section as part of the record of what was reviewed — but no claim rests on it.',
  W002:
    'A bare table(…) outside a srcfig. A table is evidence, and evidence names ' +
    'its source or says it is an assessment.',
  W003:
    'An image or diagram with no alt: is simply not there for a reader using a ' +
    'screen reader.',
  W004:
    'A quotation with no locator:. "Somewhere on the site" is not a citation — ' +
    'it cannot be turned back into the page it came from.',
  W005:
    'A registered data file that no table reads. Either the table is missing or ' +
    'the file is, and both are worth knowing before the report ships.',
  W006:
    'A srctable(…) citing something other than the file it actually reads. The ' +
    'citation and the numbers point at two different things.',
  W007:
    'A column empty in every row is usually a source that failed, arriving as ' +
    'data. Absence is reported as absence — file it with data absence, not as a ' +
    'blank the reader will read as a zero.',
  W008:
    'A column carrying one value in every row is usually a join that matched ' +
    'nothing rather than a world that is genuinely uniform.',
  W009:
    'A numeric column that is exactly 0 all the way down is usually a ' +
    'measurement that never ran.',
  W010:
    'Every citation in this passage resolves to one source family. Depth rather ' +
    'than density: a load-bearing passage resting on a single voice.',
  W011:
    'A status: nobody recognises, read as if the field were absent. A typo must ' +
    'never hand a report the leniency of draft.',
}

/** The sentence for a code, or null when the engine has grown one we have not. */
export function explain(code: string): string | null {
  return RULES[code] ?? null
}

// ── grouping, for the eye only ───────────────────────────────────────────────

export type FindingGroup = {
  /** Vault-relative POSIX path, as the engine printed it. */
  path: string
  /** The last segment, for a heading that fits on a phone. */
  name: string
  findings: Finding[]
  errors: number
  warnings: number
}

/**
 * Findings, in file order, in the order the engine listed them.
 *
 * This is arrangement, not analysis: no finding is created, dropped, reordered
 * within its file or re-levelled. Sorting by anything other than first
 * appearance would put this file in the business of ranking findings, which is
 * `check`'s job and not a browser's.
 */
export function groupFindingsByFile(findings: Finding[]): FindingGroup[] {
  const groups = new Map<string, FindingGroup>()
  for (const finding of findings) {
    let group = groups.get(finding.path)
    if (!group) {
      group = {
        path: finding.path,
        name: finding.path.split('/').filter(Boolean).pop() ?? finding.path,
        findings: [],
        errors: 0,
        warnings: 0,
      }
      groups.set(finding.path, group)
    }
    group.findings.push(finding)
    if (finding.level === 'error') group.errors += 1
    else group.warnings += 1
  }
  return [...groups.values()]
}

// ── the two buses ────────────────────────────────────────────────────────────
//
// Module-level and deliberately dumb. The Evidence pane and the editor are
// siblings under a shell that knows nothing about either; a bus is the smallest
// thing that connects them without teaching the shell about cursors.

/** Put the cursor on a line of a vault-relative file. */
export type RevealTarget = {
  /** The report the file belongs to, when the sender knows it. */
  report?: string | null
  /** Vault-relative POSIX, exactly as `check` printed it. */
  path: string
  /** 1-based, as every line number the engine prints is. */
  line: number
}

/** Put text at the cursor — a `@key` a citation just minted, and little else. */
export type InsertRequest = {
  report?: string | null
  text: string
}

function bus<T>() {
  const listeners = new Set<(value: T) => void>()
  return {
    on(listener: (value: T) => void) {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
    emit(value: T) {
      for (const listener of [...listeners]) listener(value)
    },
    /** So a sender can grey out an action nobody is listening for. */
    get size() {
      return listeners.size
    },
  }
}

const revealBus = bus<RevealTarget>()
const insertBus = bus<InsertRequest>()

/** Subscribe. The editor calls this; returns the unsubscribe. */
export const onReveal = revealBus.on
export const onInsert = insertBus.on

/** Ask whoever owns a cursor to move it. A no-op when nobody is listening. */
export function requestReveal(target: RevealTarget): void {
  revealBus.emit(target)
}

export function requestInsert(request: InsertRequest): void {
  insertBus.emit(request)
}

/** True when an editor is mounted and listening — used to hide a dead button. */
export function hasEditor(): boolean {
  return insertBus.size > 0
}

// ── clipboard ────────────────────────────────────────────────────────────────

/**
 * Copy, with the fallback a non-secure origin needs.
 *
 * `navigator.clipboard` exists only in a secure context. Loopback counts as one,
 * so the default deployment is fine — but the moment somebody runs the server on
 * a LAN address over plain HTTP the modern API is simply undefined, and a copy
 * button that silently does nothing is worse than no copy button.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // Fall through: a permission refusal is not a reason to give up entirely.
  }

  try {
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    // Off-screen, but not `display: none` — a hidden element cannot be selected.
    area.style.position = 'fixed'
    area.style.top = '-1000px'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(area)
    return ok
  } catch {
    return false
  }
}

/**
 * A copy button that says so for a moment afterwards.
 *
 * The confirmation is the whole point on a touch screen: there is no cursor to
 * change and no tooltip to show, so without this a tap produces no evidence at
 * all that anything happened.
 */
export function useCopy(timeout = 1600): {
  copied: string | null
  copy: (text: string, id?: string) => Promise<boolean>
} {
  const [copied, setCopied] = useState<string | null>(null)
  const timer = useRef<number | null>(null)

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current)
    },
    []
  )

  const copy = useCallback(
    async (text: string, id?: string) => {
      const ok = await copyText(text)
      if (!ok) return false
      if (timer.current !== null) window.clearTimeout(timer.current)
      setCopied(id ?? text)
      timer.current = window.setTimeout(() => setCopied(null), timeout)
      return true
    },
    [timeout]
  )

  return { copied, copy }
}

// ── display ──────────────────────────────────────────────────────────────────

/**
 * A date, short enough for a chip on a 375px screen.
 *
 * The engine writes ISO 8601. Anything it cannot parse is shown verbatim rather
 * than as "Invalid Date": a timestamp we do not understand is still information.
 */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso.slice(0, 10)
  return date.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

/** A density, as a whole-number percentage. The engine's fraction, rendered. */
export function percent(fraction: number): string {
  if (!Number.isFinite(fraction)) return '—'
  return `${Math.round(fraction * 100)}%`
}

/**
 * An href, or null.
 *
 * A source URL is text somebody typed into `sources.yml`, and `javascript:` is a
 * URL. Only http and https ever become a link; anything else is rendered as the
 * text it is, which loses nothing — the string is on screen either way.
 */
export function safeHref(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    const parsed = new URL(url, window.location.origin)
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return parsed.href
    return null
  } catch {
    return null
  }
}

/** `1 error` / `2 errors`, the way the CLI says it. */
export function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}
