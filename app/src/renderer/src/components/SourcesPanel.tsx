import { useEffect, useState } from 'react'
import { Archive, ArchiveX, Copy, Info, Loader2, Plus, Search } from 'lucide-react'
import type { SourceRow } from '../../../shared/types'
import { EvidencePopover } from './EvidencePopover'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { loadSources, matches, shortDate, sourcesPath } from '@/lib/sources'
import { cn } from '@/lib/utils'

type Props = {
  vault: string
  /** The report whose bibliography this is; null when the open file is not in one. */
  reportId: string | null
  sources: SourceRow[]
  loading: boolean
  /** The engine's own message when `sources --json` failed. */
  error: string | null
  /** Re-run `sources --json`. Also called after a source is added. */
  onReload: () => void
  /** Put the cursor on a line of a vault-relative file — `reports/<id>/sources.yml`. */
  onReveal: (path: string, line: number) => void
  /** Open the archived HTML for a key. */
  onOpenSnapshot: (key: string) => void
  className?: string
}

/**
 * The report's bibliography, as a panel.
 *
 * It renders `sources --json` and nothing else: the rows, their use counts and
 * their snapshot state are all the engine's answers, so what the panel says and
 * what `check` enforces cannot disagree. Every row is a jump into `sources.yml`,
 * because the panel is a way of reading the file, not a replacement for editing it.
 */
