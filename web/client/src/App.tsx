import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import {
  AlertCircle,
  FileWarning,
  GitBranch,
  ListChecks,
  Loader2,
  MoreHorizontal,
  RotateCcw,
  Search as SearchIcon,
} from 'lucide-react'

import { Connect } from '@/components/Connect'
import { Pad } from '@/components/Pad'
import { Search } from '@/components/Search'
import { Shell, type ShellTab } from '@/components/Shell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import {
  api,
  errorText,
  isAbort,
  type BuildResult,
  type CheckResult,
  type Finding,
  type Session,
} from '@/lib/api'
import { requestReveal } from '@/lib/evidence'
import { guard, useSession } from '@/lib/session'
import { cn, useIsDesktop } from '@/lib/utils'

/**
 * The application.
 *
 * Three things happen here and nothing else: a session is obtained, the first
 * run is explained, and the four panes are handed to the shell. Every fact on
 * screen came out of an `/api` call — this file computes nothing about a vault,
 * and the moment it starts to, it has become a second implementation of the
 * engine that will disagree with the first.
 */

// ── the context every pane codes against ─────────────────────────────────────

export type AppContextValue = {
  session: Session

  /** The report being worked on, or null when none is selected. */
  reportId: string | null
  selectReport: (id: string | null) => void

  tab: ShellTab
  setTab: (tab: ShellTab) => void

  /**
   * The citation rule's verdict on the whole vault, as `check --json --score`
   * last returned it. Never recomputed here, never filtered here — a pane that
   * wants one report's findings filters `findings` by `report`.
   */
  check: CheckResult | null
  checking: boolean
  refreshCheck: () => void

  /** `all <id>` on the selected report. */
  build: () => void
  building: boolean
  buildResult: BuildResult | null

  /**
   * Bumped whenever something wrote to the vault. A pane that caches a list
   * should depend on this rather than inventing its own invalidation.
   */
  revision: number
  invalidate: () => void
}

const AppContext = createContext<AppContextValue | null>(null)

export function useApp(): AppContextValue {
  const value = useContext(AppContext)
  if (!value) throw new Error('useApp() must be called inside <App>')
  return value
}

// ── panes ────────────────────────────────────────────────────────────────────

/**
 * The four panes, injectable.
 *
 * They default to placeholders so the shell builds and runs on its own. Each
 * pane is a separate piece of work: drop the real component in here and delete
 * the placeholder. Panes read shared state with `useApp()`.
 */
export type Panes = {
  reports: ReactNode
  write: ReactNode
  read: ReactNode
  evidence: ReactNode
}

function Placeholder({ name, owns }: { name: string; owns: string }) {
  return (
    <div className="pane flex-1 p-4">
      <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
        <div className="font-medium text-foreground">{name}</div>
        <p className="mt-1 break-anywhere">
          Not built yet. The component belongs in{' '}
          <code className="font-mono text-[12px]">{owns}</code> and is passed to{' '}
          <code className="font-mono text-[12px]">&lt;App panes={'{…}'} /&gt;</code>.
        </p>
      </div>
    </div>
  )
}

const PLACEHOLDERS: Panes = {
  reports: <Placeholder name="Reports" owns="src/components/ReportsPane.tsx" />,
  write: <Placeholder name="Write" owns="src/components/WritePane.tsx" />,
  read: <Placeholder name="Read" owns="src/components/ReadPane.tsx" />,
  evidence: <Placeholder name="Evidence" owns="src/components/EvidencePane.tsx" />,
}

// ── the app ──────────────────────────────────────────────────────────────────

export function App({ panes = PLACEHOLDERS }: { panes?: Panes }) {
  const { state, retry, reset } = useSession()

  if (state.status === 'seeding' || state.status === 'idle') {
    return <Seeding step={state.status === 'seeding' ? state.step : 'Starting…'} />
  }

  if (state.status === 'failed') {
    return <Unreachable message={state.message} detail={state.detail} onRetry={retry} />
  }

  return (
    <Workspace
      key={state.session.id}
      session={state.session}
      fresh={state.fresh}
      panes={panes}
      onReset={reset}
    />
  )
}

