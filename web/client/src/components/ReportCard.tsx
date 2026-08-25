import { useState } from 'react'
import { CircleCheck, CircleDashed, Clock, FolderClosed, ListTodo, TriangleAlert } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { urls, type ReportRow, type ReportScore } from '@/lib/api'
import { basename, percent, plural, type Tally } from '@/lib/reports'
import { cn } from '@/lib/utils'

/**
 * One report, as a row on a phone and as a card on a wide screen.
 *
 * Same DOM either way. The switch is a container query against the list it sits
 * in — not the viewport — because this component is dropped into a 375px pane, a
 * 256px desktop sidebar and a full-width pane, and only the first and the third
 * of those are decided by the size of the screen. A viewport media query would
 * put a two-column grid inside a 256px sidebar and be right about the phone by
 * accident.
 *
 * It states facts and computes none of them. Built and stale are `list --json`'s
 * columns; the density is `score --json`'s number; the findings count is
 * `check --json`'s, grouped; the open tasks are `todos --json`'s. Every one of
 * them is absent rather than guessed when the command behind it did not answer —
 * a card that says "clean" because a request failed is worse than a card that
 * says nothing.
 */

export type ReportCardProps = {
  row: ReportRow
  /** `score --json`'s entry for this report, when the score could be read. */
  score?: ReportScore
  /** Findings against this report. Undefined means the check has not run. */
  tally?: Tally
  /** Open tasks on the pad. Zero and absent both mean "nothing to show". */
  open?: number
  /** The design's own title, when the template list has been read. */
  templateTitle?: string
  /** Bumped after a build, so the cover is refetched rather than remembered. */
  revision?: number
  selected?: boolean
  onOpen: (id: string) => void
  className?: string
}

export function ReportCard({
  row,
  score,
  tally,
  open = 0,
  templateTitle,
  revision = 0,
  selected = false,
  onOpen,
  className,
}: ReportCardProps) {
  const title = row.title?.trim() || basename(row.id)

  return (
    <button
      type="button"
      onClick={() => onOpen(row.id)}
      aria-current={selected ? 'true' : undefined}
      className={cn(
        // The whole card is the target, so the 44px floor is met many times over.
        'group flex w-full min-h-[var(--tap)] gap-3 overflow-hidden rounded-lg border bg-card text-left text-card-foreground shadow-xs outline-none',
        'transition-colors active:bg-accent focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50',
        'hover:border-ring/60',
        selected && 'border-ring ring-[2px] ring-ring/40',
        // Card layout: the cover on top, full width, once the list is wide.
        '@min-[34rem]/pane:flex-col @min-[34rem]/pane:gap-0',
        className
      )}
    >
      <Cover row={row} title={title} revision={revision} />

      <span className="flex min-w-0 flex-1 flex-col gap-1 py-2 pr-3 @min-[34rem]/pane:px-3 @min-[34rem]/pane:pb-2.5">
        <Breadcrumb group={row.group} />

        <span className="line-clamp-2 text-[13px] leading-tight font-medium break-anywhere">
          {title}
        </span>

        <span className="flex min-w-0 items-center gap-1.5 text-[10px] text-muted-foreground">
          <span className="shrink-0 font-mono tabular-nums">{row.date || '—'}</span>
          <span aria-hidden>·</span>
          <span className="truncate font-mono">{templateTitle || row.template}</span>
        </span>

        {score && (
          <span className="flex items-center gap-2 pt-0.5">
            <EvidenceBar counts={score} className="min-w-0 flex-1" />
            <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
              {percent(score.density)}
            </span>
          </span>
        )}

        <span className="flex flex-wrap items-center gap-1 pt-0.5">
          <StateChip row={row} />
          <FindingsChip tally={tally} />
          <TodosChip open={open} />
        </span>
      </span>
    </button>
  )
}

/**
 * The cover.
 *
 * A built report shows its own first page — `GET /reports/:id/page/1`, the same
 * PNG the Read tab uses, because a phone cannot usefully display a PDF and there
 * is no reason for a thumbnail to be a second kind of thing. An unbuilt report
 * gets a typographic stand-in rather than a grey box, so a fresh vault still
 * reads as a shelf of documents.
 *
 * `revision` is a cache-buster, not a fetch: the browser caches the PNG by URL
 * and would otherwise show yesterday's cover after a rebuild.
 */
