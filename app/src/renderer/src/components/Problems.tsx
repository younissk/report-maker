import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Archive,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  CircleSlash,
  FileText,
  Loader2,
  RefreshCw,
  Terminal,
  TriangleAlert,
  WifiOff,
  X
} from 'lucide-react'
import type { Drift, Finding, Run } from '../../../shared/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { parseRun, type BuildEntry } from '@/lib/buildlog'
import { cn } from '@/lib/utils'

export type ProblemsTab = 'findings' | 'build' | 'evidence'

type Props = {
  /** `check --json` findings, exactly as the engine printed them. */
  findings: Finding[]
  /** The last engine run, whatever it was; its output is parsed for the Build tab. */
  run: Run | null
  /** `verify --json` drift rows. */
  drifts: Drift[]
  /** Open a vault-relative path and put the cursor on that line. */
  onReveal: (path: string, line: number) => void
  onVerify: () => void
  open: boolean
  onOpenChange: (open: boolean) => void
  /** An engine command is in flight — disables the actions and spins the button. */
  busy?: boolean
  /** Optional controlled tab, so a gutter marker or the palette can open the
   *  drawer straight onto the tab it means. Uncontrolled when omitted. */
  tab?: ProblemsTab
  onTabChange?: (tab: ProblemsTab) => void
  /** Reveal one finding: switches to Findings, expands its file, scrolls to the
   *  row and rings it. B1's lint gutter uses this. */
  focus?: { path: string; line: number } | null
}

const MIN_HEIGHT = 120

/** The drawer may take most of the window, but never all of it — an editor you
 *  cannot see is not a trade anybody wants to make by dragging too far. */
