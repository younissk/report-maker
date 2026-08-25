import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Plus, RefreshCw } from 'lucide-react'

import { useApp } from '@/App'
import { CiteSheet } from '@/components/CiteSheet'
import { Findings, findingsSummary } from '@/components/Findings'
import { Sources } from '@/components/Sources'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  ApiError,
  api,
  isAbort,
  type ReportScore,
  type ScoreSection,
  type SourceRow,
} from '@/lib/api'
import { guard } from '@/lib/session'
import { percent, plural, requestReveal, type RevealTarget } from '@/lib/evidence'
import { cn, useIsDesktop } from '@/lib/utils'

/**
 * The Evidence tab: the citation rule, made operable.
 *
 * Three things live here — what the rule found, what the report cites, and how
 * densely it is evidenced — plus the one action that turns a URL into a citable
 * source. Every one of them is a `/api` answer rendered as it arrived. Nothing
 * in this file decides whether a line is cited, whether a source is orphaned or
 * what a report's density is: `check`, `sources` and `score` decided all three,
 * and a second opinion computed in a browser is a second implementation of the
 * engine that will eventually disagree with the first.
 *
 * Mobile gets three tabs and desktop gets three stacked panels, which is the
 * same information in the shape each screen can hold. The Cite action sits in
 * the pane's own header in both, because it is the thing you reach for while
 * looking at any of the three.
 */

type Scope = 'report' | 'vault'

export function Evidence() {
  const { reportId, check, checking, refreshCheck, revision, setTab, selectReport } = useApp()
  const desktop = useIsDesktop()

  const [scope, setScope] = useState<Scope>('report')
  const [citing, setCiting] = useState(false)

  // `sources --json` and `score --json`, each re-read when the vault changes.
  const sources = useSources(reportId, revision)
  const score = useScore(reportId, revision)

  const findings = useMemo(() => {
    const all = check?.findings ?? []
    if (scope === 'vault' || !reportId) return all
    return all.filter((finding) => finding.report === reportId)
  }, [check, scope, reportId])

  /**
   * Travel to a finding. Selecting the report first matters: a finding in the
   * vault-wide list may belong to a report that is not the one on screen, and
   * jumping to a line of a file the editor has not opened is a jump to nowhere.
   */
  const reveal = useCallback(
    (target: RevealTarget) => {
      if (target.report && target.report !== reportId) selectReport(target.report)
      requestReveal(target)
      setTab('write')
    },
    [reportId, selectReport, setTab]
  )

  const refreshAll = useCallback(() => {
    refreshCheck()
    sources.reload()
    score.reload()
  }, [refreshCheck, sources, score])

  const header = (
    <div className="shrink-0 border-b">
      <div className="flex items-center gap-2 px-3 py-2">
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-medium tracking-widest text-muted-foreground uppercase">
            Evidence
          </div>
          <div className="truncate text-[11px] text-muted-foreground" role="status">
            {checking ? 'Checking…' : findingsSummary(findings)}
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={refreshAll}
          aria-label="Re-run the checks"
          className="lg:size-8"
        >
          <RefreshCw className={cn(checking && 'animate-spin')} aria-hidden />
        </Button>
        <Button
          size={desktop ? 'sm' : 'default'}
          onClick={() => setCiting(true)}
          disabled={!reportId}
        >
          <Plus aria-hidden />
          Cite
        </Button>
      </div>
    </div>
  )

  const findingsPanel = (
    <>
      {reportId && <ScopeToggle scope={scope} onChange={setScope} />}
      <Findings
        findings={findings}
        loading={checking}
        onReveal={reveal}
        onRetry={refreshCheck}
        emptyNote={
          scope === 'report'
            ? 'Every claim in this report either points at a source or is marked as an assessment.'
            : 'Nothing in this vault sits between a cited fact and a marked opinion.'
        }
      />
    </>
  )

  const sourcesPanel = (
    <Sources
      reportId={reportId}
      sources={sources.rows}
      loading={sources.loading}
      error={sources.error}
      detail={sources.detail}
      onRetry={sources.reload}
      onCite={() => setCiting(true)}
      onReveal={reveal}
    />
  )

  const densityPanel = (
    <Density
      report={score.report}
      loading={score.loading}
      error={score.error}
      detail={score.detail}
      onRetry={score.reload}
      reportId={reportId}
      onReveal={reveal}
    />
  )

  return (
    <div className="flex h-full min-h-0 flex-col">
      {header}

      {desktop ? (
        // One scrolling column, three stacked panels. The evidence rail is 320px
        // wide; side-by-side would give each section 100px and none of them
        // enough room for a source title.
        <div className="pane flex-1">
          <Section title="Density">{densityPanel}</Section>
          <Section title="Findings" count={findings.length}>
            {findingsPanel}
          </Section>
          <Section title="Sources" count={sources.rows.length}>
            {sourcesPanel}
          </Section>
        </div>
      ) : (
        <Tabs defaultValue="findings" className="min-h-0 flex-1 gap-0">
          <div className="shrink-0 px-3 py-2">
            <TabsList>
              <TabsTrigger value="findings" className="min-h-11 lg:min-h-7">
                Findings
                {findings.length > 0 && (
                  <Badge
                    variant={check && check.errors > 0 ? 'error' : 'secondary'}
                    className="tabular-nums"
                  >
                    {findings.length}
                  </Badge>
                )}
              </TabsTrigger>
              <TabsTrigger value="sources" className="min-h-11 lg:min-h-7">
                Sources
                {sources.rows.length > 0 && (
                  <Badge variant="secondary" className="tabular-nums">
                    {sources.rows.length}
                  </Badge>
                )}
              </TabsTrigger>
              <TabsTrigger value="density" className="min-h-11 lg:min-h-7">
                Density
              </TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="findings" className="pane">
            {findingsPanel}
          </TabsContent>
          <TabsContent value="sources" className="pane">
            {sourcesPanel}
          </TabsContent>
          <TabsContent value="density" className="pane">
            {densityPanel}
          </TabsContent>
        </Tabs>
      )}

      <CiteSheet
        reportId={reportId}
        open={citing}
        onOpenChange={setCiting}
        // A new source changes the bibliography and can clear an E006, so both
        // the panel and the rule's verdict are stale the moment it lands.
        onCited={() => {
          sources.reload()
          refreshCheck()
        }}
      />
    </div>
  )
}

