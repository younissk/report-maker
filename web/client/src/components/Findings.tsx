import { useState } from 'react'
import { ChevronDown, ChevronRight, CircleAlert, FileText, TriangleAlert } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { Finding } from '@/lib/api'
import { explain, groupFindingsByFile, plural, type RevealTarget } from '@/lib/evidence'
import { cn } from '@/lib/utils'

/**
 * The citation rule's verdict, as a list you can act on.
 *
 * Every word on screen came out of `check --json`: the level, the code, the file,
 * the line and the message are the engine's own and are rendered verbatim. This
 * component groups them by file and lays them out. It does not decide what a
 * finding means, does not re-level one, and does not filter — a caller that wants
 * one report's findings hands in one report's findings.
 *
 * Two decisions worth keeping:
 *
 * - **The code chip is a button.** Somebody meeting E012 four minutes after
 *   landing on the site is being refused by a tool they do not know yet, and the
 *   message says what is wrong without saying why anybody would want it that
 *   way. Tapping the chip opens one sentence that does. It is a disclosure, not
 *   a tooltip: there is no hover on a phone and a rule nobody can read on a
 *   phone is a rule that reads as an obstruction.
 * - **Tapping the row jumps to the line.** A finding is a coordinate, and a list
 *   of coordinates you cannot travel to is a list of complaints.
 */

export type FindingsProps = {
  /** `check --json`'s findings, already narrowed to whatever this list is about. */
  findings: Finding[]
  loading?: boolean
  /** The server's own message when the check could not be run. */
  error?: string | null
  /** Engine stderr, when there was any. Rendered verbatim — see `detail`. */
  detail?: string | null
  onRetry?: () => void
  /** Put the cursor on this line in the Write tab. */
  onReveal?: (target: RevealTarget) => void
  /** Shown in place of the list when there is nothing wrong and nothing loading. */
  emptyNote?: string
  className?: string
}

function Severity({ level }: { level: Finding['level'] }) {
  // Severity by shape as well as by hue, so it survives a colour-blind reader
  // and a phone screen in daylight. Same choice the desktop app made.
  return level === 'error' ? (
    <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
  ) : (
    <TriangleAlert className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
  )
}

