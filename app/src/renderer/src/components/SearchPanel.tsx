import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Archive,
  ExternalLink,
  FileText,
  GitBranch,
  Loader2,
  Quote,
  RotateCw,
  Search,
  X
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import {
  SEARCH_LIMIT,
  highlight,
  rebuildIndex,
  useSearch,
  type SearchHit,
  type SearchKind
} from '@/lib/search'
import { shortDate } from '@/lib/sources'
import { cn } from '@/lib/utils'

type Props = {
  vault: string
  /** Put the cursor on a line of a vault-relative file, the same call the
   *  sources and problems panels make. */
  onReveal: (path: string, line: number) => void
  /** Open the archived HTML kept beside a report for one source key. */
  onOpenSnapshot: (report: string, key: string) => void
  className?: string
}

/**
 * Everything in the vault, findable.
 *
 * The panel runs `find --json` and draws what comes back. It does not know what
 * a report is, where a snapshot lives, or how a match was scored — asking the
 * engine is the whole design, because the index is a property of the vault and
 * the vault is the thing two people can share.
 *
 * The surprise it has to carry well is the archived pages. A snapshot is a copy
 * of a cited page as it read on the day it was cited, and a phrase found in one
 * is evidence that has already left the live web. Presenting those hits as if
 * they were files in the vault would be a lie about where the text came from, so
 * they get their own group, their own wording, and a date.
 */
