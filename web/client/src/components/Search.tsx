import { useEffect, useId, useMemo, useRef, type ReactNode } from 'react'
import {
  Archive,
  FileText,
  GitBranch,
  Loader2,
  Quote,
  Search as SearchIcon,
  X,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import type { SearchHit } from '@/lib/api'
import {
  KIND_META,
  KIND_ORDER,
  highlight,
  hitsOfKind,
  shortDate,
  unknownKindHits,
  useSearch,
  type Failure,
  type SearchKind,
} from '@/lib/search'
import { cn, useIsDesktop } from '@/lib/utils'

/**
 * Everything the vault holds, findable — including the pages that are gone.
 *
 * The panel runs `find` through the API and draws what comes back. It does not
 * know what a report is, where a snapshot lives, or how a match was scored;
 * asking the engine is the whole design, because the index is a property of the
 * vault and the vault is the thing two people share.
 *
 * The surprise it has to carry well is the archived pages. A snapshot is a copy
 * of a cited page as it read on the day it was cited, so a phrase found in one
 * is evidence that may have already left the live web — nobody expects a search
 * box to reach that, and the ones who do not know will never think to look.
 * Presenting those hits as if they were files in the vault would be a lie about
 * where the text came from, so they get their own group, their own wording, the
 * source they belong to and the date they were captured.
 */

const KIND_ICON: Record<SearchKind, typeof FileText> = {
  report: FileText,
  snapshot: Archive,
  source: Quote,
  diagram: GitBranch,
}

// ── The overlay ──────────────────────────────────────────────────────────────

export type SearchProps = {
  open?: boolean
  onOpenChange?: (open: boolean) => void
  /** Anything that opens it. Wrapped in a trigger; omit when driving `open`. */
  trigger?: ReactNode
  /** Take the reader to a hit. Rows are readable but inert without it. */
  onOpenHit?: (hit: SearchHit) => void
}

/** A sheet from the bottom on a phone, a panel from the right on a desktop. */
export function Search({ open, onOpenChange, trigger, onOpenHit }: SearchProps) {
  const desktop = useIsDesktop()

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      {trigger ? <SheetTrigger asChild>{trigger}</SheetTrigger> : null}
      <SheetContent
        side={desktop ? 'right' : 'bottom'}
        className={cn('gap-0', !desktop && 'h-[88dvh] max-h-[88dvh]')}
      >
        <SheetHeader>
          <SheetTitle>Search this vault</SheetTitle>
          <SheetDescription>
            Reports, sources, diagrams — and the archived copy of every cited page.
          </SheetDescription>
        </SheetHeader>
        <Separator />
        <SearchPanel
          autoFocus={desktop}
          onOpenHit={(hit) => {
            onOpenHit?.(hit)
            onOpenChange?.(false)
          }}
          className="min-h-0 flex-1"
        />
      </SheetContent>
    </Sheet>
  )
}

// ── The panel ────────────────────────────────────────────────────────────────

export type SearchPanelProps = {
  onOpenHit?: (hit: SearchHit) => void
  /**
   * Focus the field on mount. Left off on a phone on purpose: focusing a field
   * as a sheet animates in throws the soft keyboard up over the sheet before it
   * has finished arriving, and the reader loses their place in the animation.
   */
  autoFocus?: boolean
  className?: string
}