function Cover({ row, title, revision }: { row: ReportRow; title: string; revision: number }) {
  const stamp = `${row.id}:${revision}`
  const [broken, setBroken] = useState('')
  const showImage = row.built && broken !== stamp

  return (
    <span
      className={cn(
        'flex shrink-0 overflow-hidden border-r bg-muted/40',
        'aspect-[1/1.414] w-14',
        '@min-[34rem]/pane:w-full @min-[34rem]/pane:border-r-0 @min-[34rem]/pane:border-b'
      )}
    >
      {showImage ? (
        <img
          key={stamp}
          src={`${urls.page(row.id, 1)}?v=${revision}`}
          alt=""
          loading="lazy"
          decoding="async"
          onError={() => setBroken(stamp)}
          className="h-full w-full object-cover object-top"
        />
      ) : (
        <span className="flex h-full w-full flex-col justify-between gap-1 p-1.5 @min-[34rem]/pane:p-3">
          <span className="font-mono text-[8px] tracking-[0.14em] text-muted-foreground uppercase @min-[34rem]/pane:text-[9px]">
            {row.kind || 'Report'}
          </span>
          {/* The title only earns its place once the cover is full width. At
              56px it is four characters and a hyphen. The wrapper toggles
              display so the clamp inside it is never fought over. */}
          <span className="hidden min-w-0 @min-[34rem]/pane:block">
            <span className="line-clamp-4 text-[13px] leading-snug font-medium text-muted-foreground">
              {title}
            </span>
          </span>
          <span className="text-[8px] leading-tight text-muted-foreground @min-[34rem]/pane:text-[10px]">
            not built
          </span>
        </span>
      )}
    </span>
  )
}

/** The report's folder, which is also its filing system. */
function Breadcrumb({ group }: { group: string }) {
  return (
    <span className="flex min-w-0 items-center gap-1 text-[10px] text-muted-foreground">
      <FolderClosed className="size-2.5 shrink-0" aria-hidden />
      <span className="truncate font-mono">{group ? `${group}/` : 'top level'}</span>
    </span>
  )
}

/**
 * Cited, assessed and unmarked in proportion — three segments, not one bar,
 * because the citation rule has three answers and a report that is 80% assessment
 * reads very differently from one that is 80% cited. The numbers are the
 * engine's; only the widths are arithmetic.
 */
export type EvidenceCounts = { cited: number; assessed: number; unmarked: number }

export function EvidenceBar({
  counts,
  className,
}: {
  counts: EvidenceCounts
  className?: string
}) {
  const total = counts.cited + counts.assessed + counts.unmarked
  const width = (n: number): string => `${total > 0 ? (n / total) * 100 : 0}%`

  return (
    <span
      role="img"
      aria-label={`${counts.cited} cited, ${counts.assessed} assessed, ${counts.unmarked} unmarked`}
      className={cn('flex h-1.5 overflow-hidden rounded-full bg-rail-neutral', className)}
    >
      <span className="bg-rail-cited" style={{ width: width(counts.cited) }} />
      <span className="bg-rail-assessed" style={{ width: width(counts.assessed) }} />
      <span className="bg-rail-unmarked" style={{ width: width(counts.unmarked) }} />
    </span>
  )
}

const CHIP = 'gap-1 px-1.5 py-0 text-[10px] font-normal'

function StateChip({ row }: { row: ReportRow }) {
  if (!row.built) {
    return (
      <Badge variant="outline" className={cn(CHIP, 'text-muted-foreground')}>
        <CircleDashed aria-hidden />
        unbuilt
      </Badge>
    )
  }
  if (row.stale) {
    return (
      <Badge variant="secondary" className={CHIP}>
        <Clock aria-hidden />
        stale
      </Badge>
    )
  }
  return (
    <Badge variant="outline" className={cn(CHIP, 'text-muted-foreground')}>
      <CircleCheck aria-hidden />
      built
    </Badge>
  )
}

/** No chip at all when the check has not run: silence is honest, "clean" is not. */
function FindingsChip({ tally }: { tally: Tally | undefined }) {
  if (!tally) return null
  if (tally.errors > 0) {
    return (
      <Badge variant="error" className={CHIP}>
        <TriangleAlert aria-hidden />
        {plural(tally.errors, 'error')}
      </Badge>
    )
  }
  if (tally.warnings > 0) {
    return (
      <Badge variant="warning" className={CHIP}>
        <TriangleAlert aria-hidden />
        {plural(tally.warnings, 'warning')}
      </Badge>
    )
  }
  return (
    <Badge variant="outline" className={cn(CHIP, 'text-muted-foreground')}>
      <CircleCheck aria-hidden />
      clean
    </Badge>
  )
}

/**
 * Open tasks on this report's pad — `todos.md`, `notes.md` and `// TODO:` in the
 * source. Only ever shown when there are some: "0 open" asserts something about
 * a file that may not exist.
 */
function TodosChip({ open }: { open: number }) {
  if (open <= 0) return null
  return (
    <Badge variant="outline" className={cn(CHIP, 'text-muted-foreground')}>
      <ListTodo aria-hidden />
      {open} open
    </Badge>
  )
}
