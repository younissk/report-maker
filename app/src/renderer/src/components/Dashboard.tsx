import { useEffect, useMemo, useState } from 'react'
import {
  ChevronRight,
  CircleCheck,
  CircleDashed,
  Clock,
  Folder,
  FolderTree,
  ListTodo,
  Plus,
  Search,
  TriangleAlert
} from 'lucide-react'
import type { CheckResult, ReportRow, ReportScore, ScoreResult } from '../../../shared/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { useVaultTodos } from '@/lib/notes'
import { cn } from '@/lib/utils'
import { useThumb } from '@/lib/thumbs'

/**
 * The vault, seen whole.
 *
 * A file tree answers "where is this file"; a writer opening the app wants to
 * know which report is weakest, which is stale, and which one they were last
 * working on. That is a different question, and it is answered entirely by the
 * engine: `list --json` for the rows, `out/manifest.json` for what has been
 * built, `score --json` for evidence density, `check --json` for findings. Three
 * spawns for the whole vault — never one per card, which for eighty reports
 * would be eighty processes to draw one screen.
 */

type Sort = 'date' | 'title' | 'density' | 'stale'

/**
 * `list --json` spreads a report's own metadata into each row, so a row carries
 * more than the shared type names. Widened here rather than in shared/types.ts
 * because these are display niceties: a vault whose reports omit them must
 * degrade, not fail.
 */
type Row = ReportRow & { subtitle?: string; author?: string; 'date-display'?: string }

/**
 * The part of `out/manifest.json` this screen reads — the design titles, so a
 * card can say "Base report" where the report's import line says `base`.
 * Declared here rather than in the IPC vocabulary because it is a file on disk
 * that `engine.manifest` hands back verbatim, and narrowed to what is used so
 * nobody has to keep an unread transcription of the file in step with it.
 */
type Manifest = {
  templates: Record<string, { title: string; description: string; builtin: boolean }>
}

type Props = {
  vault: string
  /** `list --json`, already loaded by the shell — the dashboard does not spawn
   *  a second copy of a command the window has just run. */
  reports: ReportRow[]
  /** Open a report: the shell resolves the id to its `main.typ`. */
  onOpen: (reportId: string) => void
  /** Raise the new-report dialog. Omit it and the empty state just explains. */
  onNew?: () => void
  /** Bumped by the shell after a build, to reload thumbnails, scores and findings. */
  revision?: number
  className?: string
}

type Tally = { errors: number; warnings: number }
type Counts = { cited: number; assessed: number; unmarked: number }

/**
 * `score --json` as `engine/score.py` prints it: the per-report rows the shared
 * `ScoreResult` names, and the vault's own totals alongside them. The totals are
 * optional here so an engine that predates them degrades to a dash rather than
 * to a wrong number — and so this file stays compilable while `ScoreResult`
 * catches up. Only `reports` is taken from the shared type, because that is the
 * only field of it the dashboard reads.
 */
type VaultScore = Pick<ScoreResult, 'reports'> &
  Partial<Counts & { density: number; sourcesTotal: number; sourcesCited: number }>

// ── reading the vault ────────────────────────────────────────────────────────

/**
 * `check --json` exits non-zero when the vault has errors, which is the ordinary
 * state of a vault being worked on. `engine.json` would turn that into a thrown
 * error and lose the findings with it, so read stdout directly.
 */
async function readCheck(vault: string): Promise<CheckResult | null> {
  try {
    const run = await window.api.engine.run(vault, ['check', '--json'])
    return JSON.parse(run.stdout) as CheckResult
  } catch {
    return null
  }
}