export function SearchPanel({ vault, onReveal, onOpenSnapshot, className }: Props) {
  const { query, setQuery, hits, loading, error, kinds, setKinds, reload } = useSearch(vault)
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // Groups are the render order, and the render order is the keyboard order —
  // `flat` is the same rows the reader is looking at, in the same sequence.
  const groups = useMemo(
    () =>
      GROUPS.map((group) => ({
        ...group,
        hits: hits.filter((hit) => hit.kind === group.kind)
      })).filter((group) => group.hits.length > 0),
    [hits]
  )
  const flat = useMemo(() => groups.flatMap((group) => group.hits), [groups])

  useEffect(() => setActive(0), [hits])

  useEffect(() => {
    // `block: 'nearest'` scrolls only when the row is actually out of view, so
    // arrowing through visible results does not jump the list under the eye.
    listRef.current
      ?.querySelector(`[data-hit="${active}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [active])

  useEffect(() => inputRef.current?.focus(), [])

  function openHit(hit: SearchHit): void {
    // An archived page is not a file anyone edits; opening it in the editor
    // would show them the HTML they never wrote. Snapshots open as pages.
    if (hit.kind === 'snapshot' && hit.key) onOpenSnapshot(hit.report, hit.key)
    else onReveal(hit.path, hit.line)
  }

  function onKeyDown(event: React.KeyboardEvent): void {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActive((index) => Math.min(index + 1, flat.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((index) => Math.max(index - 1, 0))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const hit = flat[active]
      if (hit) openHit(hit)
    } else if (event.key === 'Escape') {
      event.preventDefault()
      setQuery('')
      inputRef.current?.focus()
    }
  }

  function toggleKind(kind: SearchKind): void {
    setKinds(kinds.includes(kind) ? kinds.filter((k) => k !== kind) : [...kinds, kind])
  }

  const searching = query.trim().length > 0

  return (
    <div className={cn('flex h-full min-h-0 flex-col', className)} onKeyDown={onKeyDown}>
      <div className="flex h-8 shrink-0 items-center justify-between gap-2 px-3 text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
        <span>Search</span>
        {searching && !error && (
          <span>
            {hits.length === SEARCH_LIMIT ? `first ${SEARCH_LIMIT}` : hits.length}
            {hits.length === 1 ? ' hit' : ' hits'}
          </span>
        )}
      </div>
      <Separator />

      <div className="shrink-0 space-y-1.5 p-2">
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-2 size-3 -translate-y-1/2 text-muted-foreground" />
          <Input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Find in this vault"
            spellCheck={false}
            role="combobox"
            aria-expanded={flat.length > 0}
            aria-controls={flat.length > 0 ? 'search-results' : undefined}
            aria-activedescendant={flat.length > 0 ? `search-hit-${active}` : undefined}
            className="h-7 pr-7 pl-7 text-xs"
          />
          {loading && (
            <Loader2 className="pointer-events-none absolute top-1/2 right-2 size-3 -translate-y-1/2 animate-spin text-muted-foreground" />
          )}
          {!loading && searching && (
            <button
              onClick={() => {
                setQuery('')
                inputRef.current?.focus()
              }}
              title="Clear"
              className="absolute top-1/2 right-1.5 -translate-y-1/2 rounded-sm p-0.5 text-muted-foreground hover:text-foreground"
            >
              <X className="size-3" />
            </button>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-1">
          {GROUPS.map((group) => {
            const on = kinds.includes(group.kind)
            return (
              <Button
                key={group.kind}
                size="xs"
                variant={on ? 'secondary' : 'ghost'}
                aria-pressed={on}
                title={group.hint}
                onClick={() => toggleKind(group.kind)}
                className={cn(
                  'h-5 rounded-full px-2 text-[10.5px] font-normal',
                  !on && 'text-muted-foreground'
                )}
              >
                {group.chip}
              </Button>
            )
          })}
          {kinds.length > 0 && (
            <Button
              size="xs"
              variant="ghost"
              onClick={() => setKinds([])}
              className="h-5 px-1.5 text-[10.5px] font-normal text-muted-foreground"
            >
              All
            </Button>
          )}
        </div>
      </div>
      <Separator />

      <ScrollArea className="min-h-0 flex-1">
        <div ref={listRef} className="p-1">
          {!searching && <Scope />}

          {searching && error && (
            <div className="space-y-2 p-2">
              <p className="text-xs text-muted-foreground">
                <span className="font-mono">find --json</span> failed.
              </p>
              <pre className="max-h-40 overflow-auto rounded-md border border-border p-2 font-mono text-[11px] whitespace-pre-wrap">
                {error}
              </pre>
              <Button size="xs" variant="secondary" onClick={reload}>
                Try again
              </Button>
            </div>
          )}

          {searching && !error && !loading && hits.length === 0 && (
            <NoResults vault={vault} query={query.trim()} onRebuilt={reload} />
          )}

          {/* The listbox holds only options — the empty states above it are prose,
              and a listbox with paragraphs inside announces as a broken list. */}
          <div
            id="search-results"
            role="listbox"
            aria-label="Search results"
            hidden={flat.length === 0}
          >
            {groups.map((group) => {
              const first = flat.indexOf(group.hits[0])
              return (
                <section key={group.kind} className="mb-1" role="group" aria-label={group.title}>
                  {/* The group already carries its name for a screen reader; the
                      heading is the sighted half of the same thing. */}
                  <div className="px-2 pt-2 pb-1" aria-hidden="true">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
                        {group.title}
                      </span>
                      <span className="text-[10px] text-muted-foreground">{group.hits.length}</span>
                    </div>
                    {group.kind === 'snapshot' && (
                      <p className="mt-0.5 text-[10.5px] leading-snug text-muted-foreground">
                        Copies kept beside the report when its sources were cited — what each page
                        said then, not what it says now.
                      </p>
                    )}
                  </div>
                  {group.hits.map((hit, index) => (
                    <Row
                      key={`${hit.kind}:${hit.path}:${hit.line}:${hit.offset}`}
                      hit={hit}
                      index={first + index}
                      active={first + index === active}
                      onHover={setActive}
                      onOpen={openHit}
                      onOpenSnapshot={onOpenSnapshot}
                    />
                  ))}
                </section>
              )
            })}
          </div>
        </div>
      </ScrollArea>
    </div>
  )
}

// ── The four things a vault holds ────────────────────────────────────────────

type KindMeta = {
  /** The filter chip's label — what you are switching off. */
  chip: string
  /** The results heading — what you are looking at. A snapshot is a filter over
   *  "snapshots" and a group of "archived pages"; the reader meets the second
   *  word first, and it is the one that says what the text actually is. */
  title: string
  hint: string
  Icon: typeof FileText
}

/** Keyed by kind rather than listed, so a kind added to the engine cannot reach
 *  this panel without a compile error naming it. */
const KIND_META: Record<SearchKind, KindMeta> = {
  report: {
    chip: 'Reports',
    title: 'Reports',
    hint: 'The prose of every main.typ',
    Icon: FileText
  },
  snapshot: {
    chip: 'Snapshots',
    title: 'Archived pages',
    hint: 'The archived copy of each cited page, as it read when it was cited',
    Icon: Archive
  },
  source: {
    chip: 'Sources',
    title: 'Sources',
    hint: 'Keys, titles and authors in sources.yml',
    Icon: Quote
  },
  diagram: {
    chip: 'Diagrams',
    title: 'Diagrams',
    hint: 'The mermaid source in diagrams/*.mmd',
    Icon: GitBranch
  }
}

/** Display order: what you wrote, then what you kept, then how you cited it.
 *  Archived pages sit second because they are the surprising ones — buried at
 *  the bottom they would read as an afterthought rather than as evidence. */
const ORDER: SearchKind[] = ['report', 'snapshot', 'source', 'diagram']

const GROUPS = ORDER.map((kind) => ({ kind, ...KIND_META[kind] }))

// ── One hit ──────────────────────────────────────────────────────────────────

function Row({
  hit,
  index,
  active,
  onHover,
  onOpen,
  onOpenSnapshot
}: {
  hit: SearchHit
  index: number
  active: boolean
  onHover: (index: number) => void
  onOpen: (hit: SearchHit) => void
  onOpenSnapshot: (report: string, key: string) => void
}) {
  const Icon = KIND_META[hit.kind]?.Icon ?? FileText
  const snapshot = hit.kind === 'snapshot'
  // Bound as a const so the guard below still holds inside the click handler —
  // TypeScript discards narrowing of a property across a function boundary.
  const key = hit.key

  return (
    <div
      id={`search-hit-${index}`}
      data-hit={index}
      role="option"
      aria-selected={active}
      onMouseEnter={() => onHover(index)}
      onClick={() => onOpen(hit)}
      className={cn(
        'cursor-default rounded-sm px-2 py-1.5',
        active ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60'
      )}
    >
      <div className="flex items-center gap-1.5">
        <Icon className="size-3 shrink-0 text-muted-foreground" />
        {key && <span className="shrink-0 font-mono text-[11px]">@{key}</span>}
        <span className="truncate text-[11.5px] font-medium">{hit.title || hit.report}</span>
      </div>

      <p className="mt-0.5 line-clamp-3 text-[11.5px] leading-snug text-foreground/80">
        {highlight(hit.excerpt, hit.marks)}
      </p>

      <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground">
        {snapshot ? (
          // The one row that has to explain itself: this text is not in the
          // vault's prose, it is in a copy of somebody else's page.
          <span className="truncate">
            archived copy
            {hit.fetched ? ` · captured ${shortDate(hit.fetched)}` : ''} · cited by{' '}
            <span className="font-mono">{hit.report}</span>
          </span>
        ) : (
          <span className="truncate font-mono">
            {hit.path}:{hit.line}
          </span>
        )}
      </div>

      {snapshot && key && (
        <Button
          size="xs"
          variant="secondary"
          className="mt-1 h-5 px-1.5 text-[10.5px] font-normal"
          onClick={(event) => {
            // The row opens it too; without this the click would arrive twice.
            event.stopPropagation()
            onOpenSnapshot(hit.report, key)
          }}
        >
          <ExternalLink className="size-3" />
          Open snapshot
        </Button>
      )}
    </div>
  )
}

// ── Nothing typed yet ────────────────────────────────────────────────────────

/** What is in scope, said before it is asked. The archived pages are the line
 *  worth reading: nobody expects a search box to reach text that is no longer
 *  on the web, and the ones who do not know will never think to look. */
function Scope() {
  return (
    <div className="space-y-2 px-3 py-4 text-xs leading-relaxed text-muted-foreground">
      <p className="text-foreground/80">Search everything this vault holds.</p>
      <ul className="space-y-1.5">
        {GROUPS.map((group) => (
          <li key={group.kind} className="flex gap-2">
            <group.Icon className="mt-[3px] size-3 shrink-0" />
            <span>
              <span className="text-foreground/80">{group.title}</span> — {group.hint}
            </span>
          </li>
        ))}
      </ul>
      <p>
        Archived pages are included, so a phrase you remember from a source is findable even after
        the live page has changed or gone.
      </p>
    </div>
  )
}

// ── Nothing found ────────────────────────────────────────────────────────────

function NoResults({
  vault,
  query,
  onRebuilt
}: {
  vault: string
  query: string
  onRebuilt: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  async function rebuild(): Promise<void> {
    setBusy(true)
    setFailure(null)
    const result = await rebuildIndex(vault)
    setBusy(false)
    if (result.code !== 0) {
      setFailure((result.stderr || result.stdout || `exit ${result.code}`).trimEnd())
      return
    }
    onRebuilt()
  }

  return (
    <div className="space-y-2 px-3 py-4 text-xs leading-relaxed text-muted-foreground">
      <p className="text-foreground/80">Nothing matches “{query}”.</p>
      <p>
        The index is built from the files on disk. A report pulled in with git, or written in another
        editor, may not have reached it yet.
      </p>
      <Button size="xs" variant="secondary" disabled={busy} onClick={() => void rebuild()}>
        {busy ? <Loader2 className="size-3 animate-spin" /> : <RotateCw className="size-3" />}
        {busy ? 'Rebuilding…' : 'Rebuild the index'}
      </Button>
      {failure && (
        <pre className="max-h-40 overflow-auto rounded-md border border-destructive/50 p-2 font-mono text-[11px] whitespace-pre-wrap">
          {failure}
        </pre>
      )}
    </div>
  )
}