export function SourcesPanel({
  vault,
  reportId,
  sources,
  loading,
  error,
  onReload,
  onReveal,
  onOpenSnapshot,
  className
}: Props) {
  const [query, setQuery] = useState('')
  const [adding, setAdding] = useState(false)

  const shown = sources.filter((row) => matches(row, query))
  const orphans = sources.filter((row) => row.uses === 0).length

  return (
    <TooltipProvider delayDuration={200}>
      <div className={cn('flex h-full min-h-0 flex-col', className)}>
        <div className="flex h-8 shrink-0 items-center justify-between gap-2 px-3 text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
          <span>Sources</span>
          <span>
            {sources.length} {sources.length === 1 ? 'source' : 'sources'}
            {orphans > 0 && ` · ${orphans} orphaned`}
          </span>
        </div>
        <Separator />

        <div className="flex shrink-0 items-center gap-1.5 p-2">
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute top-1/2 left-2 size-3 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Filter"
              spellCheck={false}
              className="h-7 pl-7 text-xs"
            />
          </div>
          <Button
            size="xs"
            variant="secondary"
            disabled={!reportId}
            title={reportId ? undefined : 'Open a file inside a report first'}
            onClick={() => setAdding(true)}
          >
            <Plus className="size-3" />
            Add source…
          </Button>
        </div>
        <Separator />

        <ScrollArea className="min-h-0 flex-1">
          <div className="p-1">
            {!reportId && (
              <Empty>
                Open a <span className="font-mono">main.typ</span> to see the report's sources.
              </Empty>
            )}

            {reportId && loading && sources.length === 0 && (
              <div className="space-y-2 p-2">
                {[0, 1, 2].map((row) => (
                  <Skeleton key={row} className="h-8 w-full" />
                ))}
              </div>
            )}

            {reportId && error && (
              <div className="space-y-2 p-2">
                <p className="text-xs text-muted-foreground">
                  <span className="font-mono">sources --json</span> failed.
                </p>
                <pre className="max-h-40 overflow-auto rounded-md border border-border p-2 font-mono text-[11px] whitespace-pre-wrap">
                  {error}
                </pre>
                <Button size="xs" variant="secondary" onClick={onReload}>
                  Try again
                </Button>
              </div>
            )}

            {reportId && !error && !loading && sources.length === 0 && (
              <Empty>
                No sources yet. Start <span className="font-mono">sources.yml</span> before the
                prose — a claim with no key to point at is an opinion.
              </Empty>
            )}

            {reportId && sources.length > 0 && shown.length === 0 && (
              <Empty>Nothing matches “{query}”.</Empty>
            )}

            {reportId &&
              shown.map((row) => (
                <Row
                  key={row.key}
                  row={row}
                  onJump={() => onReveal(sourcesPath(reportId), row.line)}
                  onOpenSnapshot={onOpenSnapshot}
                />
              ))}
          </div>
        </ScrollArea>

        {reportId && (
          <AddSourceDialog
            vault={vault}
            reportId={reportId}
            open={adding}
            onOpenChange={setAdding}
            onAdded={onReload}
          />
        )}
      </div>
    </TooltipProvider>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="px-3 py-4 text-xs leading-relaxed text-muted-foreground">{children}</p>
}

// ── One source ───────────────────────────────────────────────────────────────

function Row({
  row,
  onJump,
  onOpenSnapshot
}: {
  row: SourceRow
  onJump: () => void
  onOpenSnapshot: (key: string) => void
}) {
  const orphan = row.uses === 0

  return (
    <div className="group flex items-start gap-1 rounded-sm px-1 py-1 hover:bg-accent/60">
      <button
        onClick={onJump}
        title={`sources.yml:${row.line}`}
        className="min-w-0 flex-1 rounded-sm text-left"
      >
        <div className="flex items-center gap-1.5">
          <span
            className={cn(
              'truncate font-mono text-[11px]',
              orphan ? 'text-muted-foreground' : 'text-foreground'
            )}
          >
            @{row.key}
          </span>
          <Badge
            variant="outline"
            className="shrink-0 px-1 py-0 text-[9.5px] font-normal text-muted-foreground"
          >
            {row.type}
          </Badge>
          {orphan && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge
                  variant="outline"
                  tabIndex={0}
                  className="shrink-0 border-dashed px-1 py-0 font-mono text-[9.5px] font-normal text-muted-foreground"
                >
                  W001
                </Badge>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="max-w-[260px]">
                Nothing in <span className="font-mono">main.typ</span> cites @{row.key}. It still
                appears in the References section — every reviewed source is listed, cited or not.
              </TooltipContent>
            </Tooltip>
          )}
        </div>
        <div
          className={cn(
            'truncate text-[11.5px]',
            orphan ? 'text-muted-foreground' : 'text-foreground/80'
          )}
        >
          {row.title || 'untitled'}
        </div>
      </button>

      <div className="flex shrink-0 flex-col items-end gap-0.5">
        <SnapshotChip row={row} onOpen={() => onOpenSnapshot(row.key)} />
        <span className="px-1 text-[10px] text-muted-foreground" title="claims citing this source">
          {row.uses}×
        </span>
      </div>

      <Popover>
        <PopoverTrigger asChild>
          <Button
            size="icon-xs"
            variant="ghost"
            title="Evidence"
            className="mt-0.5 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100"
          >
            <Info />
          </Button>
        </PopoverTrigger>
        <PopoverContent side="left" align="start" className="w-80 p-3">
          <EvidencePopover source={row} onOpenSnapshot={onOpenSnapshot} />
        </PopoverContent>
      </Popover>
    </div>
  )
}

function SnapshotChip({ row, onOpen }: { row: SourceRow; onOpen: () => void }) {
  const archived = row.snapshot
  return (
    <button
      onClick={onOpen}
      disabled={!archived}
      title={
        archived
          ? `archived ${shortDate(archived.fetched)} · sha256 ${archived.sha256.slice(0, 12)} — open the copy`
          : 'not archived — report-maker cite --refresh keeps a copy'
      }
      className={cn(
        'flex items-center gap-1 rounded-sm px-1 py-0.5 text-[10px] whitespace-nowrap',
        archived
          ? 'text-foreground/70 hover:bg-accent hover:text-foreground'
          : 'cursor-default text-muted-foreground/70'
      )}
    >
      {archived ? <Archive className="size-3" /> : <ArchiveX className="size-3" />}
      {archived ? shortDate(archived.fetched) : 'not archived'}
    </button>
  )
}

// ── Adding one ───────────────────────────────────────────────────────────────

type AddProps = {
  vault: string
  reportId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Pre-filled URL — a drop onto the window arrives this way. */
  defaultUrl?: string
  /** Called once `cite` has succeeded, so the caller can reload. */
  onAdded: () => void
}

