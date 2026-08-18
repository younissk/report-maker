/**
 * The citation rule, shown in the margin.
 *
 * Nothing here decides what a finding *is*. `report-maker check --json` decides,
 * and this module is a projection of its answer onto the buffer: a state field
 * the app pushes findings into, and a lint source that turns the ones belonging
 * to the open file into line diagnostics. A renderer that re-derived even one of
 * these rules would be a second implementation of the rule, drifting from the
 * engine the first time a rule changed — so the only thing this file knows about
 * E006 is how to draw it.
 *
 * The findings describe the file **as last saved**. While the buffer is dirty
 * they can point at a line that has moved; that is honest rather than wrong, and
 * it is the same trade the evidence rail makes (see `rail.ts`).
 */

import { StateEffect, StateField, type EditorState, type Extension } from '@codemirror/state'
import { EditorView, ViewPlugin } from '@codemirror/view'
import { linter, type Diagnostic, type LintSource } from '@codemirror/lint'
import type { Finding } from '../../../shared/types'

/** What the editor has been told about the open file's findings. */
export type Findings = {
  findings: readonly Finding[]
  /**
   * The open file's path. Vault-relative POSIX is what `Finding.path` uses and
   * what the app should send; an absolute path also works, because the match
   * below accepts a path that merely *ends* with the finding's.
   */
  path: string | null
}

/** Push a new answer from `check --json` into the editor. */
export const setFindingsEffect = StateEffect.define<Findings>()

/**
 * The findings the editor currently holds.
 *
 * It lives outside every compartment the Editor reconfigures, so toggling the
 * lint gutter in Settings cannot throw away the findings we already have.
 */
export const findingState = StateField.define<Findings>({
  create: () => ({ findings: [], path: null }),
  update(value, tr) {
    for (const effect of tr.effects) if (effect.is(setFindingsEffect)) return effect.value
    return value
  }
})

/** Replace the editor's findings. `path` names the file they were computed for. */
export function setFindings(
  view: EditorView,
  findings: readonly Finding[],
  path: string | null
): void {
  view.dispatch({ effects: setFindingsEffect.of({ findings, path }) })
}

/**
 * One path test for both shapes the app might hold.
 *
 * `Finding.path` is always vault-relative POSIX. The editor may know the open
 * file only by its absolute path, and `reports/x/main.typ` is the suffix of
 * `/Users/…/vault/reports/x/main.typ` — so a suffix match on a segment boundary
 * answers both cases without the renderer having to know where the vault starts.
 */
function samePath(docPath: string | null, findingPath: string): boolean {
  if (!docPath) return false
  const doc = docPath.replace(/\\/g, '/')
  return doc === findingPath || doc.endsWith('/' + findingPath)
}

/** The findings that belong to the file currently in the editor. */
export function findingsFor(state: EditorState): Finding[] {
  const held = state.field(findingState, false)
  if (!held) return []
  return held.findings.filter((finding) => samePath(held.path, finding.path))
}

/**
 * `E006 · @market-size is not defined in sources.yml` — the code first, because
 * it is the thing you look up, and the sentence after it, because it is the
 * thing you act on.
 */
function label(finding: Finding): string {
  return finding.code ? `${finding.code} · ${finding.message}` : finding.message
}

/** Findings → diagnostics, one per line, clamped to a document that may have
 *  shrunk since the engine last read it. */
export const lintSource: LintSource = (view) => {
  const doc = view.state.doc
  const seen = new Set<string>()
  const diagnostics: Diagnostic[] = []

  for (const finding of findingsFor(view.state)) {
    const number = Math.min(Math.max(finding.line || 1, 1), doc.lines)
    // Two findings on one line are two diagnostics, but the same finding
    // reported twice is not — the engine can repeat a report-level finding.
    const fingerprint = `${number}:${finding.code}:${finding.message}`
    if (seen.has(fingerprint)) continue
    seen.add(fingerprint)

    const line = doc.line(number)
    diagnostics.push({
      from: line.from,
      to: line.to,
      severity: finding.level === 'error' ? 'error' : 'warning',
      source: 'report-maker check',
      message: label(finding)
    })
  }
  return diagnostics
}

/**
 * The linter itself.
 *
 * `delay: 0` because there is nothing to debounce — the expensive part already
 * happened in the engine, and this only re-reads a list. `needsRefresh` is what
 * makes a fresh `check --json` appear without touching the document.
 */
export function reportLinter(): Extension {
  return linter(lintSource, {
    delay: 0,
    needsRefresh: (update) =>
      update.transactions.some((tr) =>
        tr.effects.some((effect) => effect.is(setFindingsEffect))
      )
  })
}

/**
 * Clicking a gutter marker selects that finding, so the Problems panel can
 * filter to it.
 *
 * It listens on `view.dom` rather than through `EditorView.domEventHandlers`,
 * which only reaches the content: the gutters are siblings of the content, so a
 * click on a marker never arrives there.
 */
export function findingClicks(onSelect: (finding: Finding) => void): Extension {
  return ViewPlugin.define((view) => {
    const onMouseDown = (event: MouseEvent): void => {
      const target = event.target as HTMLElement | null
      if (!target?.closest('.cm-lint-marker')) return
      const block = view.lineBlockAtHeight(event.clientY - view.documentTop)
      const number = view.state.doc.lineAt(block.from).number
      const hit = findingsFor(view.state).find((finding) => finding.line === number)
      if (hit) onSelect(hit)
    }
    view.dom.addEventListener('mousedown', onMouseDown)
    return {
      destroy: () => view.dom.removeEventListener('mousedown', onMouseDown)
    }
  })
}