function clampHeight(height: number): number {
  return Math.max(MIN_HEIGHT, Math.min(height, Math.round(window.innerHeight * 0.8)))
}

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? '' : 's'}`
}

/**
 * Severity by shape, not by hue.
 *
 * The chrome's palette is deliberately near-neutral so it does not compete with
 * the report's own brand, and there is no warning token to reach for. An error
 * is destructive-coloured; a warning is told apart by its triangle.
 */
function Severity({ level }: { level: Finding['level'] }): React.JSX.Element {
  return level === 'error' ? (
    <CircleAlert className="mt-px size-3.5 shrink-0 text-destructive" />
  ) : (
    <TriangleAlert className="mt-px size-3.5 shrink-0 text-muted-foreground" />
  )
}

function Empty({ children }: { children: React.ReactNode }): React.JSX.Element {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-1.5 px-6 text-center text-xs text-muted-foreground">
      {children}
    </div>
  )
}

/** A collapsible run of rows under one heading — one file, or one report. */
function Group({
  label,
  icon,
  right,
  collapsed,
  onToggle,
  children
}: {
  label: string
  icon?: React.ReactNode
  right?: React.ReactNode
  collapsed: boolean
  onToggle: () => void
  children: React.ReactNode
}): React.JSX.Element {
  const Chevron = collapsed ? ChevronRight : ChevronDown
  return (
    <div className="border-b border-border last:border-b-0">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={!collapsed}
        className="flex w-full items-center gap-1.5 px-2 py-1 text-left hover:bg-accent"
      >
        <Chevron className="size-3 shrink-0 text-muted-foreground" />
        {icon}
        <span className="truncate font-mono text-[11.5px]">{label}</span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5">{right}</span>
      </button>
      {!collapsed && <div className="pb-1">{children}</div>}
    </div>
  )
}

// ── Findings ────────────────────────────────────────────────────────────────

function FindingsTab({
  findings,
  onReveal,
  focusKey
}: {
  findings: Finding[]
  onReveal: (path: string, line: number) => void
  focusKey: string | null
}): React.JSX.Element {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const rows = useRef(new Map<string, HTMLButtonElement | null>())

  const groups = useMemo(() => {
    // Groups keep the order the engine printed them; rows inside a file read
    // best top to bottom, which is the order you would scroll the file in.
    const byPath = new Map<string, Finding[]>()
    for (const finding of findings) {
      const existing = byPath.get(finding.path)
      if (existing) existing.push(finding)
      else byPath.set(finding.path, [finding])
    }
    return [...byPath.entries()].map(([path, list]) => ({
      path,
      rows: [...list].sort((a, b) => a.line - b.line),
      errors: list.filter((finding) => finding.level === 'error').length
    }))
  }, [findings])

  useEffect(() => {
    if (!focusKey) return
    const target = findings.find((finding) => `${finding.path}:${finding.line}` === focusKey)
    if (!target) return
    setCollapsed((current) => {
      if (!current.has(target.path)) return current
      const next = new Set(current)
      next.delete(target.path)
      return next
    })
    // Once the expand has painted, bring the row into view.
    const frame = requestAnimationFrame(() =>
      rows.current.get(focusKey)?.scrollIntoView({ block: 'nearest' })
    )
    return () => cancelAnimationFrame(frame)
  }, [focusKey, findings])

  if (findings.length === 0) {
    return (
      <Empty>
        <CircleCheck className="size-4" />
        {/* The CLI's own words for a clean run, so the app and the terminal agree. */}
        <span>cited or opinion — no findings</span>
      </Empty>
    )
  }

  return (
    <ScrollArea className="h-full">
      {groups.map((group) => (
        <Group
          key={group.path}
          label={group.path}
          icon={<FileText className="size-3.5 shrink-0 text-muted-foreground" />}
          collapsed={collapsed.has(group.path)}
          onToggle={() =>
            setCollapsed((current) => {
              const next = new Set(current)
              if (next.has(group.path)) next.delete(group.path)
              else next.add(group.path)
              return next
            })
          }
          right={
            <>
              {/* Collapsed, the group has to say whether it is hiding an error.
                  Expanded, every row already says so — repeating it is noise. */}
              {collapsed.has(group.path) && group.errors > 0 && (
                <span className="flex items-center gap-0.5 text-[11px] text-destructive">
                  <CircleAlert className="size-3" />
                  {group.errors}
                </span>
              )}
              <span className="text-[10px] text-muted-foreground">{group.rows.length}</span>
            </>
          }
        >
          {group.rows.map((finding, index) => {
            const key = `${finding.path}:${finding.line}`
            const ringed = key === focusKey
            return (
              <button
                key={`${key}-${finding.code}-${index}`}
                type="button"
                ref={(element) => {
                  rows.current.set(key, element)
                }}
                onClick={() => onReveal(finding.path, finding.line)}
                title={`${finding.path}:${finding.line}`}
                className={cn(
                  'flex w-full items-start gap-2 px-2 py-1 pl-7 text-left hover:bg-accent',
                  ringed && 'bg-accent ring-1 ring-ring ring-inset'
                )}
              >
                <Severity level={finding.level} />
                <Badge
                  variant="outline"
                  className="h-4 shrink-0 rounded px-1 font-mono text-[10px] font-normal"
                >
                  {finding.code}
                </Badge>
                <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                  :{finding.line}
                </span>
                {/* Wrapped, never truncated: the message is the whole point. */}
                <span className="min-w-0 flex-1 text-[12px] leading-4">{finding.message}</span>
              </button>
            )
          })}
        </Group>
      ))}
    </ScrollArea>
  )
}

// ── Build ───────────────────────────────────────────────────────────────────

function BuildRow({
  entry,
  onReveal
}: {
  entry: BuildEntry
  onReveal: (path: string, line: number) => void
}): React.JSX.Element {
  const detail = entry.raw.slice(1)
  const located = entry.path !== null && entry.line !== null
  return (
    <div className="border-b border-border px-2 py-1 last:border-b-0">
      <div className="flex items-start gap-2">
        {entry.level === 'error' ? (
          <CircleAlert className="mt-px size-3.5 shrink-0 text-destructive" />
        ) : entry.level === 'warning' ? (
          <TriangleAlert className="mt-px size-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <Terminal className="mt-px size-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="min-w-0 flex-1 text-[12px] leading-4">{entry.message}</span>
        {located && (
          <button
            type="button"
            onClick={() => onReveal(entry.path as string, entry.line as number)}
            className="shrink-0 font-mono text-[11px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          >
            {entry.path}:{entry.line}
            {entry.col !== null && `:${entry.col}`}
          </button>
        )}
      </div>
      {detail.length > 0 && (
        <pre className="mt-0.5 overflow-x-auto pl-5 font-mono text-[11px] leading-[1.35] whitespace-pre text-muted-foreground">
          {detail.join('\n')}
        </pre>
      )}
    </div>
  )
}

function BuildTab({
  run,
  onReveal
}: {
  run: Run | null
  onReveal: (path: string, line: number) => void
}): React.JSX.Element {
  const log = useMemo(() => parseRun(run), [run])

  if (!run) {
    return (
      <Empty>
        <Terminal className="size-4" />
        <span>
          Nothing built yet — press <kbd className="font-mono">⌘B</kbd>.
        </span>
      </Empty>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-6 shrink-0 items-center gap-2 border-b border-border px-2 text-[11px] text-muted-foreground">
        <Terminal className="size-3 shrink-0" />
        <span className="truncate font-mono" title={run.command}>
          {run.command}
        </span>
        <Badge
          variant={run.code === 0 ? 'secondary' : 'destructive'}
          className="ml-auto h-4 shrink-0 rounded px-1 font-mono text-[10px] font-normal"
        >
          exit {run.code}
        </Badge>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        {log.blocks.length === 0 ? (
          <p className="px-2 py-1.5 text-[11.5px] text-muted-foreground">
            The command printed nothing.
          </p>
        ) : (
          log.blocks.map((block, index) =>
            block.kind === 'entry' ? (
              <BuildRow key={index} entry={block} onReveal={onReveal} />
            ) : (
              // Unparsed output is shown exactly as it came out. The panel is a
              // reader for the CLI, not a replacement for reading it.
              <pre
                key={index}
                className="overflow-x-auto px-2 py-1 font-mono text-[11px] leading-[1.35] whitespace-pre text-muted-foreground"
              >
                {block.lines.join('\n')}
              </pre>
            )
          )
        )}
      </ScrollArea>
    </div>
  )
}

// ── Evidence ────────────────────────────────────────────────────────────────

/** How each drift state reads. Two of them are failures, the rest are states of
 *  the archive rather than of the claim. */
const DRIFT: Record<Drift['state'], { icon: React.ElementType; variant: 'secondary' | 'outline' | 'destructive' }> = {
  ok: { icon: CircleCheck, variant: 'secondary' },
  changed: { icon: TriangleAlert, variant: 'outline' },
  gone: { icon: CircleSlash, variant: 'destructive' },
  error: { icon: CircleAlert, variant: 'destructive' },
  unsnapshotted: { icon: Archive, variant: 'outline' },
  offline: { icon: WifiOff, variant: 'outline' }
}

function DriftRow({ drift }: { drift: Drift }): React.JSX.Element {
  const state = DRIFT[drift.state] ?? DRIFT.error
  const Icon = state.icon
  return (
    <div className="flex items-start gap-2 px-2 py-1 pl-7">
      {/* The chip sizes to its word, inside a fixed column, so the keys line up
          down the list however long the state's name happens to be. */}
      <span className="w-[6.5rem] shrink-0">
        <Badge variant={state.variant} className="h-4 gap-1 rounded px-1 text-[10px] font-normal">
          <Icon />
          {drift.state}
        </Badge>
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-mono text-[11.5px]">{drift.key}</span>
          {drift.similarity !== null && (
            <span className="text-[11px] text-muted-foreground">
              {Math.round(drift.similarity * 100)}% of the archived text still matches
            </span>
          )}
          {drift.fetched && (
            <span className="text-[11px] text-muted-foreground">
              archived {drift.fetched.slice(0, 10)}
            </span>
          )}
        </div>
        {drift.url && (
          <div className="truncate font-mono text-[11px] text-muted-foreground" title={drift.url}>
            {drift.url}
          </div>
        )}
        {drift.detail && <div className="text-[11.5px] text-muted-foreground">{drift.detail}</div>}
      </div>
    </div>
  )
}

function EvidenceTab({ drifts }: { drifts: Drift[] }): React.JSX.Element {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())

  const groups = useMemo(() => {
    const byReport = new Map<string, Drift[]>()
    for (const drift of drifts) {
      const existing = byReport.get(drift.report)
      if (existing) existing.push(drift)
      else byReport.set(drift.report, [drift])
    }
    return [...byReport.entries()].map(([report, rows]) => ({
      report,
      rows,
      unsettled: rows.filter((drift) => drift.state !== 'ok').length
    }))
  }, [drifts])

  if (drifts.length === 0) {
    return (
      <Empty>
        <Archive className="size-4" />
        <span>No sources checked yet.</span>
        <span>
          Re-verify fetches every archived page again and reports what has moved
          underneath the report.
        </span>
      </Empty>
    )
  }

  return (
    <ScrollArea className="h-full">
      {groups.map((group) => (
        <Group
          key={group.report}
          label={group.report}
          icon={<Archive className="size-3.5 shrink-0 text-muted-foreground" />}
          collapsed={collapsed.has(group.report)}
          onToggle={() =>
            setCollapsed((current) => {
              const next = new Set(current)
              if (next.has(group.report)) next.delete(group.report)
              else next.add(group.report)
              return next
            })
          }
          right={
            <>
              {collapsed.has(group.report) && group.unsettled > 0 && (
                <span className="text-[11px] text-muted-foreground">{group.unsettled} moved</span>
              )}
              <span className="text-[10px] text-muted-foreground">{group.rows.length}</span>
            </>
          }
        >
          {group.rows.map((drift) => (
            <DriftRow key={`${drift.report}/${drift.key}`} drift={drift} />
          ))}
        </Group>
      ))}
    </ScrollArea>
  )
}

// ── The drawer ──────────────────────────────────────────────────────────────

/**
 * Everything the engine said, in full.
 *
 * The header can only ever show a status line; this is where the output actually
 * lives — the citation-rule findings, the last build's diagnostics, and the state
 * of the archived evidence. It holds no opinion of its own about any of them: the
 * findings come from `check --json`, the drift from `verify --json`, and the
 * build tab is a reader for what the compiler printed.
 */
export function Problems({
  findings,
  run,
  drifts,
  onReveal,
  onVerify,
  open,
  onOpenChange,
  busy = false,
  tab,
  onTabChange,
  focus
}: Props): React.JSX.Element | null {
  const [uncontrolled, setUncontrolled] = useState<ProblemsTab>('findings')
  const [height, setHeight] = useState(260)
  const drag = useRef<{ y: number; height: number } | null>(null)

  const active = tab ?? uncontrolled

  const setTab = useCallback(
    (next: ProblemsTab) => {
      setUncontrolled(next)
      onTabChange?.(next)
    },
    [onTabChange]
  )

  // A string key rather than the object: the parent may hand us a fresh object on
  // every render, and re-scrolling on every keystroke would fight the reader.
  const focusKey = focus ? `${focus.path}:${focus.line}` : null
  useEffect(() => {
    if (focusKey) setTab('findings')
  }, [focusKey, setTab])

  useEffect(() => {
    const onResize = (): void => setHeight((current) => clampHeight(current))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  if (!open) return null

  const errors = findings.filter((finding) => finding.level === 'error').length
  const build = parseRun(run)
  const moved = drifts.filter((drift) => drift.state !== 'ok').length

  return (
    <section
      aria-label="Problems"
      className="flex shrink-0 flex-col border-t border-border bg-background"
      style={{ height }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') onOpenChange(false)
      }}
    >
      {/* The drawer resizes itself rather than living in a panel group, so it can
          be dropped into the shell without the layout above it knowing. The handle
          straddles the border the way an editor's sash does, so the grab target is
          bigger than the line it appears to be. */}
      <div
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize the problems panel"
        tabIndex={0}
        className="-mt-px h-1.5 shrink-0 cursor-row-resize touch-none select-none outline-none hover:bg-ring/40 focus-visible:bg-ring/60"
        onPointerDown={(event) => {
          event.preventDefault()
          event.currentTarget.setPointerCapture(event.pointerId)
          drag.current = { y: event.clientY, height }
        }}
        onPointerMove={(event) => {
          if (!drag.current) return
          setHeight(clampHeight(drag.current.height + (drag.current.y - event.clientY)))
        }}
        onPointerUp={(event) => {
          drag.current = null
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId)
          }
        }}
        onPointerCancel={() => {
          drag.current = null
        }}
        onKeyDown={(event) => {
          if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
          event.preventDefault()
          setHeight((current) => clampHeight(current + (event.key === 'ArrowUp' ? 24 : -24)))
        }}
      />

      <Tabs
        value={active}
        onValueChange={(value) => setTab(value as ProblemsTab)}
        className="flex min-h-0 flex-1 flex-col gap-0"
      >
        <div className="flex h-8 shrink-0 items-center gap-2 px-2">
          <TabsList className="h-6 gap-0.5 p-0.5">
            <TabsTrigger value="findings" className="h-5 gap-1.5 px-2 text-[11px]">
              Findings
              {findings.length > 0 && (
                <span className={cn('text-[10px]', errors > 0 ? 'text-destructive' : 'text-muted-foreground')}>
                  {findings.length}
                </span>
              )}
            </TabsTrigger>
            <TabsTrigger value="build" className="h-5 gap-1.5 px-2 text-[11px]">
              Build
              {build.errors > 0 && <span className="text-[10px] text-destructive">{build.errors}</span>}
            </TabsTrigger>
            <TabsTrigger value="evidence" className="h-5 gap-1.5 px-2 text-[11px]">
              Evidence
              {moved > 0 && <span className="text-[10px] text-muted-foreground">{moved}</span>}
            </TabsTrigger>
          </TabsList>

          <div className="ml-auto flex items-center gap-1">
            {active === 'evidence' && (
              <Button
                variant="ghost"
                size="xs"
                className="gap-1.5 text-[11px]"
                disabled={busy}
                onClick={onVerify}
              >
                {busy ? (
                  <Loader2 className="size-3 animate-spin" />
                ) : (
                  <RefreshCw className="size-3" />
                )}
                Re-verify
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon-xs"
              title="Close the problems panel"
              onClick={() => onOpenChange(false)}
            >
              <X />
            </Button>
          </div>
        </div>
        <Separator />

        <TabsContent value="findings" className="min-h-0 flex-1 overflow-hidden">
          <FindingsTab findings={findings} onReveal={onReveal} focusKey={focusKey} />
        </TabsContent>
        <TabsContent value="build" className="min-h-0 flex-1 overflow-hidden">
          <BuildTab run={run} onReveal={onReveal} />
        </TabsContent>
        <TabsContent value="evidence" className="min-h-0 flex-1 overflow-hidden">
          <EvidenceTab drifts={drifts} />
        </TabsContent>
      </Tabs>
    </section>
  )
}

// ── The status-bar chip ─────────────────────────────────────────────────────

type ChipProps = {
  findings: Finding[]
  open: boolean
  onToggle: () => void
  /** A check is running — the counts on screen are the previous ones. */
  busy?: boolean
  className?: string
}

/**
 * `n errors · m warnings`, coloured by the worst level it is counting, and the
 * handle for the drawer. Clean reads in the CLI's own words on hover.
 */
export function ProblemsChip({
  findings,
  open,
  onToggle,
  busy = false,
  className
}: ChipProps): React.JSX.Element {
  const errors = findings.filter((finding) => finding.level === 'error').length
  const warnings = findings.length - errors
  const clean = errors === 0 && warnings === 0
  const Icon = errors > 0 ? CircleAlert : warnings > 0 ? TriangleAlert : CircleCheck

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={open}
      title={clean ? 'cited or opinion — no findings' : 'Show the problems panel'}
      className={cn(
        'inline-flex h-5 items-center gap-1 rounded-sm px-1.5 text-[11px] hover:bg-accent',
        errors > 0 && 'text-destructive',
        errors === 0 && warnings > 0 && 'text-foreground',
        clean && 'text-muted-foreground',
        open && 'bg-accent',
        className
      )}
    >
      {busy ? <Loader2 className="size-3 animate-spin" /> : <Icon className="size-3" />}
      {clean ? 'no findings' : `${plural(errors, 'error')} · ${plural(warnings, 'warning')}`}
    </button>
  )
}