function basename(path: string): string {
  const parts = path.replace(/\/$/, '').split('/')
  return parts[parts.length - 1] || path
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

/** Unbuilt first, then stale, then built — the order of what needs attention. */
function staleness(row: Row): number {
  if (!row.built) return 2
  return row.stale ? 1 : 0
}

// ── pieces ───────────────────────────────────────────────────────────────────

/**
 * Cited / assessed / unmarked, in proportion. Three segments rather than one
 * percentage because the citation rule has three answers, and a report that is
 * 80% assessment reads very differently from one that is 80% cited.
 */
function EvidenceBar({
  counts,
  className
}: {
  counts: Counts
  className?: string
}) {
  const total = counts.cited + counts.assessed + counts.unmarked
  const width = (n: number): string => `${total > 0 ? (n / total) * 100 : 0}%`

  return (
    <div
      className={cn('flex h-1 w-full overflow-hidden rounded-full bg-muted', className)}
      title={`${counts.cited} cited · ${counts.assessed} assessed · ${counts.unmarked} unmarked`}
    >
      <div className="bg-foreground" style={{ width: width(counts.cited) }} />
      <div className="bg-muted-foreground" style={{ width: width(counts.assessed) }} />
      <div className="bg-destructive/70" style={{ width: width(counts.unmarked) }} />
    </div>
  )
}

function Stat({ label, value }: { label: string; value: number | string }): React.JSX.Element {
  return (
    <div className="text-right">
      <div className="font-mono text-sm tabular-nums">{value}</div>
      <div className="text-[10px] tracking-widest text-muted-foreground uppercase">{label}</div>
    </div>
  )
}

/** The report's folder, which is also its filing system. */
function Breadcrumb({ group }: { group: string }) {
  const parts = group ? group.split('/') : []
  return (
    <div className="flex min-w-0 items-center gap-0.5 text-[10px] text-muted-foreground">
      <Folder className="size-2.5 shrink-0" />
      {parts.length === 0 ? (
        <span className="truncate">top level</span>
      ) : (
        parts.map((part, index) => (
          <span key={`${part}-${index}`} className="flex min-w-0 items-center gap-0.5">
            {index > 0 && <ChevronRight className="size-2.5 shrink-0" />}
            <span className="truncate">{part}</span>
          </span>
        ))
      )}
    </div>
  )
}

function StateChip({ row }: { row: Row }) {
  if (!row.built) {
    return (
      <Badge variant="outline" className="gap-1 px-1.5 text-[10px] font-normal text-muted-foreground">
        <CircleDashed />
        unbuilt
      </Badge>
    )
  }
  if (row.stale) {
    return (
      <Badge variant="secondary" className="gap-1 px-1.5 text-[10px] font-normal">
        <Clock />
        stale
      </Badge>
    )
  }
  return (
    <Badge variant="outline" className="gap-1 px-1.5 text-[10px] font-normal text-muted-foreground">
      <CircleCheck />
      built
    </Badge>
  )
}

function FindingsChip({ tally }: { tally: Tally | undefined }) {
  // No chip at all when check has not run: silence is honest, a green chip is not.
  if (!tally) return null
  if (tally.errors > 0) {
    return (
      <Badge variant="destructive" className="gap-1 px-1.5 text-[10px] font-normal">
        <TriangleAlert />
        {plural(tally.errors, 'error')}
      </Badge>
    )
  }
  if (tally.warnings > 0) {
    return (
      <Badge variant="secondary" className="gap-1 px-1.5 text-[10px] font-normal">
        <TriangleAlert />
        {plural(tally.warnings, 'warning')}
      </Badge>
    )
  }
  return (
    <Badge variant="outline" className="gap-1 px-1.5 text-[10px] font-normal text-muted-foreground">
      <CircleCheck />
      clean
    </Badge>
  )
}

/**
 * The cover. A built report shows its own first page; an unbuilt one gets a
 * typographic stand-in rather than a grey box, so the grid still reads as a
 * shelf of documents before anything has been compiled.
 */
function Cover({ vault, row, revision }: { vault: string; row: Row; revision: number }) {
  // An unbuilt report has no pages directory, so do not go and look for one.
  const { url, loading } = useThumb(vault, row.built ? row.id : null, revision)

  if (url) {
    return <img src={url} alt="" className="h-full w-full object-cover object-top" />
  }
  if (loading) {
    return <Skeleton className="h-full w-full rounded-none" />
  }
  return (
    <div className="flex h-full flex-col justify-between p-3">
      <span className="font-mono text-[9px] tracking-[0.14em] text-muted-foreground uppercase">
        {row.kind || 'Report'}
      </span>
      <span className="line-clamp-4 text-[13px] leading-snug font-medium text-muted-foreground">
        {row.title || basename(row.id)}
      </span>
      <span className="text-[10px] text-muted-foreground">not built yet</span>
    </div>
  )
}

/**
 * How many tasks are still open on this report's pad.
 *
 * Only ever shown when there are some: a card that says "0 open" is a card
 * asserting something about a file that may not exist. The count is the
 * engine's, out of one `todos --json` for the whole vault — see
 * {@link useVaultTodos}.
 */
function TodosChip({ open }: { open: number }) {
  if (open <= 0) return null
  return (
    <Badge
      variant="outline"
      className="gap-1 px-1.5 text-[10px] font-normal text-muted-foreground"
      title={`${plural(open, 'task')} on this report's pad — todos.md, notes.md and // TODO: in the source`}
    >
      <ListTodo />
      {open} open
    </Badge>
  )
}

function ReportCard({
  vault,
  row,
  score,
  tally,
  todos,
  design,
  revision,
  onOpen
}: {
  vault: string
  row: Row
  score: ReportScore | undefined
  tally: Tally | undefined
  /** Open tasks on the report's pad. Zero when the pad is empty or absent. */
  todos: number
  design: string | undefined
  revision: number
  onOpen: (reportId: string) => void
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(row.id)}
      title={row.id}
      className="group rounded-lg text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
    >
      <Card className="gap-0 overflow-hidden py-0 transition-colors group-hover:border-ring">
        <div className="aspect-[1/1.414] w-full overflow-hidden border-b border-border bg-muted/40">
          <Cover vault={vault} row={row} revision={revision} />
        </div>

        <div className="flex flex-col gap-1.5 px-3 py-2.5">
          <Breadcrumb group={row.group} />
          <span className="truncate text-[13px] leading-tight font-medium">
            {row.title || basename(row.id)}
          </span>

          <div className="flex min-w-0 items-center gap-1.5 text-[10px] text-muted-foreground">
            <span className="shrink-0 font-mono">{row['date-display'] || row.date || '—'}</span>
            <span aria-hidden>·</span>
            <span className="truncate font-mono" title={design || row.template}>
              {row.template}
            </span>
          </div>

          {score && (
            <div className="flex items-center gap-2 pt-0.5">
              <EvidenceBar counts={score} className="flex-1" />
              <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
                {percent(score.density)}
              </span>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-1 pt-0.5">
            <StateChip row={row} />
            <FindingsChip tally={tally} />
            <TodosChip open={todos} />
          </div>
        </div>
      </Card>
    </button>
  )
}

function Grid({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3">{children}</div>
  )
}

// ── the screen ───────────────────────────────────────────────────────────────

export function Dashboard({ vault, reports, onOpen, onNew, revision = 0, className }: Props) {
  const [manifest, setManifest] = useState<Manifest | null>(null)
  const [score, setScore] = useState<VaultScore | null>(null)
  const [check, setCheck] = useState<CheckResult | null>(null)
  const [loading, setLoading] = useState(true)

  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<Sort>('date')
  const [grouped, setGrouped] = useState(false)

  // One read of each, for the whole vault. `score` and `check` are new verbs, so
  // a vault whose engine predates them simply shows no density and no findings
  // chips — the grid is still useful, and a missing command is not an error to
  // put in front of a writer.
  useEffect(() => {
    let live = true
    setLoading(true)
    void Promise.all([
      window.api.engine.manifest<Manifest>(vault).catch(() => null),
      window.api.engine.json<VaultScore>(vault, ['score', '--json']).catch(() => null),
      readCheck(vault)
    ]).then(([loadedManifest, loadedScore, loadedCheck]) => {
      if (!live) return
      setManifest(loadedManifest)
      setScore(loadedScore)
      setCheck(loadedCheck)
      setLoading(false)
    })
    return () => {
      live = false
    }
  }, [vault, revision])

  // The pad, for every card, in one subprocess. `todos --json` with no target
  // already walks the vault, so this is one spawn per dashboard rather than one
  // per card — which for eighty reports would be eighty processes to draw one
  // screen. Reports with nothing on the pad are simply absent from `byId`, and
  // that absence is the answer rather than a gap.
  const { byId: pads } = useVaultTodos(vault, revision)

  const rows = reports as Row[]

  const scores = useMemo(
    () => new Map((score?.reports ?? []).map((entry) => [entry.id, entry])),
    [score]
  )

  const tallies = useMemo(() => {
    const map = new Map<string, Tally>()
    for (const finding of check?.findings ?? []) {
      const tally = map.get(finding.report) ?? { errors: 0, warnings: 0 }
      if (finding.level === 'error') tally.errors += 1
      else tally.warnings += 1
      map.set(finding.report, tally)
    }
    // A report the check visited and had nothing to say about still deserves its
    // "clean" chip, so seed every row once the check has run at all.
    if (check) for (const row of rows) if (!map.has(row.id)) map.set(row.id, { errors: 0, warnings: 0 })
    return map
  }, [check, rows])

  /**
   * The vault's own totals, as `score --json` prints them beside the per-report
   * rows. Deliberately not summed here: a weighted density computed in the
   * renderer would be a second implementation of a rule the engine owns, free to
   * disagree with the CLI the moment either changes.
   */
  const aggregate: Counts = {
    cited: score?.cited ?? 0,
    assessed: score?.assessed ?? 0,
    unmarked: score?.unmarked ?? 0
  }

  const staleCount = useMemo(() => rows.filter((row) => row.stale || !row.built).length, [rows])

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const matched = needle
      ? rows.filter((row) =>
          `${row.title ?? ''} ${row.id} ${row.group}`.toLowerCase().includes(needle)
        )
      : rows

    // Unscored reports sort last under "weakest first": an unknown density is
    // not a good one, but it is not evidence of a problem either.
    const density = (row: Row): number => scores.get(row.id)?.density ?? Number.POSITIVE_INFINITY
    const byId = (a: Row, b: Row): number => a.id.localeCompare(b.id)

    const order: Record<Sort, (a: Row, b: Row) => number> = {
      date: (a, b) => (b.date ?? '').localeCompare(a.date ?? '') || byId(a, b),
      title: (a, b) => (a.title || a.id).localeCompare(b.title || b.id),
      density: (a, b) => density(a) - density(b) || byId(a, b),
      stale: (a, b) => staleness(b) - staleness(a) || byId(a, b)
    }
    return [...matched].sort(order[sort])
  }, [rows, query, sort, scores])

  const sections = useMemo(() => {
    if (!grouped) return null
    const map = new Map<string, Row[]>()
    for (const row of visible) {
      const bucket = map.get(row.group)
      if (bucket) bucket.push(row)
      else map.set(row.group, [row])
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [grouped, visible])

  const card = (row: Row): React.JSX.Element => (
    <ReportCard
      key={row.id}
      vault={vault}
      row={row}
      score={scores.get(row.id)}
      tally={tallies.get(row.id)}
      todos={pads.get(row.id)?.open ?? 0}
      design={manifest?.templates?.[row.template]?.title}
      revision={revision}
      onOpen={onOpen}
    />
  )

  return (
    <div className={cn('flex h-full flex-col', className)}>
      <header className="flex shrink-0 flex-wrap items-end justify-between gap-4 border-b border-border px-5 py-4">
        <div className="min-w-0">
          <h1 className="truncate text-base leading-tight font-medium">{basename(vault)}</h1>
          <p className="truncate font-mono text-[10px] text-muted-foreground">{vault}</p>
        </div>

        <div className="flex items-end gap-5">
          <Stat label="reports" value={rows.length} />
          <Stat label="need building" value={staleCount} />
          <Stat label="sources" value={score?.sourcesTotal ?? '—'} />
          <div className="w-40">
            <div className="flex items-baseline justify-between">
              <span className="text-[10px] tracking-widest text-muted-foreground uppercase">
                evidence
              </span>
              <span className="font-mono text-sm tabular-nums">
                {typeof score?.density === 'number' ? percent(score.density) : '—'}
              </span>
            </div>
            <EvidenceBar
              counts={aggregate}
              className="mt-1.5"
            />
          </div>
        </div>
      </header>

      {/* Filtering and sorting nothing is worse than useless, so an empty vault
          gets the empty state and its one call to action instead. */}
      {rows.length > 0 && (
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-5 py-2.5">
          <div className="relative w-64">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Filter by title, id or folder"
              className="h-8 pl-8 text-xs"
            />
          </div>

          <Select value={sort} onValueChange={(value) => setSort(value as Sort)}>
            <SelectTrigger size="sm" className="w-[188px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="date" className="text-xs">
                Newest first
              </SelectItem>
              <SelectItem value="title" className="text-xs">
                Title A–Z
              </SelectItem>
              <SelectItem value="density" className="text-xs">
                Evidence, weakest first
              </SelectItem>
              <SelectItem value="stale" className="text-xs">
                Needs building first
              </SelectItem>
            </SelectContent>
          </Select>

          <Button
            variant={grouped ? 'secondary' : 'ghost'}
            size="sm"
            className="h-8 gap-1.5 text-xs"
            aria-pressed={grouped}
            onClick={() => setGrouped((on) => !on)}
          >
            <FolderTree className="size-3.5" />
            Group by folder
          </Button>

          <div className="ml-auto flex items-center gap-3">
            {visible.length !== rows.length && (
              <span className="text-[11px] text-muted-foreground">
                {visible.length} of {rows.length}
              </span>
            )}
            {onNew && (
              <Button size="sm" className="h-8 gap-1.5 text-xs" onClick={onNew}>
                <Plus className="size-3.5" />
                New report
              </Button>
            )}
          </div>
        </div>
      )}

      <ScrollArea className="min-h-0 flex-1">
        <div className="px-5 py-4">
          {rows.length === 0 ? (
            loading ? (
              <Grid>
                {Array.from({ length: 8 }, (_, index) => (
                  <Skeleton key={index} className="aspect-[1/1.7] w-full" />
                ))}
              </Grid>
            ) : (
              <Empty
                title="No reports yet"
                body="A report is a folder under reports/ holding main.typ and sources.yml. Start with the sources — a claim with nothing to point at is an opinion."
                action={
                  onNew && (
                    <Button size="sm" className="gap-1.5" onClick={onNew}>
                      <Plus className="size-3.5" />
                      New report
                    </Button>
                  )
                }
              />
            )
          ) : visible.length === 0 ? (
            <Empty
              title="Nothing matches"
              body={`No report's title, id or folder contains “${query.trim()}”.`}
              action={
                <Button size="sm" variant="secondary" onClick={() => setQuery('')}>
                  Clear the filter
                </Button>
              }
            />
          ) : sections ? (
            <div className="flex flex-col gap-6">
              {sections.map(([group, items]) => (
                <section key={group || '(top level)'}>
                  <div className="mb-2 flex items-baseline gap-2">
                    <h2 className="font-mono text-[11px] text-muted-foreground">
                      {group ? `reports/${group}/` : 'reports/'}
                    </h2>
                    <span className="text-[10px] text-muted-foreground">{items.length}</span>
                  </div>
                  <Grid>{items.map(card)}</Grid>
                </section>
              ))}
            </div>
          ) : (
            <Grid>{visible.map(card)}</Grid>
          )}
        </div>
      </ScrollArea>
    </div>
  )
}

function Empty({
  title,
  body,
  action
}: {
  title: string
  body: string
  action?: React.ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-3 px-8 py-20 text-center">
      <h2 className="text-sm font-medium">{title}</h2>
      <p className="max-w-md text-xs text-muted-foreground">{body}</p>
      {action}
    </div>
  )
}
