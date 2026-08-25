import { useMemo, useState, type ReactNode } from 'react'
import { Plus, RotateCcw, Search } from 'lucide-react'

import { EvidenceBar, ReportCard } from '@/components/ReportCard'
import { NewReport } from '@/components/NewReport'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { useApp } from '@/App'
import type { ReportRow } from '@/lib/api'
import { percent, plural, tallyFindings, useVaultReports } from '@/lib/reports'
import { cn } from '@/lib/utils'

/**
 * The Reports tab: the shelf somebody lands on.
 *
 * Three requests draw it — `list --json`, `score --json`, `todos --json` — and
 * the findings come from the vault-wide `check` the app shell already holds. Not
 * one request per card: eighty reports would be eighty engine subprocesses to
 * paint one screen, and the engine walks the whole vault in each of those three
 * anyway.
 *
 * The layout answers to the width of the pane, not the width of the screen. This
 * component is dropped into a 375px phone pane, a 256px desktop sidebar and a
 * full-width pane, and a viewport media query would put a card grid into the
 * sidebar and be right about the phone by luck. `@container/pane` is the pane
 * itself; every responsive class below and inside {@link ReportCard} is a
 * question about it.
 */

type Sort = 'date' | 'title' | 'density' | 'stale'

const SORTS: { value: Sort; label: string }[] = [
  { value: 'date', label: 'Newest first' },
  { value: 'title', label: 'Title A–Z' },
  { value: 'density', label: 'Evidence, weakest first' },
  { value: 'stale', label: 'Needs building first' },
]

/** Unbuilt first, then stale, then built — the order of what wants attention. */
function staleness(row: ReportRow): number {
  if (!row.built) return 2
  return row.stale ? 1 : 0
}

