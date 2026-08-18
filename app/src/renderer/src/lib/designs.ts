/**
 * The vault's designs, as the engine lists them.
 *
 * A design is Typst that runs when a report is built, so which designs a vault
 * has, where each one came from and what it inherits are not questions the app
 * may answer on its own. Everything here is a call to `templates --json`,
 * `template list --installed --json` or `list --json`, or a way of holding on to
 * what one of them returned.
 *
 * The one thing this module derives is the *lineage chain*: it follows the
 * `extends` pointer each design already printed, which is the same walk
 * `vault.lineage` does in the engine. That is a rendering of engine data rather
 * than a second copy of a rule — and when the chain actually matters,
 * `template show` prints the authoritative one, which the screen shows verbatim
 * rather than paraphrasing.
 */

import { useCallback, useEffect, useState } from 'react'
import type { ReportRow, Run, TemplateRow } from '../../../shared/types'
import { describeError } from '@/lib/sources'

// ── What the engine prints ───────────────────────────────────────────────────

/**
 * One row of `template list --installed --json`: a design that was fetched from
 * a git URL rather than written in the vault or shipped with the engine. The
 * keys are the engine's, spelled as it spells them.
 */
export type InstalledDesign = {
  id: string
  url: string
  /** The branch, tag or commit that was asked for; null when the default was taken. */
  ref: string | null
  /** The commit actually installed — the only exact answer to "which code is this". */
  sha: string
  /** Sub-path inside the repository, when the design is not at its root. */
  subdir: string | null
  /** ISO datetime of the install. */
  installed_at: string
  /** Where it landed, so uninstall can name the folder it is about to remove. */
  folder: string
}

export type DesignOrigin = 'built-in' | 'vault' | 'installed'

/** A design with everything the screen needs about it, assembled from the three
 *  commands above. Nothing here is computed except `lineage` and the counts. */
export type Design = TemplateRow & {
  id: string
  origin: DesignOrigin
  /** The install record, when this design came from a URL. */
  installed: InstalledDesign | null
  /** How many reports build with it — from the `template` field of `list --json`. */
  uses: number
  /** This design and its ancestors, oldest first. */
  lineage: string[]
  /** An ancestor named by `extends` that no longer exists; builds using it fail. */
  missingParent: string | null
}

// ── Loading ──────────────────────────────────────────────────────────────────

export function loadTemplates(vault: string): Promise<Record<string, TemplateRow>> {
  return window.api.engine.json<Record<string, TemplateRow>>(vault, ['templates', '--json'])
}

export function loadInstalled(vault: string): Promise<InstalledDesign[]> {
  return window.api.engine.json<InstalledDesign[]>(vault, [
    'template',
    'list',
    '--installed',
    '--json'
  ])
}

export function loadReports(vault: string): Promise<ReportRow[]> {
  return window.api.engine.json<ReportRow[]>(vault, ['list', '--json'])
}

export type UseDesigns = {
  designs: Design[]
  loading: boolean
  /** `templates --json` failed — there is nothing to show. */
  error: string | null
  /** The install ledger or the report list failed while the designs loaded. The
   *  screen still works; it just cannot say which cards are installed or used. */
  partial: string | null
  reload: () => void
}

/**
 * Every design in a vault, kept current.
 *
 * Only `templates --json` is load-bearing: a vault whose install ledger or
 * report list cannot be read still has designs worth browsing, and a screen that
 * blanks itself over a missing side-answer is a screen you cannot use to fix the
 * thing that is missing. Those two failures degrade to `partial` instead.
 */