function Workspace({
  session,
  fresh,
  panes,
  onReset,
}: {
  session: Session
  fresh: boolean
  panes: Panes
  onReset: () => Promise<Session>
}) {
  const desktop = useIsDesktop()
  const [tab, setTab] = useState<ShellTab>('write')
  const [reportId, setReportId] = useState<string | null>(session.seeded ?? null)
  const [revision, setRevision] = useState(0)

  const [check, setCheck] = useState<CheckResult | null>(null)
  const [checking, setChecking] = useState(true)
  const [checkError, setCheckError] = useState<string | null>(null)

  const [building, setBuilding] = useState(false)
  const [buildResult, setBuildResult] = useState<BuildResult | null>(null)

  // The three things that are neither a pane nor a tab: the pad, the search and
  // the repository. Each is a sheet on a phone and a right-hand panel on a
  // desktop, and each is reached from the top bar rather than from the tab bar —
  // four tabs is the whole navigation, and a fifth would make it a menu.
  const [moreOpen, setMoreOpen] = useState(false)
  const [padOpen, setPadOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [repoOpen, setRepoOpen] = useState(false)

  // The first run is dismissed once and never returns. It is shown when this
  // load created the session, which is exactly the case where the vault holds
  // nothing but the starter.
  const [introDismissed, setIntroDismissed] = useState(!fresh)

  const checkRun = useRef<AbortController | null>(null)

  const refreshCheck = useCallback(() => {
    checkRun.current?.abort()
    const controller = new AbortController()
    checkRun.current = controller
    setChecking(true)
    setCheckError(null)
    guard((signal) => api.check(undefined, signal), controller.signal)
      .then((result) => {
        setCheck(result)
        setChecking(false)
      })
      .catch((error) => {
        if (isAbort(error)) return
        setCheckError(errorText(error))
        setChecking(false)
      })
  }, [])

  const invalidate = useCallback(() => {
    setRevision((n) => n + 1)
    refreshCheck()
  }, [refreshCheck])

  useEffect(() => {
    refreshCheck()
    return () => checkRun.current?.abort()
  }, [refreshCheck])

  // The first report a fresh vault was seeded with. Asked for rather than
  // assumed: `POST /session` may or may not name it, and the list is the
  // authority either way.
  useEffect(() => {
    if (reportId) return
    let cancelled = false
    guard((signal) => api.listReports(signal))
      .then((rows) => {
        if (!cancelled && rows.length > 0) setReportId(rows[0].id)
      })
      .catch(() => {
        /* The Reports pane reports this properly; the shell just stays empty. */
      })
    return () => {
      cancelled = true
    }
  }, [reportId, revision])

  const build = useCallback(() => {
    if (!reportId || building) return
    setBuilding(true)
    guard((signal) => api.build(reportId, signal))
      .then((result) => {
        setBuildResult(result)
        setBuilding(false)
        // A build ends with `check`, so the verdict on screen is now stale.
        invalidate()
      })
      .catch((error) => {
        if (isAbort(error)) return
        setBuildResult({
          ok: false,
          code: -1,
          stdout: '',
          stderr: errorText(error),
          artefacts: {},
        })
        setBuilding(false)
      })
  }, [reportId, building, invalidate])

  const value = useMemo<AppContextValue>(
    () => ({
      session,
      reportId,
      selectReport: setReportId,
      tab,
      setTab,
      check,
      checking,
      refreshCheck,
      build,
      building,
      buildResult,
      revision,
      invalidate,
    }),
    [
      session,
      reportId,
      tab,
      check,
      checking,
      refreshCheck,
      build,
      building,
      buildResult,
      revision,
      invalidate,
    ]
  )

  if (!introDismissed) {
    return (
      <AppContext.Provider value={value}>
        <FirstRun
          check={check}
          checking={checking}
          error={checkError}
          reportId={reportId}
          explainer={session.starterExplainer ?? null}
          onStart={() => {
            setIntroDismissed(true)
            setTab('write')
          }}
          onSeeFindings={() => {
            setIntroDismissed(true)
            setTab('evidence')
          }}
        />
      </AppContext.Provider>
    )
  }

  const errors = check?.errors ?? 0

  return (
    <AppContext.Provider value={value}>
      <Shell
        tab={tab}
        onTabChange={setTab}
        title={reportId ?? 'report-maker'}
        subtitle={
          checking
            ? 'Checking the citation rule…'
            : checkError
              ? checkError
              : check
                ? `${count(check.errors, 'error')}, ${count(check.warnings, 'warning')}`
                : undefined
        }
        onBuild={reportId ? build : undefined}
        building={building}
        counts={{ evidence: errors + (check?.warnings ?? 0) }}
        alerts={{ evidence: errors > 0 }}
        actions={
          // Four icons and a Build button leave a 375px top bar with room for
          // six characters of title, which was measured and was worse than the
          // extra tap. On a phone they collapse into one button that opens a
          // sheet; from 1024px up, where there is room, they sit in the bar.
          desktop ? (
            <>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Search this vault"
                onClick={() => setSearchOpen(true)}
              >
                <SearchIcon aria-hidden />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label="The pad — this report's tasks and notes"
                onClick={() => setPadOpen(true)}
                disabled={!reportId}
              >
                <ListChecks aria-hidden />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Repository"
                onClick={() => setRepoOpen(true)}
              >
                <GitBranch aria-hidden />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Start again with a new vault"
                onClick={() => void onReset()}
              >
                <RotateCcw aria-hidden />
              </Button>
            </>
          ) : (
            <Button
              variant="ghost"
              // `icon`, not `icon-sm`: 44px under the breakpoint, 36px above it.
              size="icon"
              aria-label="More — search, the pad, the repository"
              onClick={() => setMoreOpen(true)}
            >
              <MoreHorizontal aria-hidden />
            </Button>
          )
        }
        reports={panes.reports}
        write={panes.write}
        read={panes.read}
        evidence={panes.evidence}
        footer={<StatusBar check={check} checking={checking} building={building} />}
      />

      <More
        open={moreOpen}
        onOpenChange={setMoreOpen}
        hasReport={Boolean(reportId)}
        onSearch={() => {
          setMoreOpen(false)
          setSearchOpen(true)
        }}
        onPad={() => {
          setMoreOpen(false)
          setPadOpen(true)
        }}
        onRepo={() => {
          setMoreOpen(false)
          setRepoOpen(true)
        }}
        onReset={() => {
          setMoreOpen(false)
          void onReset()
        }}
      />

      {/* One jump handler serves all three: ask whoever owns a cursor to move,
          then show the pane that owns it. Exactly what the Evidence tab does
          with a finding, and the Write pane is already listening. */}
      <Pad
        reportId={reportId}
        open={padOpen}
        onOpenChange={setPadOpen}
        revision={revision}
        onJump={(path, line) => {
          setPadOpen(false)
          requestReveal({ report: reportId, path, line })
          setTab('write')
        }}
      />
      <Search
        open={searchOpen}
        onOpenChange={setSearchOpen}
        onOpenHit={(hit) => {
          setSearchOpen(false)
          if (hit.report) setReportId(hit.report)
          // A snapshot hit has no line — there is nothing in the editor to jump
          // to, so the source list is where it belongs.
          if (hit.line == null) {
            setTab('evidence')
            return
          }
          requestReveal({ report: hit.report, path: hit.path, line: hit.line })
          setTab('write')
        }}
      />
      <Connect open={repoOpen} onOpenChange={setRepoOpen} />
    </AppContext.Provider>
  )
}

/**
 * The three things that are not a pane, on a phone.
 *
 * Rows rather than icons: at this size a label is cheaper to read than an icon
 * is to decode, and each row is a 56px target. Every one says what it opens and
 * what that is for, because a list of four verbs with no object is a menu you
 * have to open to find out what is in it.
 */
function More({
  open,
  onOpenChange,
  hasReport,
  onSearch,
  onPad,
  onRepo,
  onReset,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  hasReport: boolean
  onSearch: () => void
  onPad: () => void
  onRepo: () => void
  onReset: () => void
}) {
  const rows: {
    icon: typeof SearchIcon
    label: string
    note: string
    run: () => void
    disabled?: boolean
  }[] = [
    {
      icon: SearchIcon,
      label: 'Search this vault',
      note: 'Prose, sources, archived pages and diagrams.',
      run: onSearch,
    },
    {
      icon: ListChecks,
      label: 'The pad',
      note: "This report's tasks and notes. Never compiled, never cited.",
      run: onPad,
      disabled: !hasReport,
    },
    {
      icon: GitBranch,
      label: 'Repository',
      note: 'Commit and push, when a repository is connected.',
      run: onRepo,
    },
    {
      icon: RotateCcw,
      label: 'Start again',
      note: 'Throw this vault away and get an empty one.',
      run: onReset,
    },
  ]

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="bottom">
        <SheetHeader>
          <SheetTitle>More</SheetTitle>
          <SheetDescription>Everything that is not one of the four tabs.</SheetDescription>
        </SheetHeader>
        <SheetBody>
          <ul className="flex flex-col">
            {rows.map(({ icon: Icon, label, note, run, disabled }) => (
              <li key={label}>
                <button
                  type="button"
                  onClick={run}
                  disabled={disabled}
                  className="flex min-h-14 w-full items-center gap-3 rounded-md px-2 py-2 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50 active:bg-accent"
                >
                  <Icon className="size-5 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium">{label}</span>
                    <span className="block text-[12px] leading-snug text-muted-foreground break-anywhere">
                      {note}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </SheetBody>
      </SheetContent>
    </Sheet>
  )
}

function count(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? '' : 's'}`
}

// ── the first run ────────────────────────────────────────────────────────────

/**
 * The product, in one screen.
 *
 * A freshly seeded vault fails its own check, on purpose: the starter ships
 * invented cover figures and a citation pointing at example.com, and the rule
 * refuses to let that build. Every instinct says to hide that from somebody who
 * has been here four seconds. Do not. It is the most honest possible
 * introduction to what this thing is for, it costs nothing because it was going
 * to happen anyway, and a tool that quietly tolerated the placeholders on the
 * first run would have taught the wrong lesson in the first thirty seconds.
 *
 * Nothing here is computed. The findings are `check --json`'s own, in its own
 * words, with its own codes.
 */
function FirstRun({
  check,
  checking,
  error,
  reportId,
  explainer,
  onStart,
  onSeeFindings,
}: {
  check: CheckResult | null
  checking: boolean
  error: string | null
  reportId: string | null
  /** The server's own sentence about why a fresh vault is red. Preferred. */
  explainer: string | null
  onStart: () => void
  onSeeFindings: () => void
}) {
  const findings = check?.findings ?? []
  const errors = check?.errors ?? 0

  return (
    <div className="safe-t safe-x safe-b flex h-full min-h-0 flex-col">
      <div className="pane flex-1">
        <div className="mx-auto w-full max-w-2xl px-4 py-6 lg:py-10">
          <p className="text-[11px] font-medium tracking-widest text-muted-foreground uppercase">
            report-maker
          </p>
          <h1 className="mt-2 text-xl leading-tight font-semibold lg:text-2xl">
            Something is either cited, or it is an opinion.
          </h1>

          {/* The server ships this sentence with the session. Preferring it
              means the explanation and the thing being explained can never
              drift apart — the starter changes, and the copy changes with it. */}
          <p className="mt-4 text-sm leading-relaxed break-anywhere whitespace-pre-line text-muted-foreground">
            {explainer ??
              'You have a vault and a report in it already — but that report is unedited scaffolding: its cover figures are invented and its one citation points at example.com. Placeholders, all of them. The rule is the whole product, so the tool refuses to call this a report until you replace them, and it says exactly where.'}
          </p>

          <div className="mt-6 flex flex-wrap items-center gap-2">
            {checking ? (
              <Skeleton className="h-6 w-40" />
            ) : error ? (
              <Badge variant="error">
                <AlertCircle aria-hidden />
                {error}
              </Badge>
            ) : (
              <>
                <Badge variant="error">
                  <FileWarning aria-hidden />
                  {count(errors, 'error')}
                </Badge>
                {check && check.warnings > 0 && (
                  <Badge variant="warning">{count(check.warnings, 'warning')}</Badge>
                )}
                {reportId && (
                  <span className="truncate font-mono text-[11px] text-muted-foreground">
                    {reportId}
                  </span>
                )}
              </>
            )}
          </div>

          <Separator className="my-5" />

          {checking ? (
            <div className="flex flex-col gap-2">
              {[0, 1, 2, 3, 4].map((n) => (
                <Skeleton key={n} className="h-12 w-full" />
              ))}
            </div>
          ) : findings.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {error
                ? 'The check could not be run. Everything else still works — try it again from the Evidence tab.'
                : 'Nothing to report. That is unusual for a fresh vault; the starter normally fails until you edit it.'}
            </p>
          ) : (
            <FindingList findings={findings} />
          )}
        </div>
      </div>

      {/* The action sits on the bottom edge where the thumb is, above the home
          indicator, and does not float over anything. */}
      <div className="shrink-0 border-t bg-background px-4 py-3">
        <div className="mx-auto flex w-full max-w-2xl flex-col gap-2 lg:flex-row lg:justify-end">
          {findings.length > 0 && (
            <Button variant="outline" size="lg" onClick={onSeeFindings} className="w-full lg:w-auto">
              Work through the findings
            </Button>
          )}
          <Button size="lg" onClick={onStart} className="w-full lg:w-auto">
            Start writing
          </Button>
        </div>
      </div>
    </div>
  )
}

/**
 * Findings, in the engine's own words. Grouped by code, because seventeen E012s
 * are one problem — the starter — and reading them as seventeen problems is a
 * misreading the list itself can prevent.
 */
export function FindingList({ findings }: { findings: Finding[] }) {
  const groups = new Map<string, Finding[]>()
  for (const finding of findings) {
    const list = groups.get(finding.code)
    if (list) list.push(finding)
    else groups.set(finding.code, [finding])
  }

  return (
    <ul className="flex flex-col gap-3">
      {[...groups.entries()].map(([code, items]) => (
        <li key={code} className="rounded-lg border">
          <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
            <Badge variant={items[0].level === 'error' ? 'error' : 'warning'}>{code}</Badge>
            <span className="text-xs text-muted-foreground">
              {count(items.length, items[0].level)}
            </span>
          </div>
          <ul className="divide-y">
            {items.map((finding, index) => (
              <li key={`${finding.path}:${finding.line}:${index}`} className="px-3 py-2">
                <p className="text-sm leading-snug break-anywhere">{finding.message}</p>
                <p className="mt-1 font-mono text-[11px] text-muted-foreground break-anywhere">
                  {finding.path}:{finding.line}
                </p>
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  )
}

// ── the states before there is an app ────────────────────────────────────────

function Seeding({ step }: { step: string }) {
  return (
    <div className="safe-t safe-b safe-x flex h-full flex-col items-center justify-center gap-4 px-6 text-center">
      <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden />
      <div>
        <p className="text-sm font-medium" role="status" aria-live="polite">
          {step}
        </p>
        <p className="mt-1 max-w-xs text-xs text-muted-foreground">
          No account, no sign-in. A vault is being made for this browser and a
          first report scaffolded into it.
        </p>
      </div>
    </div>
  )
}

function Unreachable({
  message,
  detail,
  onRetry,
}: {
  message: string
  detail: string | null
  onRetry: () => void
}) {
  return (
    <div className="safe-t safe-b safe-x flex h-full flex-col items-center justify-center gap-4 px-6">
      <div className="w-full max-w-md">
        <h1 className="text-base font-semibold">No vault could be made.</h1>
        <p className="mt-2 text-sm text-muted-foreground break-anywhere">{message}</p>
        {detail && (
          // The engine's refusals name the command that fixes them. Showing the
          // detail verbatim is the only way that survives to the person reading.
          <pre className="scroll-x mt-3 max-h-56 overflow-y-auto rounded-md border bg-muted p-3 font-mono text-[11px] whitespace-pre">
            {detail}
          </pre>
        )}
        <Button size="lg" className="mt-4 w-full lg:w-auto" onClick={onRetry}>
          Try again
        </Button>
      </div>
    </div>
  )
}

// ── the desktop status bar ───────────────────────────────────────────────────

function StatusBar({
  check,
  checking,
  building,
}: {
  check: CheckResult | null
  checking: boolean
  building: boolean
}) {
  const score = check?.score
  return (
    <div className="flex min-h-8 items-center gap-3 px-4 text-[11px] text-muted-foreground">
      {checking ? (
        <span>Checking…</span>
      ) : check ? (
        <>
          <span className={cn(check.errors > 0 && 'text-destructive')}>
            {count(check.errors, 'error')}
          </span>
          <span>{count(check.warnings, 'warning')}</span>
        </>
      ) : (
        <span>Not checked</span>
      )}
      {score && (
        <>
          <Separator orientation="vertical" className="h-3" />
          <span className="tabular-nums">
            {Math.round(score.density * 100)}% cited or assessed
          </span>
          <span
            className="flex h-1.5 w-24 overflow-hidden rounded-full bg-rail-neutral"
            aria-hidden
          >
            <Meter value={score.cited} total={score.cited + score.assessed + score.unmarked} className="rail-cited" />
            <Meter value={score.assessed} total={score.cited + score.assessed + score.unmarked} className="rail-assessed" />
            <Meter value={score.unmarked} total={score.cited + score.assessed + score.unmarked} className="rail-unmarked" />
          </span>
        </>
      )}
      {building && <span className="ml-auto">Building…</span>}
    </div>
  )
}

function Meter({
  value,
  total,
  className,
}: {
  value: number
  total: number
  className: string
}) {
  if (total <= 0) return null
  return <span className={className} style={{ width: `${(value / total) * 100}%` }} />
}
