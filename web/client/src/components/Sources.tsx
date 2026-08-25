import { Archive, ArchiveX, Check, Copy, ExternalLink, Plus } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { SourceRow } from '@/lib/api'
import { plural, safeHref, shortDate, useCopy, type RevealTarget } from '@/lib/evidence'
import { cn } from '@/lib/utils'

/**
 * The report's bibliography, as the engine reports it.
 *
 * `sources --json` answers every question on screen — the key, the type, the
 * title, how many claims cite it, and whether a page was archived and when. None
 * of it is derived here, which is the only way this panel and `check` can be
 * guaranteed to agree.
 *
 * A source nothing cites is rendered muted and says so. It is not an error and
 * must not look like one: every source in `sources.yml` reaches the References
 * section whether or not a `@key` points at it, so that section doubles as the
 * inventory of what was reviewed. Rendering an orphan as a failure would push
 * people to delete the record of a page they read and decided against, which is
 * exactly the wrong lesson.
 */

export type SourcesProps = {
  /** The report whose bibliography this is; null when none is selected. */
  reportId: string | null
  sources: SourceRow[]
  loading?: boolean
  error?: string | null
  detail?: string | null
  onRetry?: () => void
  /** Open the cite sheet. Absent hides the action. */
  onCite?: () => void
  /** Put the cursor on the key's line in `sources.yml`. */
  onReveal?: (target: RevealTarget) => void
  className?: string
}

export function Sources({
  reportId,
  sources,
  loading = false,
  error = null,
  detail = null,
  onRetry,
  onCite,
  onReveal,
  className,
}: SourcesProps) {
  const { copied, copy } = useCopy()

  if (!reportId) {
    return (
      <p className={cn('px-6 py-10 text-center text-sm text-muted-foreground', className)}>
        Select a report to see the sources behind it.
      </p>
    )
  }

  if (loading && sources.length === 0) {
    return (
      <div className={cn('flex flex-col gap-2 p-3', className)}>
        {[0, 1, 2].map((n) => (
          <Skeleton key={n} className="h-20 w-full" />
        ))}
      </div>
    )
  }

  if (error) {
    return (
      <div className={cn('flex flex-col items-start gap-3 p-3', className)}>
        <p className="text-sm text-muted-foreground break-anywhere">
          The bibliography could not be read. {error}
        </p>
        {detail && (
          <pre className="scroll-x max-h-48 w-full overflow-y-auto rounded-md border bg-muted p-3 font-mono text-[11px] whitespace-pre">
            {detail}
          </pre>
        )}
        {onRetry && (
          <Button variant="outline" onClick={onRetry}>
            Try again
          </Button>
        )}
      </div>
    )
  }

  if (sources.length === 0) {
    return (
      <div className={cn('flex flex-col items-center gap-3 px-6 py-10 text-center', className)}>
        <p className="text-sm text-muted-foreground">
          No sources yet. Start <span className="font-mono">sources.yml</span> before
          the prose — a claim with no key to point at is an opinion.
        </p>
        {onCite && (
          <Button onClick={onCite}>
            <Plus aria-hidden />
            Cite a URL
          </Button>
        )}
      </div>
    )
  }

  return (
    <ul className={cn('flex flex-col divide-y', className)}>
      {sources.map((source) => {
        const orphan = source.uses === 0
        const href = safeHref(source.url)
        const justCopied = copied === source.key
        return (
          <li key={`${source.key}:${source.line}`} className="px-2 py-1.5">
            <div className="flex items-start gap-1">
              {/* The primary target: a tap puts `@key` on the clipboard, which
                  is the one thing anybody wants from a bibliography row while
                  they are writing the sentence that needs it. */}
              <button
                type="button"
                onClick={() => void copy(`@${source.key}`, source.key)}
                aria-label={`Copy @${source.key}`}
                className="tap flex min-w-0 flex-1 flex-col items-start gap-1 rounded-md px-2 py-2 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 active:bg-accent lg:min-h-0 hover:bg-accent"
              >
                <span className="flex w-full min-w-0 items-center gap-1.5">
                  <span
                    className={cn(
                      'min-w-0 truncate font-mono text-[13px] font-medium',
                      orphan && 'text-muted-foreground'
                    )}
                  >
                    @{source.key}
                  </span>
                  {justCopied ? (
                    <Check className="size-3.5 shrink-0 text-rail-cited" aria-hidden />
                  ) : (
                    <Copy className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                  )}
                  <span className="sr-only" role="status">
                    {justCopied ? `@${source.key} copied` : ''}
                  </span>
                </span>

                {/* User text. Escaped by React, wrapped by `break-anywhere` so a
                    120-character page title cannot widen the layout. */}
                <span
                  className={cn(
                    'block text-[13px] leading-snug break-anywhere',
                    orphan ? 'text-muted-foreground' : 'text-foreground'
                  )}
                >
                  {source.title || <span className="text-muted-foreground">Untitled</span>}
                </span>

                <span className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                  {source.type && (
                    <Badge variant="outline" className="font-mono">
                      {source.type}
                    </Badge>
                  )}
                  <span className="tabular-nums">
                    {orphan ? 'cited nowhere' : plural(source.uses, 'use')}
                  </span>
                  {source.snapshot ? (
                    <Badge variant="cited" title={source.snapshot.sha256}>
                      <Archive aria-hidden />
                      archived {shortDate(source.snapshot.fetched)}
                    </Badge>
                  ) : (
                    <Badge variant="outline" className="text-muted-foreground">
                      <ArchiveX aria-hidden />
                      not archived
                    </Badge>
                  )}
                </span>

                {orphan && (
                  <span className="block text-[11px] leading-snug text-muted-foreground break-anywhere">
                    Nothing cites this. It still reaches the References section —
                    that list is the record of what was reviewed, not only of what
                    was used.
                  </span>
                )}
              </button>

              {href && (
                // `noreferrer` as well as `noopener`: the target has no business
                // learning which page linked to it from a private vault.
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={`Open ${source.url} in a new tab`}
                  className="tap inline-flex shrink-0 items-center justify-center rounded-md text-muted-foreground outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 active:bg-accent lg:size-9 lg:min-h-0 lg:min-w-0 hover:bg-accent hover:text-foreground"
                >
                  <ExternalLink className="size-4" aria-hidden />
                </a>
              )}
            </div>

            {onReveal && (
              <button
                type="button"
                onClick={() =>
                  onReveal({
                    report: reportId,
                    path: `reports/${reportId}/sources.yml`,
                    line: source.line,
                  })
                }
                className="tap ml-2 inline-flex items-center rounded-md px-2 font-mono text-[10px] text-muted-foreground outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 active:bg-accent lg:min-h-8 hover:bg-accent hover:text-foreground"
              >
                sources.yml:{source.line}
              </button>
            )}
          </li>
        )
      })}
    </ul>
  )
}