export function useDesigns(vault: string | null, revision = 0): UseDesigns {
  const [designs, setDesigns] = useState<Design[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [partial, setPartial] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    if (!vault) {
      setDesigns([])
      setError(null)
      setPartial(null)
      setLoading(false)
      return
    }
    let stale = false
    setLoading(true)

    void (async () => {
      const soft: string[] = []
      const installed = await loadInstalled(vault).catch((err) => {
        soft.push(`template list --installed: ${describeError(err)}`)
        return [] as InstalledDesign[]
      })
      const reports = await loadReports(vault).catch((err) => {
        soft.push(`list --json: ${describeError(err)}`)
        return [] as ReportRow[]
      })

      try {
        const templates = await loadTemplates(vault)
        if (stale) return
        setDesigns(collate(templates, installed, reports))
        setError(null)
        setPartial(soft.length > 0 ? soft.join('\n') : null)
      } catch (err) {
        if (stale) return
        setDesigns([])
        setError(describeError(err))
        setPartial(null)
      } finally {
        if (!stale) setLoading(false)
      }
    })()

    return () => {
      stale = true
    }
  }, [vault, revision, nonce])

  return { designs, loading, error, partial, reload }
}

// ── Assembling one card's worth ──────────────────────────────────────────────

export function collate(
  templates: Record<string, TemplateRow>,
  installed: InstalledDesign[],
  reports: ReportRow[]
): Design[] {
  const records = new Map(installed.map((row) => [row.id, row]))
  const counts = countUses(reports)

  return Object.entries(templates)
    .map(([id, row]) => {
      const record = records.get(id) ?? null
      const walked = lineage(templates, id)
      return {
        ...row,
        id,
        origin: originOf(row, record),
        installed: record,
        uses: counts.get(id) ?? 0,
        lineage: walked.chain,
        missingParent: walked.missing
      }
    })
    .sort((a, b) => a.id.localeCompare(b.id))
}

/**
 * Where a design came from. `builtin` is the engine's word for "ships with
 * report-maker"; everything else lives in the vault, and the install ledger is
 * what separates a design somebody wrote here from one fetched from a URL.
 */
function originOf(row: TemplateRow, record: InstalledDesign | null): DesignOrigin {
  if (row.builtin) return 'built-in'
  return record ? 'installed' : 'vault'
}

/** How many reports name each design, from the `template` field of `list --json`. */
export function countUses(reports: ReportRow[]): Map<string, number> {
  const counts = new Map<string, number>()
  for (const report of reports) {
    counts.set(report.template, (counts.get(report.template) ?? 0) + 1)
  }
  return counts
}

/**
 * A design and its ancestors, oldest first — the order in which their files
 * merge, so the chain reads the way the build reads it.
 *
 * An `extends` naming a design that is not installed is reported rather than
 * dropped: that is a broken vault, and the card should say so instead of
 * silently rendering a shorter chain. A loop just stops the walk; the engine
 * raises on one, and this is not the place to relitigate that.
 */
export function lineage(
  templates: Record<string, TemplateRow>,
  id: string
): { chain: string[]; missing: string | null } {
  const chain: string[] = []
  const seen = new Set<string>()
  let current: string | null = id
  let missing: string | null = null

  while (current) {
    if (seen.has(current)) break
    seen.add(current)
    chain.push(current)
    const row: TemplateRow | undefined = templates[current]
    if (!row) {
      missing = current
      break
    }
    current = row.extends
  }

  return { chain: chain.reverse(), missing }
}

// ── Grouping and filtering ───────────────────────────────────────────────────

export type DesignGroup = { group: string; designs: Design[] }

/** Grouped by the folder the design sits in, which is the only grouping a vault
 *  has — nesting `templates/audits/company/` is how you say "an audit design". */
export function grouped(designs: Design[]): DesignGroup[] {
  const groups = new Map<string, Design[]>()
  for (const design of designs) {
    const bucket = groups.get(design.group)
    if (bucket) bucket.push(design)
    else groups.set(design.group, [design])
  }
  return [...groups.entries()]
    .map(([group, items]) => ({ group, designs: items }))
    .sort((a, b) => a.group.localeCompare(b.group))
}

/** Substring match over what somebody would type to find a design. Literal, not
 *  fuzzy: a vault has tens of designs, not thousands. */
export function matches(design: Design, query: string): boolean {
  const needle = query.trim().toLowerCase()
  if (!needle) return true
  return [
    design.id,
    design.title,
    design.description,
    design.group,
    design.brand,
    design.origin,
    design.installed?.url ?? ''
  ]
    .join(' ')
    .toLowerCase()
    .includes(needle)
}

// ── The commands ─────────────────────────────────────────────────────────────