// ── the desktop stack ────────────────────────────────────────────────────────

function Section({
  title,
  count,
  children,
}: {
  title: string
  count?: number
  children: ReactNode
}) {
  return (
    <section className="border-b last:border-b-0">
      <h2 className="sticky top-0 z-10 flex items-center gap-2 border-b bg-background px-3 py-1.5 text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
        <span>{title}</span>
        {count !== undefined && count > 0 && (
          <span className="tabular-nums normal-case">{count}</span>
        )}
      </h2>
      {children}
    </section>
  )
}

function ScopeToggle({
  scope,
  onChange,
}: {
  scope: Scope
  onChange: (scope: Scope) => void
}) {
  return (
    <div className="flex items-center gap-1 border-b px-3 py-2">
      {(
        [
          ['report', 'This report'],
          ['vault', 'Whole vault'],
        ] as const
      ).map(([value, label]) => (
        <button
          key={value}
          type="button"
          onClick={() => onChange(value)}
          aria-pressed={scope === value}
          className={cn(
            'tap inline-flex items-center rounded-md px-3 text-xs font-medium whitespace-nowrap outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 lg:min-h-7 lg:min-w-0',
            scope === value
              ? 'bg-secondary text-secondary-foreground'
              : 'text-muted-foreground active:bg-accent hover:bg-accent'
          )}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

// ── density ──────────────────────────────────────────────────────────────────

/**
 * How well evidenced this report is, as `score --json` measures it.
 *
 * Three counts and a bar. The honest summary of the whole product, so it is laid
 * out to be read rather than to decorate: every segment carries its own number,
 * the legend is beside the bar rather than in a key somewhere else, and the
 * percentage is the engine's own `density`, never an average taken here.
 */
function Density({
  report,
  loading,
  error,
  detail,
  onRetry,
  reportId,
  onReveal,
}: {
  report: ReportScore | null
  loading: boolean
  error: string | null
  detail: string | null
  onRetry: () => void
  reportId: string | null
  onReveal: (target: RevealTarget) => void
}) {
  if (!reportId) {
    return (
      <p className="px-6 py-10 text-center text-sm text-muted-foreground">
        Select a report to see how densely it is evidenced.
      </p>
    )
  }

  if (loading && !report) {
    return (
      <div className="flex flex-col gap-2 p-3">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-start gap-3 p-3">
        <p className="text-sm text-muted-foreground break-anywhere">
          The density could not be measured. {error}
        </p>
        {detail && (
          <pre className="scroll-x max-h-48 w-full overflow-y-auto rounded-md border bg-muted p-3 font-mono text-[11px] whitespace-pre">
            {detail}
          </pre>
        )}
        <Button variant="outline" onClick={onRetry}>
          Try again
        </Button>
      </div>
    )
  }

  if (!report) {
    return (
      <p className="px-6 py-10 text-center text-sm text-muted-foreground">
        No score for this report yet.
      </p>
    )
  }

  const total = report.cited + report.assessed + report.unmarked

  return (
    <div className="flex flex-col gap-4 p-3">
      <div className="flex flex-col gap-2">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-2xl font-semibold tabular-nums">
            {percent(report.density)}
          </span>
          <span className="text-right text-[11px] text-muted-foreground">
            cited or assessed
            <br />
            {plural(total, 'claim')} counted
          </span>
        </div>

        <Bar cited={report.cited} assessed={report.assessed} unmarked={report.unmarked} />

        <ul className="flex flex-col gap-1">
          <Legend swatch="rail-cited" label="cited" value={report.cited} total={total}>
            points at a source
          </Legend>
          <Legend
            swatch="rail-assessed"
            label="assessed"
            value={report.assessed}
            total={total}
          >
            marked as judgement
          </Legend>
          <Legend
            swatch="rail-unmarked"
            label="unmarked"
            value={report.unmarked}
            total={total}
          >
            neither: an opinion in the clothes of a fact
          </Legend>
        </ul>
      </div>

      <p className="text-[11px] text-muted-foreground break-anywhere">
        <span className="tabular-nums">
          {report.sourcesCited} of {report.sourcesTotal}
        </span>{' '}
        {report.sourcesTotal === 1 ? 'source is' : 'sources are'} cited by something.
        The rest still reach the References section.
      </p>

      {report.sections.length > 0 && (
        <div className="flex flex-col gap-1">
          <h3 className="text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
            By section
          </h3>
          <ul className="flex flex-col divide-y rounded-lg border">
            {report.sections.map((section, index) => (
              <SectionRow
                key={`${section.line}:${index}`}
                section={section}
                onSelect={() =>
                  onReveal({
                    report: reportId,
                    path: `reports/${reportId}/main.typ`,
                    line: section.line,
                  })
                }
              />
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function SectionRow({
  section,
  onSelect,
}: {
  section: ScoreSection
  onSelect: () => void
}) {
  const total = section.cited + section.assessed + section.unmarked
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        className="tap flex w-full flex-col items-stretch gap-1.5 px-3 py-2 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset active:bg-accent lg:min-h-0 hover:bg-accent"
      >
        <span className="flex items-baseline gap-2">
          {/* Section titles are the author's own words. Escaped, wrapped, and
              indented by heading level so the outline is readable at 375px. */}
          <span
            className="min-w-0 flex-1 text-[13px] leading-snug break-anywhere"
            style={{ paddingLeft: `${Math.max(0, section.level - 1) * 10}px` }}
          >
            {section.title}
          </span>
          <span className="shrink-0 text-[11px] text-muted-foreground tabular-nums">
            {percent(section.density)}
          </span>
        </span>
        <Bar
          cited={section.cited}
          assessed={section.assessed}
          unmarked={section.unmarked}
          thin
        />
        <span className="text-[10px] text-muted-foreground tabular-nums">
          {section.cited} cited · {section.assessed} assessed · {section.unmarked} unmarked
          {total === 0 && ' — nothing counted here'}
        </span>
      </button>
    </li>
  )
}

/**
 * The three counts as one bar.
 *
 * A segment carrying at least one claim is never allowed to vanish: one unmarked
 * sentence in four hundred is exactly the case the bar exists to show, and at
 * true proportion it would be a quarter of a pixel wide.
 */
function Bar({
  cited,
  assessed,
  unmarked,
  thin = false,
}: {
  cited: number
  assessed: number
  unmarked: number
  thin?: boolean
}) {
  const total = cited + assessed + unmarked
  const label = `${cited} cited, ${assessed} assessed, ${unmarked} unmarked`

  if (total === 0) {
    return (
      <span
        className={cn('block w-full rounded-full rail-neutral', thin ? 'h-1' : 'h-2')}
        role="img"
        aria-label="nothing counted"
      />
    )
  }

  const segments: [number, string][] = [
    [cited, 'rail-cited'],
    [assessed, 'rail-assessed'],
    [unmarked, 'rail-unmarked'],
  ]

  return (
    <span
      role="img"
      aria-label={label}
      className={cn(
        'flex w-full overflow-hidden rounded-full rail-neutral',
        thin ? 'h-1' : 'h-2'
      )}
    >
      {segments.map(([value, klass]) =>
        value === 0 ? null : (
          <span
            key={klass}
            className={klass}
            style={{ width: `${(value / total) * 100}%`, minWidth: '3px' }}
          />
        )
      )}
    </span>
  )
}

function Legend({
  swatch,
  label,
  value,
  total,
  children,
}: {
  swatch: string
  label: string
  value: number
  total: number
  children: ReactNode
}) {
  return (
    <li className="flex items-start gap-2 text-[11px]">
      <span className={cn('mt-1 size-2 shrink-0 rounded-full', swatch)} aria-hidden />
      <span className="min-w-0 flex-1 break-anywhere">
        <span className="font-medium">{label}</span>{' '}
        <span className="text-muted-foreground">— {children}</span>
      </span>
      <span className="shrink-0 tabular-nums">
        {value}
        <span className="text-muted-foreground">
          {total > 0 ? ` · ${percent(value / total)}` : ''}
        </span>
      </span>
    </li>
  )
}

// ── the two fetches this pane owns ───────────────────────────────────────────
//
// Both are the same shape: one endpoint, an abort on the way out, and the
// server's own message when it refuses. Neither transforms what came back.

function useSources(reportId: string | null, revision: number) {
  const [rows, setRows] = useState<SourceRow[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [detail, setDetail] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const run = useRef<AbortController | null>(null)

  useEffect(() => {
    run.current?.abort()
    if (!reportId) {
      setRows([])
      setError(null)
      setDetail(null)
      setLoading(false)
      return
    }
    const controller = new AbortController()
    run.current = controller
    setLoading(true)
    setError(null)
    setDetail(null)
    guard((signal) => api.sources(reportId, signal), controller.signal)
      .then((result) => {
        setRows(result)
        setLoading(false)
      })
      .catch((thrown: unknown) => {
        if (isAbort(thrown)) return
        const { message, detail: text } = describe(thrown)
        setError(message)
        setDetail(text)
        setLoading(false)
      })
    return () => controller.abort()
  }, [reportId, revision, nonce])

  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { rows, loading, error, detail, reload }
}

function useScore(reportId: string | null, revision: number) {
  const [report, setReport] = useState<ReportScore | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [detail, setDetail] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const run = useRef<AbortController | null>(null)

  useEffect(() => {
    run.current?.abort()
    if (!reportId) {
      setReport(null)
      setError(null)
      setDetail(null)
      setLoading(false)
      return
    }
    const controller = new AbortController()
    run.current = controller
    setLoading(true)
    setError(null)
    setDetail(null)
    guard((signal) => api.score(reportId, signal), controller.signal)
      .then((result) => {
        // `score --json` answers with a list whatever the target was. Picking
        // this report's row out of it is a lookup, not a calculation — the sums
        // inside the row are the engine's and are used exactly as they arrived.
        setReport(
          result.reports.find((row) => row.id === reportId) ?? result.reports[0] ?? null
        )
        setLoading(false)
      })
      .catch((thrown: unknown) => {
        if (isAbort(thrown)) return
        const { message, detail: text } = describe(thrown)
        setError(message)
        setDetail(text)
        setLoading(false)
      })
    return () => controller.abort()
  }, [reportId, revision, nonce])

  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { report, loading, error, detail, reload }
}

/** The server's own words, whatever it threw. */
function describe(error: unknown): { message: string; detail: string | null } {
  return error instanceof ApiError
    ? { message: error.message, detail: error.detail }
    : { message: error instanceof Error ? error.message : String(error), detail: null }
}

export default Evidence