export function Reports() {
  const { session, reportId, selectReport, setTab, check, revision, invalidate } = useApp()
  const { rows, scores, totals, open, loading, error, reload } = useVaultReports(revision)

  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<Sort>('date')
  const [creating, setCreating] = useState(false)

  const tallies = useMemo(() => tallyFindings(check?.findings, rows), [check, rows])

  // `!built || stale` is a count of two columns the engine printed, not a
  // judgement about them.
  const needBuilding = useMemo(
    () => rows.filter((row) => !row.built || row.stale).length,
    [rows]
  )

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const matched = needle
      ? rows.filter((row) =>
          `${row.title ?? ''} ${row.id} ${row.group}`.toLowerCase().includes(needle)
        )
      : rows

    // An unscored report sorts last under "weakest first": an unknown density is
    // not a good one, but it is not evidence of a problem either.
    const density = (row: ReportRow): number =>
      scores.get(row.id)?.density ?? Number.POSITIVE_INFINITY
    const byId = (a: ReportRow, b: ReportRow): number => a.id.localeCompare(b.id)

    const order: Record<Sort, (a: ReportRow, b: ReportRow) => number> = {
      date: (a, b) => (b.date ?? '').localeCompare(a.date ?? '') || byId(a, b),
      title: (a, b) => (a.title || a.id).localeCompare(b.title || b.id),
      density: (a, b) => density(a) - density(b) || byId(a, b),
      stale: (a, b) => staleness(b) - staleness(a) || byId(a, b),
    }
    return [...matched].sort(order[sort])
  }, [rows, query, sort, scores])

  function opened(id: string): void {
    selectReport(id)
    setTab('write')
  }

  return (
    <div className="@container/pane flex h-full min-h-0 flex-col">
      {/* ── the strip ───────────────────────────────────────────────────── */}
      <header className="shrink-0 border-b px-3 py-3 lg:px-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h1 className="truncate text-sm leading-tight font-semibold">{session.vault}</h1>
            <p className="truncate text-[11px] text-muted-foreground">
              {loading && rows.length === 0
                ? 'Reading the vault…'
                : `${plural(rows.length, 'report')} · ${needBuilding} need building`}
            </p>
          </div>
          <Button className="shrink-0" onClick={() => setCreating(true)}>
            <Plus aria-hidden />
            New
          </Button>
        </div>

        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 @min-[30rem]/pane:grid-cols-4">
          <Stat label="reports" value={rows.length} />
          <Stat label="need building" value={needBuilding} />
          <Stat label="sources" value={totals?.sourcesTotal ?? '—'} />
          <div className="min-w-0">
            <dt className="text-[10px] tracking-widest text-muted-foreground uppercase">
              evidence
            </dt>
            <dd className="mt-0.5 flex items-center gap-2">
              <span className="font-mono text-sm leading-none tabular-nums">
                {percent(totals?.density)}
              </span>
              {totals && (
                <EvidenceBar
                  counts={totals}
                  className="min-w-0 flex-1 @min-[30rem]/pane:max-w-24"
                />
              )}
            </dd>
          </div>
        </dl>
      </header>

      {/* Filtering and sorting nothing is worse than useless, so a vault with
          one report skips straight to it. */}
      {rows.length > 1 && (
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b px-3 py-2 lg:px-4">
          {/* `min-w-40` is what makes the wrap happen at the right moment: in a
              256px desktop sidebar the select cannot fit beside a usable field,
              so it drops to its own line instead of squeezing the field to four
              characters. */}
          <div className="relative min-w-40 flex-1">
            <Search
              className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <Input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              aria-label="Filter reports"
              placeholder="Title, id or folder"
              className="pl-9"
            />
          </div>

          <select
            value={sort}
            onChange={(event) => setSort(event.target.value as Sort)}
            aria-label="Sort reports"
            className={cn(
              // A native select: the platform's own picker, which on a phone is
              // a better control than anything that could be built here, and at
              // 16px so iOS does not zoom the page to reach it.
              'h-11 shrink-0 rounded-md border border-input bg-transparent px-3 text-base outline-none',
              'focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50',
              'w-36 dark:bg-input/30 @min-[30rem]/pane:w-56 lg:h-9 lg:text-sm'
            )}
          >
            {SORTS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>

          {visible.length !== rows.length && (
            <span className="shrink-0 text-[11px] text-muted-foreground">
              {visible.length} of {rows.length}
            </span>
          )}
        </div>
      )}

      {/* ── the shelf ───────────────────────────────────────────────────── */}
      <div className="pane flex-1 px-3 py-3 lg:px-4">
        {error ? (
          <Empty
            title="The vault could not be listed."
            body={error}
            action={
              <Button onClick={reload}>
                <RotateCcw aria-hidden />
                Try again
              </Button>
            }
          />
        ) : loading && rows.length === 0 ? (
          <div className="grid grid-cols-1 gap-2 @min-[34rem]/pane:grid-cols-[repeat(auto-fill,minmax(180px,1fr))] @min-[34rem]/pane:gap-3">
            {[0, 1, 2, 3, 4, 5].map((n) => (
              <Skeleton
                key={n}
                className="h-[5.5rem] w-full @min-[34rem]/pane:h-[19rem]"
              />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <Empty
            title="Nothing here yet — start with a question you can source."
            body="A report is a folder holding main.typ and its sources.yml. Write the sources first: a claim with nothing to point at is an opinion, and this tool will say so."
            action={
              <Button size="lg" onClick={() => setCreating(true)}>
                <Plus aria-hidden />
                New report
              </Button>
            }
          />
        ) : visible.length === 0 ? (
          <Empty
            title="Nothing matches."
            body={`No report's title, id or folder contains “${query.trim()}”.`}
            action={
              <Button variant="secondary" onClick={() => setQuery('')}>
                Clear the filter
              </Button>
            }
          />
        ) : (
          <ul className="grid grid-cols-1 gap-2 @min-[34rem]/pane:grid-cols-[repeat(auto-fill,minmax(180px,1fr))] @min-[34rem]/pane:gap-3">
            {visible.map((row) => (
              <li key={row.id} className="min-w-0">
                <ReportCard
                  row={row}
                  score={scores.get(row.id)}
                  tally={tallies.get(row.id)}
                  open={open.get(row.id) ?? 0}
                  revision={revision}
                  selected={row.id === reportId}
                  onOpen={opened}
                />
              </li>
            ))}
          </ul>
        )}
      </div>

      <NewReport
        open={creating}
        onOpenChange={setCreating}
        rows={rows}
        onCreated={(id) => {
          opened(id)
          // The vault changed under every other pane too.
          invalidate()
        }}
      />
    </div>
  )
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="min-w-0">
      <dt className="truncate text-[10px] tracking-widest text-muted-foreground uppercase">
        {label}
      </dt>
      <dd className="mt-0.5 font-mono text-sm leading-none tabular-nums">{value}</dd>
    </div>
  )
}

function Empty({
  title,
  body,
  action,
}: {
  title: string
  body: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-3 px-4 py-14 text-center">
      <h2 className="text-sm font-medium break-anywhere">{title}</h2>
      <p className="max-w-md text-xs leading-relaxed text-muted-foreground break-anywhere">
        {body}
      </p>
      {action}
    </div>
  )
}