export type InstallSpec = {
  url: string
  /** Left empty, the engine names the design itself. */
  id: string
  ref: string
  subdir: string
  force: boolean
}

export const emptyInstall: InstallSpec = { url: '', id: '', ref: '', subdir: '', force: false }

/**
 * The argv for `template install`. Built in one place so the review step can
 * show the *same* array it is about to run: a dialog that describes one command
 * and runs another is worse than no dialog at all.
 */
export function installArgs(spec: InstallSpec): string[] {
  const args = ['template', 'install', spec.url.trim()]
  if (spec.id.trim()) args.push('--id', spec.id.trim())
  if (spec.ref.trim()) args.push('--ref', spec.ref.trim())
  if (spec.subdir.trim()) args.push('--subdir', spec.subdir.trim())
  if (spec.force) args.push('--force')
  return args
}

export function updateArgs(id?: string): string[] {
  return id ? ['template', 'update', id] : ['template', 'update']
}

export function uninstallArgs(id: string): string[] {
  return ['template', 'uninstall', id]
}

export function showArgs(id: string): string[] {
  return ['template', 'show', id]
}

/** How a command reads once it has run: the argv, the exit code, and both
 *  streams, all verbatim. */
export function commandLine(args: string[]): string {
  return `report-maker ${args.join(' ')}`
}

/**
 * Run an engine command and always come back with a `Run`.
 *
 * `engine.run` already resolves rather than rejects for a non-zero exit, but the
 * IPC call itself can still throw. To the person waiting, a bridge that failed
 * and a command that failed are the same event — so both arrive here as output
 * they can read.
 */
export async function run(vault: string, args: string[]): Promise<Run> {
  try {
    return await window.api.engine.run(vault, args)
  } catch (err) {
    return { code: -1, stdout: '', stderr: describeError(err), command: commandLine(args) }
  }
}

// ── Reading a git URL ────────────────────────────────────────────────────────

export type GitUrl = { host: string; path: string }

/**
 * The host and repository path of a git remote, for the review step.
 *
 * Only the host is worth putting in front of somebody deciding whether to trust
 * an install, and it is the part a long URL hides. Returns null when the text is
 * not a remote this could plausibly be — the engine is still the one that
 * decides, so this only ever softens the button, never blocks it.
 */
export function parseGitUrl(text: string): GitUrl | null {
  const raw = text.trim()
  if (!raw) return null

  // scp-style — `git@github.com:owner/repo.git` — is not a URL and never parses.
  const scp = /^([^@\s/]+@)?([^\s:/]+):(?!\/\/)(.+)$/.exec(raw)
  if (scp) return { host: scp[2], path: tidyPath(scp[3]) }

  try {
    const url = new URL(raw)
    if (!/^(https?|ssh|git):$/.test(url.protocol)) return null
    if (!url.hostname) return null
    return { host: url.hostname, path: tidyPath(url.pathname) }
  } catch {
    return null
  }
}

function tidyPath(path: string): string {
  return path.replace(/^\/+/, '').replace(/\.git$/i, '').replace(/\/+$/, '')
}

/**
 * A first guess at the design id, for the install dialog's id field.
 *
 * A suggestion in an editable field, not a rule: whatever ends up in that field
 * is passed to the engine as `--id`, so what the review step shows is exactly
 * what gets installed. Leaving it empty hands the naming back to the engine.
 */
export function suggestId(url: string, subdir = ''): string {
  const fromSubdir = subdir.trim().replace(/\/+$/, '').split('/').filter(Boolean).pop()
  const parsed = parseGitUrl(url)
  const fromUrl = parsed?.path.split('/').filter(Boolean).pop()
  return slug(fromSubdir || fromUrl || '')
}

function slug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

// ── Formatting ───────────────────────────────────────────────────────────────

/** Enough of a commit to recognise, which is all a card has room for. The full
 *  sha stays in the title attribute — it is the exact answer to "which code". */
export function shortSha(sha: string): string {
  return sha.slice(0, 7)
}

/** The day part of an ISO datetime; the second an install happened is never the
 *  thing being read. */
export function day(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : ''
}