export function Findings({
  findings,
  loading = false,
  error = null,
  detail = null,
  onRetry,
  onReveal,
  emptyNote,
  className,
}: FindingsProps) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  // Keyed by row, not by code: two E012s on two lines are two places to
  // look, and expanding both because they share a code buries the second one
  // under a paragraph the reader has already read.
  const [openRow, setOpenRow] = useState<string | null>(null)

  if (loading && findings.length === 0) {
    return (
      <div className={cn('flex flex-col gap-2 p-3', className)}>
        {[0, 1, 2, 3].map((n) => (
          <Skeleton key={n} className="h-14 w-full" />
        ))}
      </div>
    )
  }

  if (error) {
    return (
      <div className={cn('flex flex-col items-start gap-3 p-3', className)}>
        <p className="text-sm text-muted-foreground break-anywhere">
          The citation rule could not be checked. {error}
        </p>
        {detail && (
          // The engine's refusals name the command that fixes them. Verbatim, in
          // its own box, scrolling sideways inside itself so a long line can
          // never drag the page with it.
          <pre className="scroll-x max-h-48 w-full overflow-y-auto rounded-md border bg-muted p-3 font-mono text-[11px] whitespace-pre">
            {detail}
          </pre>
        )}
        {onRetry && (
          <Button variant="outline" onClick={onRetry}>
            Check again
          </Button>
        )}
      </div>
    )
  }

  if (findings.length === 0) {
    return (
      <div className={cn('flex flex-col items-center gap-1 px-6 py-10 text-center', className)}>
        {/* The CLI's own words for this state. A tool that says one thing in the
            terminal and another in a browser is two tools. */}
        <p className="font-mono text-sm">cited or opinion — no findings</p>
        {emptyNote && (
          <p className="max-w-xs text-xs text-muted-foreground break-anywhere">{emptyNote}</p>
        )}
      </div>
    )
  }

  const groups = groupFindingsByFile(findings)

  return (
    <ul className={cn('flex flex-col', className)}>
      {groups.map((group) => {
        const shut = collapsed[group.path] ?? false
        const Chevron = shut ? ChevronRight : ChevronDown
        return (
          <li key={group.path} className="border-b last:border-b-0">
            <button
              type="button"
              onClick={() =>
                setCollapsed((state) => ({ ...state, [group.path]: !shut }))
              }
              aria-expanded={!shut}
              className="tap flex w-full items-center gap-2 px-3 py-2 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset active:bg-accent lg:min-h-9 hover:bg-accent"
            >
              <Chevron className="size-4 shrink-0 text-muted-foreground" aria-hidden />
              <FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-mono text-[12px] font-medium">
                  {group.name}
                </span>
                <span className="block truncate font-mono text-[10px] text-muted-foreground">
                  {group.path}
                </span>
              </span>
              <span className="flex shrink-0 items-center gap-1">
                {group.errors > 0 && (
                  <Badge variant="error">{group.errors}</Badge>
                )}
                {group.warnings > 0 && (
                  <Badge variant="warning">{group.warnings}</Badge>
                )}
              </span>
            </button>

            {!shut && (
              <ul className="divide-y border-t">
                {group.findings.map((finding, index) => {
                  const rowId = `${finding.path}:${finding.line}:${finding.code}:${index}`
                  const codeOpen = openRow === rowId
                  const sentence = explain(finding.code)
                  return (
                    <li key={rowId}>
                      <div className="flex items-stretch gap-0">
                        {/* Two targets, side by side, each past 44px: the chip
                            explains the rule, the body travels to the line.
                            Nested buttons are invalid, so they are siblings. */}
                        <button
                          type="button"
                          onClick={() => setOpenRow(codeOpen ? null : rowId)}
                          aria-expanded={codeOpen}
                          aria-label={
                            sentence
                              ? `${finding.code} — what this rule protects`
                              : finding.code
                          }
                          className="tap flex shrink-0 items-start justify-center px-3 pt-2.5 outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset active:bg-accent lg:min-h-0 lg:min-w-0 lg:px-2 lg:pt-2 hover:bg-accent"
                        >
                          <Badge
                            variant={finding.level === 'error' ? 'error' : 'warning'}
                            className="font-mono"
                          >
                            {finding.code}
                          </Badge>
                        </button>

                        <button
                          type="button"
                          onClick={() =>
                            onReveal?.({
                              report: finding.report,
                              path: finding.path,
                              line: finding.line,
                            })
                          }
                          disabled={!onReveal}
                          className="tap flex min-w-0 flex-1 items-start gap-2 py-2 pr-3 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset disabled:cursor-default active:bg-accent lg:min-h-0 hover:bg-accent disabled:hover:bg-transparent"
                        >
                          <Severity level={finding.level} />
                          <span className="min-w-0 flex-1">
                            {/* User text and engine text alike. React escapes
                                it; nothing here reaches for innerHTML. */}
                            <span className="block text-sm leading-snug break-anywhere">
                              {finding.message}
                            </span>
                            <span className="mt-0.5 block font-mono text-[10px] text-muted-foreground tabular-nums">
                              line {finding.line}
                            </span>
                          </span>
                        </button>
                      </div>

                      {codeOpen && (
                        <p className="border-t bg-muted/50 px-3 py-2.5 text-[12px] leading-relaxed text-muted-foreground break-anywhere">
                          {sentence ??
                            `${finding.code} is a rule this build of the app has no note for. The message above is the engine's own.`}
                        </p>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
          </li>
        )
      })}
    </ul>
  )
}

/** `3 errors · 1 warning`, or nothing at all when a list is clean. */
export function findingsSummary(findings: Finding[]): string {
  const errors = findings.filter((finding) => finding.level === 'error').length
  const warnings = findings.length - errors
  if (findings.length === 0) return 'no findings'
  if (errors === 0) return plural(warnings, 'warning')
  if (warnings === 0) return plural(errors, 'error')
  return `${plural(errors, 'error')} · ${plural(warnings, 'warning')}`
}