export function SearchPanel({ onOpenHit, autoFocus = false, className }: SearchPanelProps) {
  const search = useSearch()
  const { query, setQuery, all, hits, hidden, toggleKind, showAll, counts, loading, error } = search
  const field = useRef<HTMLInputElement>(null)
  const id = useId()

  useEffect(() => {
    if (autoFocus) field.current?.focus()
  }, [autoFocus])

  const groups = useMemo(
    () =>
      KIND_ORDER.map((kind) => ({ kind, ...KIND_META[kind], hits: hitsOfKind(hits, kind) })).filter(
        (group) => group.hits.length > 0
      ),
    [hits]
  )
  const strays = useMemo(() => unknownKindHits(hits), [hits])

  const searching = query.trim().length > 0
  const filtered = hidden.length > 0 && all.length > hits.length

  return (
    <div className={cn('flex min-h-0 flex-col', className)}>
      {/* The field is at the top of the pane, which is also the only place a
          soft keyboard cannot cover. Nothing below it is fixed. */}
      <div className="shrink-0 px-3 pt-3 pb-2">
        <label className="sr-only" htmlFor={id}>
          Search this vault
        </label>
        <div className="relative">
          <SearchIcon
            className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            id={id}
            ref={field}
            value={query}
            type="search"
            spellCheck={false}
            autoComplete="off"
            enterKeyHint="search"
            inputMode="search"
            placeholder="Find in this vault"
            onChange={(event) => setQuery(event.target.value)}
            className="pr-12 pl-8"
          />
          <span className="absolute top-1/2 right-1 -translate-y-1/2">
            {loading ? (
              <span className="tap inline-flex items-center justify-center text-muted-foreground">
                <Loader2 className="size-4 animate-spin" aria-hidden />
                <span className="sr-only">Searching</span>
              </span>
            ) : (
              searching && (
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Clear the search"
                  onClick={() => {
                    search.clear()
                    field.current?.focus()
                  }}
                >
                  <X aria-hidden />
                </Button>
              )
            )}
          </span>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {KIND_ORDER.map((kind) => {
            const on = !hidden.includes(kind)
            const n = counts[kind] ?? 0
            return (
              <button
                key={kind}
                type="button"
                aria-pressed={on}
                onClick={() => toggleKind(kind)}
                className={cn(
                  'inline-flex min-h-11 items-center gap-1.5 rounded-full border px-3 text-[13px] outline-none lg:min-h-8',
                  'focus-visible:ring-[3px] focus-visible:ring-ring/50 active:bg-accent',
                  on
                    ? 'border-transparent bg-secondary text-secondary-foreground'
                    : 'border-border text-muted-foreground line-through decoration-1'
                )}
              >
                {KIND_META[kind].chip}
                {searching && <span className="tabular-nums opacity-70">{n}</span>}
              </button>
            )
          })}
          {hidden.length > 0 && (
            <Button variant="ghost" size="sm" onClick={showAll}>
              Show all
            </Button>
          )}
        </div>
      </div>

      <Separator />

      <div className="pane flex-1">
        {!searching && <Scope />}

        {searching && error && <Refusal failure={error} onRetry={search.reload} />}

        {searching && loading && all.length === 0 && !error && (
          <div className="flex flex-col gap-2 p-3">
            {[0, 1, 2, 3].map((row) => (
              <Skeleton key={row} className="h-16 w-full" />
            ))}
          </div>
        )}

        {searching && !error && !loading && all.length === 0 && (
          <div className="px-4 py-4 text-sm leading-relaxed text-muted-foreground">
            <p className="text-foreground">Nothing matches “{query.trim()}”.</p>
            <p className="mt-2">
              The index is built from the files in the vault. A report that
              arrived with a git clone may not have reached it yet.
            </p>
          </div>
        )}

        {searching && !error && all.length > 0 && hits.length === 0 && (
          <div className="px-4 py-4 text-sm leading-relaxed text-muted-foreground">
            <p className="text-foreground">
              {all.length} {all.length === 1 ? 'hit' : 'hits'}, all in kinds you have
              switched off.
            </p>
            <Button variant="outline" size="sm" className="mt-3" onClick={showAll}>
              Show all kinds
            </Button>
          </div>
        )}

        {hits.length > 0 && (
          <div className="pb-4">
            <p className="px-4 pt-3 pb-1 text-[11px] text-muted-foreground tabular-nums">
              {hits.length} {hits.length === 1 ? 'hit' : 'hits'}
              {filtered && ` of ${all.length}`}
            </p>
            {groups.map((group) => {
              const Icon = KIND_ICON[group.kind]
              return (
                <section key={group.kind} aria-label={group.title} className="pt-1">
                  <div className="px-4 pt-2 pb-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <h3 className="text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
                        {group.title}
                      </h3>
                      <span className="text-[10px] text-muted-foreground tabular-nums">
                        {group.hits.length}
                      </span>
                    </div>
                    {group.kind === 'snapshot' && (
                      // The one group that has to explain itself: this text is
                      // not in the vault's prose, it is in a copy of somebody
                      // else's page, kept on the day it was cited.
                      <p className="mt-1 text-[12px] leading-snug text-muted-foreground">
                        Copies kept beside the report when its sources were cited
                        — what each page said then, not what it says now.
                      </p>
                    )}
                  </div>
                  <ul className="flex flex-col px-1">
                    {group.hits.map((hit, index) => (
                      <Row
                        key={`${hit.kind}:${hit.path}:${hit.line}:${hit.offset}:${index}`}
                        hit={hit}
                        Icon={Icon}
                        onOpen={onOpenHit}
                      />
                    ))}
                  </ul>
                </section>
              )
            })}

            {strays.length > 0 && (
              <section aria-label="Other" className="pt-1">
                <h3 className="px-4 pt-3 pb-1 text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
                  Other
                </h3>
                <ul className="flex flex-col px-1">
                  {strays.map((hit, index) => (
                    <Row
                      key={`stray:${hit.path}:${hit.line}:${index}`}
                      hit={hit}
                      Icon={FileText}
                      onOpen={onOpenHit}
                    />
                  ))}
                </ul>
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── One hit ──────────────────────────────────────────────────────────────────

function Row({
  hit,
  Icon,
  onOpen,
}: {
  hit: SearchHit
  Icon: typeof FileText
  onOpen?: (hit: SearchHit) => void
}) {
  const snapshot = hit.kind === 'snapshot'
  const key = hit.key || null

  const body = (
    <>
      <span className="flex items-center gap-1.5">
        <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        {key && <span className="shrink-0 font-mono text-[11px]">@{key}</span>}
        <span className="truncate text-[13px] font-medium">{hit.title || hit.report}</span>
      </span>

      {/* The excerpt is somebody's prose — or somebody else's page. It is
          rendered as text with the server's spans wrapped, never as HTML. */}
      <span className="mt-1 block text-[13px] leading-snug text-foreground/85 break-anywhere">
        {highlight(hit.excerpt, hit.marks)}
      </span>

      <span className="mt-1 block text-[11px] text-muted-foreground break-anywhere">
        {snapshot ? (
          <>
            archived copy
            {hit.fetched ? ` · captured ${shortDate(hit.fetched)}` : ''} · cited by{' '}
            <span className="font-mono">{hit.report}</span>
          </>
        ) : (
          <span className="font-mono">
            {hit.path}
            {hit.line !== null ? `:${hit.line}` : ''}
          </span>
        )}
      </span>
    </>
  )

  if (!onOpen) {
    return (
      <li className="rounded-md px-3 py-2">
        <div className="min-w-0">{body}</div>
      </li>
    )
  }

  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(hit)}
        className={cn(
          'w-full min-w-0 rounded-md px-3 py-2 text-left outline-none',
          'focus-visible:ring-[3px] focus-visible:ring-ring/50 active:bg-accent'
        )}
      >
        {body}
      </button>
    </li>
  )
}

// ── Nothing typed yet ────────────────────────────────────────────────────────

/**
 * What is in scope, said before it is asked. The archived pages are the line
 * worth reading, so they are not last.
 */
function Scope() {
  return (
    <div className="px-4 py-4 text-[13px] leading-relaxed text-muted-foreground">
      <p className="text-foreground">Search everything this vault holds.</p>
      <ul className="mt-3 flex flex-col gap-2">
        {KIND_ORDER.map((kind) => {
          const Icon = KIND_ICON[kind]
          return (
            <li key={kind} className="flex gap-2">
              <Icon className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              <span>
                <span className="text-foreground">{KIND_META[kind].title}</span> —{' '}
                {KIND_META[kind].hint}
              </span>
            </li>
          )
        })}
      </ul>
      <p className="mt-3">
        Archived pages are included, so a phrase you remember from a source stays
        findable after the live page has changed or gone.
      </p>
    </div>
  )
}

// ── A refusal ────────────────────────────────────────────────────────────────

function Refusal({ failure, onRetry }: { failure: Failure; onRetry: () => void }) {
  return (
    <div className="m-3 rounded-lg border border-destructive/40 p-3">
      <p className="text-sm break-anywhere">{failure.message}</p>
      {failure.detail && (
        <pre className="scroll-x mt-2 max-h-40 overflow-y-auto rounded-md border bg-muted p-2 font-mono text-[11px] whitespace-pre">
          {failure.detail}
        </pre>
      )}
      <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
        Try again
      </Button>
    </div>
  )
}