/**
 * `report-maker cite <report> <url>`: fetch the page, archive it beside the
 * report, write the entry. The dialog collects a URL and reports what happened;
 * every decision — the key, the metadata, whether a snapshot is possible — is
 * the engine's.
 *
 * Exported so the same flow can be reached from a dropped URL or the palette
 * without a second implementation of it.
 */
export function AddSourceDialog({
  vault,
  reportId,
  open,
  onOpenChange,
  defaultUrl = '',
  onAdded
}: AddProps) {
  const [url, setUrl] = useState(defaultUrl)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<{ command: string; output: string } | null>(null)
  const [added, setAdded] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  // Reopening with a different URL — a second drop — must not show the last run.
  useEffect(() => {
    if (!open) return
    setUrl(defaultUrl)
    setFailure(null)
    setAdded(null)
    setCopied(false)
  }, [open, defaultUrl])

  async function submit(): Promise<void> {
    const target = url.trim()
    if (!target || busy) return
    setBusy(true)
    setFailure(null)

    // Which key the engine chose is read by comparing the bibliography before
    // and after, rather than by scraping the CLI's prose. The engine owns
    // key-slugging and collision handling; a regex here would be a second,
    // worse copy of that rule.
    const before = new Set((await loadSources(vault, reportId).catch(() => [])).map((s) => s.key))
    const result = await window.api.engine.run(vault, ['cite', reportId, target])
    if (result.code !== 0) {
      setBusy(false)
      setFailure({
        command: result.command,
        output: (result.stderr || result.stdout || `exit ${result.code}`).trimEnd()
      })
      return
    }

    const after = await loadSources(vault, reportId).catch(() => [])
    const fresh = after.map((s) => s.key).filter((key) => !before.has(key))
    // `cite` is idempotent: citing a URL already in the file adds nothing, and
    // the key it names is the one it printed.
    setAdded(fresh[0] ?? lastKeyIn(result.stdout))
    setBusy(false)
    onAdded()
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Add a source</DialogTitle>
          <DialogDescription>
            The engine fetches the page, archives a copy beside the report, and writes the entry
            into <span className="font-mono">sources.yml</span>.
          </DialogDescription>
        </DialogHeader>

        {added ? (
          <div className="space-y-3">
            <p className="text-sm">Cite it as</p>
            <div className="flex items-center gap-2">
              <code className="flex-1 truncate rounded-md border border-border px-2 py-1.5 font-mono text-sm">
                @{added}
              </code>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  void navigator.clipboard.writeText(`@${added}`)
                  setCopied(true)
                }}
              >
                <Copy className="size-3.5" />
                {copied ? 'Copied' : 'Copy @key'}
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <Input
              autoFocus
              value={url}
              spellCheck={false}
              placeholder="https://…"
              disabled={busy}
              aria-invalid={Boolean(failure)}
              onChange={(event) => setUrl(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  void submit()
                }
              }}
            />
            {failure && (
              // Verbatim, and scrollable rather than clipped: this is the text
              // the writer has to act on — a paywall, a DNS failure, a 403.
              <div className="space-y-1">
                <p className="font-mono text-[10.5px] break-all text-muted-foreground">
                  {failure.command}
                </p>
                <pre className="max-h-48 overflow-auto rounded-md border border-destructive/50 p-2 font-mono text-[11px] whitespace-pre-wrap">
                  {failure.output}
                </pre>
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          {added ? (
            <>
              <Button variant="ghost" size="sm" onClick={() => setAdded(null)}>
                Add another
              </Button>
              <Button size="sm" onClick={() => onOpenChange(false)}>
                Done
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button size="sm" disabled={busy || url.trim().length === 0} onClick={() => void submit()}>
                {busy && <Loader2 className="size-3.5 animate-spin" />}
                {busy ? 'Fetching…' : 'Fetch and cite'}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** The key `cite` printed, used only when the bibliography did not grow — i.e.
 *  when the URL was already cited and the engine is naming the existing entry. */
function lastKeyIn(stdout: string): string | null {
  const found = [...stdout.matchAll(/@([A-Za-z][\w.:+-]*)/g)]
  return found.length > 0 ? found[found.length - 1][1] : null
}
