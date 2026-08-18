import { useEffect, useMemo, useState } from 'react'
import {
  ArrowRight,
  GitBranch,
  GitCommitHorizontal,
  History,
  Images,
  Loader2,
  Minus,
  PenLine,
  Plus,
  RefreshCw,
  TerminalSquare,
  TriangleAlert
} from 'lucide-react'
import type { Change, GitLogEntry, GitState } from '../../../shared/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import {
  actionOf,
  exactDate,
  fileOf,
  groupChanges,
  initRepo,
  isUnsupported,
  pagesShow,
  relativeDate,
  reportIsDirty,
  summarise,
  useDiff,
  useGitState,
  useLog,
  wordDiff,
  type Piece,
  type Trouble
} from '@/lib/git'
import { usePages } from '@/lib/thumbs'
import { cn } from '@/lib/utils'

/**
 * A report, revision by revision.
 *
 * `git log` answers "which bytes moved"; the person whose name is on the cover
 * asks something else — which claims changed, which evidence arrived, which
 * judgement was withdrawn. `report-maker diff --json` answers *that*, and this
 * screen is its reader: a rail of the commits that touched this report, and for
 * any one or two of them, the change list grouped the way the engine groups it.
 *
 * The one computation here is the word diff on a reworded claim, and it is
 * presentation — "this sentence changed" comes from the engine, "these four
 * words changed" is how it gets read. Everything else on this screen is a
 * command's output shown as the command printed it.
 *
 * Two things are said out loud rather than hidden. A vault that is not a
 * repository gets an explainer and one button, not a blank pane; and a revision
 * whose pages were never built says so under its own sha, because a comparison
 * that silently shows today's build beside an old commit is worse than one that
 * shows nothing.
 */

type Props = {
  vault: string
  /** The report this is the history of, as the engine names it. */
  reportId: string | null
  /** Vault-relative path to log. Defaults to the report's folder, so a commit
   *  that only touched `sources.yml` is part of the history too. */
  path?: string
  /** Bumped by the shell after a build, a save or a commit. */
  revision?: number
  /** True when the built pages are older than the file on disk — `list --json`
   *  already says this per report, and the working-tree column should not claim
   *  to show edits that were never rendered. */
  stale?: boolean
  /** Open a vault-relative path with the cursor on a line. */
  onReveal?: (path: string, line: number) => void
  /** The repository changed under us — the shell should re-read what it holds. */
  onChanged?: () => void
  className?: string
}

export function Timeline({
  vault,
  reportId,
  path,
  revision = 0,
  stale,
  onReveal,
  onChanged,
  className
}: Props) {
  const [tick, setTick] = useState(0)
  const beat = revision + tick

  const state = useGitState(vault, beat)
  const logPath = reportId ? (path ?? `reports/${reportId}`) : null
  const history = useLog(vault, state.state?.repo ? logPath : null, beat)

  /** Ask everything on this screen again. */
  const reload = (): void => setTick((n) => n + 1)

  /** The repository itself moved — this screen *and* the shell are out of date. */
  const changed = (): void => {
    reload()
    onChanged?.()
  }

  return (
    <div className={cn('flex h-full min-h-0 flex-col bg-background', className)}>
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-4">
        <History className="size-4 text-muted-foreground" />
        <h2 className="text-sm font-medium">History</h2>
        {reportId && (
          <Badge variant="outline" className="font-mono text-[10px] font-normal">
            {reportId}
          </Badge>
        )}

        <div className="ml-auto flex items-center gap-2">
          {state.state?.repo && state.state.branch && (
            <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
              <GitBranch className="size-3" />
              <span className="font-mono">{state.state.branch}</span>
            </span>
          )}
          {reportId && reportIsDirty(state.state, reportId) && (
            <Badge variant="secondary" className="text-[10px] font-normal">
              uncommitted edits
            </Badge>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            title="Re-read the repository"
            onClick={reload}
          >
            <RefreshCw className={cn('size-3.5', (state.loading || history.loading) && 'animate-spin')} />
          </Button>
        </div>
      </header>

      {!reportId ? (
        <Hollow>
          <p>Open a report to see its history.</p>
          <p className="mt-1">
            The timeline is per report — it lists the commits that touched its folder.
          </p>
        </Hollow>
      ) : state.loading && !state.state ? (
        <Loading />
      ) : state.trouble ? (
        <Centre>
          <TroubleCard
            trouble={state.trouble}
            title={
              state.trouble.kind === 'unsupported'
                ? 'This engine has no sync command yet'
                : 'The repository could not be read'
            }
            note={
              state.trouble.kind === 'unsupported'
                ? 'The timeline reads git through report-maker sync. Update the engine this app is pointed at, and this screen works with no change here.'
                : undefined
            }
            onRetry={reload}
          />
        </Centre>
      ) : state.state && !state.state.repo ? (
        <Centre>
          <InitCard vault={vault} onDone={changed} />
        </Centre>
      ) : (
        <Revisions
          vault={vault}
          reportId={reportId}
          log={history.log}
          loading={history.loading}
          trouble={history.trouble}
          dirty={reportIsDirty(state.state, reportId)}
          state={state.state}
          revision={beat}
          stale={stale}
          onReveal={onReveal}
        />
      )}
    </div>
  )
}

// ── Not a repository yet ─────────────────────────────────────────────────────

/**
 * The explainer, and one button.
 *
 * `git init` is run through `report-maker sync --init` — the engine is the only
 * thing in this system allowed to write vault history, and an app that spawned
 * `git` itself to save a round trip would be a second author of it. An engine
 * with no such verb is reported in its own words, with the command to type.
 */
function InitCard({ vault, onDone }: { vault: string; onDone: () => void }) {
  const [running, setRunning] = useState(false)
  const [failed, setFailed] = useState<{ message: string; unsupported: boolean } | null>(null)

  // The exact line `engine/gitsync.py` tells the writer to run when a vault is
  // not a repository. Quoted rather than paraphrased so it can be pasted.
  const initCommand = `git -C ${vault} init`

  const start = async (): Promise<void> => {
    setRunning(true)
    setFailed(null)
    const result = await initRepo(vault)
    setRunning(false)
    if (result.code === 0) {
      onDone()
      return
    }
    setFailed({
      message: (result.stderr || result.stdout).trim() || `exited ${result.code}`,
      unsupported: isUnsupported(result)
    })
  }

  return (
    <Card className="max-w-xl">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <GitCommitHorizontal className="size-4 text-muted-foreground" />
          This vault is not under version control
        </CardTitle>
        <CardDescription>
          A vault is a folder of plain text. Git is what turns that into a record of how the
          document got to where it is.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <ul className="space-y-1.5 text-xs text-muted-foreground">
          <li className="flex gap-2">
            <span className="text-foreground">·</span>
            Every revision of this report, listed — who changed it and when.
          </li>
          <li className="flex gap-2">
            <span className="text-foreground">·</span>
            What changed between any two of them in the report&rsquo;s own terms: claims reworded,
            sources added or withdrawn, judgements taken back.
          </li>
          <li className="flex gap-2">
            <span className="text-foreground">·</span>
            The built pages of two revisions side by side, when both have been built.
          </li>
          <li className="flex gap-2">
            <span className="text-foreground">·</span>
            A vault you can hand to somebody else, or roll back when a claim turns out to be
            wrong.
          </li>
        </ul>

        <div className="flex items-center gap-2">
          <Button size="sm" className="h-7 gap-1.5 text-xs" disabled={running} onClick={start}>
            {running ? <Loader2 className="size-3.5 animate-spin" /> : <GitCommitHorizontal className="size-3.5" />}
            Initialise git here
          </Button>
          <span className="font-mono text-[10.5px] text-muted-foreground">
            report-maker sync --init
          </span>
        </div>
        <p className="text-[11px] text-muted-foreground">
          That runs <span className="font-mono">git init</span> in{' '}
          <span className="font-mono">{vault}</span> and nothing else — no commit, no remote,
          nothing sent anywhere.
        </p>

        {failed && (
          <div className="space-y-1.5 rounded-md border border-border bg-muted/40 p-2.5">
            <p className="flex items-start gap-1.5 text-[11px] text-destructive">
              <TriangleAlert className="mt-px size-3.5 shrink-0" />
              {failed.unsupported
                ? 'This build of report-maker cannot initialise a repository.'
                : 'The engine refused.'}
            </p>
            <pre className="max-h-32 overflow-auto font-mono text-[10.5px] whitespace-pre-wrap text-muted-foreground">
              {failed.message}
            </pre>
            {failed.unsupported && (
              <div className="space-y-1.5">
                <p className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
                  <TerminalSquare className="mt-px size-3.5 shrink-0" />
                  <span>
                    Run it yourself and reload. The app will not spawn git on your behalf — the
                    engine is the only thing here allowed to write vault history.
                  </span>
                </p>
                <div className="flex items-center gap-2">
                  <code className="min-w-0 flex-1 truncate rounded-sm border border-border bg-background px-2 py-1 font-mono text-[10.5px]">
                    {initCommand}
                  </code>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 text-[11px]"
                    onClick={() => void navigator.clipboard?.writeText(initCommand)}
                  >
                    Copy
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ── The rail, and what a selection shows ─────────────────────────────────────

function Revisions({
  vault,
  reportId,
  log,
  loading,
  trouble,
  dirty,
  state,
  revision,
  stale,
  onReveal
}: {
  vault: string
  reportId: string
  log: GitLogEntry[]
  loading: boolean
  trouble: Trouble | null
  dirty: boolean
  state: GitState | null
  revision: number
  stale?: boolean
  onReveal?: (path: string, line: number) => void
}) {
  // Selection is a set of at most two shas. Held as shas rather than indices so
  // a reload that adds a commit at the top does not silently move it.
  const [selected, setSelected] = useState<string[]>([])

  useEffect(() => {
    setSelected((current) => {
      const kept = current.filter((sha) => log.some((entry) => entry.sha === sha))
      if (kept.length) return kept.length === current.length ? current : kept
      // Nothing chosen, or nothing that survives: the newest commit against the
      // working tree — "what have I done since I last committed".
      return log.length ? [log[0].sha] : []
    })
  }, [log])

  const choose = (sha: string): void =>
    setSelected((current) => {
      if (current.includes(sha)) {
        const kept = current.filter((entry) => entry !== sha)
        return kept.length ? kept : current
      }
      return current.length < 2 ? [...current, sha] : [current[1], sha]
    })

  // `log` is newest first, so filtering it orders a selection for free.
  const chosen = useMemo(
    () => log.filter((entry) => selected.includes(entry.sha)),
    [log, selected]
  )
  const from = chosen.length ? chosen[chosen.length - 1] : null
  const to = chosen.length > 1 ? chosen[0] : null

  const comparison = useDiff(vault, reportId, from?.sha ?? null, to?.sha ?? null, revision)
  const built = usePages(vault, reportId, revision)

  if (trouble) {
    return (
      <Centre>
        <TroubleCard
          trouble={trouble}
          title={
            trouble.kind === 'unsupported'
              ? 'This engine cannot list commits yet'
              : 'The commit list could not be read'
          }
          note={
            trouble.kind === 'unsupported'
              ? 'The rail is report-maker sync --log <path> --json. Until the engine has it, use git log in a terminal.'
              : undefined
          }
        />
      </Centre>
    )
  }

  if (loading && log.length === 0) return <Loading />

  if (log.length === 0) {
    return (
      <Hollow>
        <p>No commit has touched this report yet.</p>
        <p className="mt-1">
          Commit the vault — <span className="font-mono">report-maker sync</span>, or ⌘K →{' '}
          <em>Commit the vault</em> — and its history starts here.
        </p>
      </Hollow>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 overflow-x-auto border-b border-border">
        <div className="flex w-max items-stretch gap-2 p-3">
          {log.map((entry, index) => (
            <CommitCard
              key={entry.sha || `${index}`}
              entry={entry}
              selected={selected.includes(entry.sha)}
              tip={index === 0}
              dirty={index === 0 && dirty}
              onClick={() => choose(entry.sha)}
            />
          ))}
        </div>
      </div>

      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border px-4 text-[11px]">
        {from ? (
          <>
            <span className="font-mono">{from.short}</span>
            <ArrowRight className="size-3 text-muted-foreground" />
            <span className={cn('font-mono', !to && 'text-muted-foreground')}>
              {to ? to.short : 'working tree'}
            </span>
            {comparison.loading && <Loader2 className="size-3 animate-spin text-muted-foreground" />}
            {comparison.diff && (
              <span className="truncate text-muted-foreground">
                {headline(comparison.diff.counts) || 'no changes'}
              </span>
            )}
            <span className="ml-auto text-muted-foreground">
              {to ? (
                <button className="hover:text-foreground" onClick={() => setSelected([from.sha])}>
                  compare against the working tree instead
                </button>
              ) : (
                'pick a second commit to compare two revisions'
              )}
            </span>
          </>
        ) : (
          <span className="text-muted-foreground">Pick a commit above.</span>
        )}
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="pb-8">
          {comparison.note && (
            <p className="border-b border-border bg-muted/40 px-4 py-2 text-[11px] text-muted-foreground">
              {comparison.note}
            </p>
          )}

          <Pages
            reportId={reportId}
            from={from}
            to={to}
            log={log}
            state={state}
            pages={built.pages}
            loading={built.loading}
            stale={stale}
          />

          {comparison.trouble ? (
            <div className="p-4">
              <TroubleCard
                trouble={comparison.trouble}
                title={
                  comparison.trouble.kind === 'unsupported'
                    ? 'This engine has no diff command yet'
                    : 'The change list could not be read'
                }
              />
            </div>
          ) : comparison.loading ? (
            <div className="space-y-2 p-4">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : comparison.diff && comparison.diff.changes.length > 0 ? (
            <Changes
              reportId={reportId}
              changes={comparison.diff.changes}
              counts={comparison.diff.counts}
              onReveal={onReveal}
            />
          ) : (
            <p className="px-4 py-6 text-center text-xs text-muted-foreground">
              {from
                ? `Nothing changed between ${from.short} and ${to ? to.short : 'the working tree'}.`
                : 'Pick a commit above.'}
            </p>
          )}
        </div>
      </ScrollArea>
    </div>
  )
}

function headline(counts: Record<string, Record<string, number>>): string {
  return Object.entries(counts)
    .map(([bucket, actions]) => {
      const total = Object.values(actions).reduce((sum, n) => sum + n, 0)
      return total ? `${total} ${bucket}` : ''
    })
    .filter(Boolean)
    .join(' · ')
}

function CommitCard({
  entry,
  selected,
  tip,
  dirty,
  onClick
}: {
  entry: GitLogEntry
  selected: boolean
  tip: boolean
  dirty: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        'flex w-56 shrink-0 flex-col gap-1 rounded-md border border-border px-2.5 py-2 text-left transition-colors',
        selected ? 'bg-accent ring-1 ring-ring' : 'hover:bg-accent/50'
      )}
      title={exactDate(entry.date)}
    >
      <div className="flex items-center gap-1.5">
        <GitCommitHorizontal className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="font-mono text-[11px]">{entry.short || '—'}</span>
        {tip && (
          <Badge variant="outline" className="px-1 py-0 text-[9px] font-normal">
            {dirty ? 'tip · edited' : 'tip'}
          </Badge>
        )}
        <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
          {relativeDate(entry.date)}
        </span>
      </div>
      <span className="line-clamp-2 text-[12px] leading-snug">{entry.subject || '(no subject)'}</span>
      <span className="truncate text-[10px] text-muted-foreground">{entry.author}</span>
    </button>
  )
}

// ── The change list ──────────────────────────────────────────────────────────

function Changes({
  reportId,
  changes,
  counts,
  onReveal
}: {
  reportId: string
  changes: Change[]
  counts: Record<string, Record<string, number>>
  onReveal?: (path: string, line: number) => void
}) {
  const groups = useMemo(() => groupChanges(changes), [changes])

  return (
    <div>
      {groups.map((group) => (
        <section key={group.bucket}>
          <header className="flex items-baseline gap-2 border-b border-border bg-muted/30 px-4 py-1.5">
            <h3 className="text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
              {group.bucket}
            </h3>
            <span className="text-[10.5px] text-muted-foreground">
              {summarise(counts[group.bucket]) || `${group.changes.length}`}
            </span>
          </header>
          <ul>
            {group.changes.map((change, index) => (
              <ChangeRow
                key={`${change.kind}:${change.key}:${index}`}
                reportId={reportId}
                change={change}
                onReveal={onReveal}
              />
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}

const ACTION_ICON: Record<string, typeof Plus> = {
  added: Plus,
  removed: Minus,
  changed: PenLine
}

function ChangeRow({
  reportId,
  change,
  onReveal
}: {
  reportId: string
  change: Change
  onReveal?: (path: string, line: number) => void
}) {
  const action = actionOf(change.kind)
  const Icon = ACTION_ICON[action] ?? PenLine
  const file = fileOf(reportId, change.kind)
  const line = change.line

  return (
    <li className="border-b border-border px-4 py-2.5 last:border-b-0">
      <div className="flex items-center gap-2">
        <Icon
          className={cn('size-3.5 shrink-0', action === 'removed' ? 'text-destructive' : 'text-muted-foreground')}
        />
        <span className="text-[10px] tracking-wide text-muted-foreground uppercase">{action}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px]" title={change.key}>
          {change.key}
        </span>
        {line != null && onReveal && (
          <button
            className="shrink-0 font-mono text-[10.5px] text-muted-foreground hover:text-foreground"
            title={`${file}:${line}`}
            onClick={() => onReveal(file, line)}
          >
            {file.split('/').pop()}:{line}
          </button>
        )}
      </div>

      {change.before !== null && change.after !== null ? (
        <Reworded before={change.before} after={change.after} />
      ) : change.after !== null ? (
        <p className="mt-1.5 text-[12px] leading-relaxed whitespace-pre-wrap">{change.after}</p>
      ) : change.before !== null ? (
        <p className="mt-1.5 text-[12px] leading-relaxed whitespace-pre-wrap text-muted-foreground line-through decoration-muted-foreground/50">
          {change.before}
        </p>
      ) : null}
    </li>
  )
}

/**
 * The before and the after, with the words that actually moved picked out.
 *
 * Two columns, because the eye compares two sentences by scanning them in
 * parallel, and stacked on a narrow pane where two columns would be two very
 * thin ones. The old side is struck through whole and muted — it is gone — and
 * the changed words inside it are the only red on this screen. The new side is
 * plain text with the changed words lifted, so the sentence still reads as a
 * sentence.
 */
function Reworded({ before, after }: { before: string; after: string }) {
  const diff = useMemo(() => wordDiff(before, after), [before, after])

  return (
    <div className="mt-1.5 grid gap-2 sm:grid-cols-2">
      <div className="rounded-md border border-border bg-muted/40 px-2.5 py-2">
        <div className="mb-1 text-[9.5px] tracking-widest text-muted-foreground uppercase">was</div>
        <p className="text-[12px] leading-relaxed whitespace-pre-wrap text-muted-foreground line-through decoration-muted-foreground/50">
          <Pieces pieces={diff.before} tone="gone" />
        </p>
      </div>
      <div className="rounded-md border border-border px-2.5 py-2">
        <div className="mb-1 text-[9.5px] tracking-widest text-muted-foreground uppercase">now</div>
        <p className="text-[12px] leading-relaxed whitespace-pre-wrap">
          <Pieces pieces={diff.after} tone="new" />
        </p>
      </div>
    </div>
  )
}

function Pieces({ pieces, tone }: { pieces: Piece[]; tone: 'gone' | 'new' }) {
  return (
    <>
      {pieces.map((piece, index) =>
        piece.changed ? (
          <span
            key={index}
            className={cn(
              'rounded-[3px]',
              tone === 'gone'
                ? 'bg-destructive/10 text-destructive'
                : 'bg-primary/10 font-medium text-foreground'
            )}
          >
            {piece.text}
          </span>
        ) : (
          <span key={index}>{piece.text}</span>
        )
      )}
    </>
  )
}

// ── Built pages, side by side ────────────────────────────────────────────────

function Pages({
  reportId,
  from,
  to,
  log,
  state,
  pages,
  loading,
  stale
}: {
  reportId: string
  from: GitLogEntry | null
  to: GitLogEntry | null
  log: GitLogEntry[]
  state: GitState | null
  pages: string[]
  loading: boolean
  stale?: boolean
}) {
  if (!from) return null

  // `out/pages/<id>/` is the working tree's build and nothing archives it per
  // commit, so a revision may claim it only when it *is* the working tree —
  // `pagesShow` is where that rule is written down.
  const forRevision = (entry: GitLogEntry): Side =>
    pagesShow(entry.sha, log, state, reportId)
      ? {
          label: entry.short,
          url: pages[0] ?? null,
          count: pages.length,
          note: pages.length ? null : `${entry.short} is built from files that have no pages on disk yet`
        }
      : {
          label: entry.short,
          url: null,
          count: 0,
          note: `no built pages for ${entry.short} — a vault keeps one build, not one per commit`
        }

  const left = forRevision(from)
  const right: Side = to
    ? forRevision(to)
    : {
        label: 'working tree',
        url: pages[0] ?? null,
        count: pages.length,
        note: pages.length
          ? stale
            ? 'built before the latest edits — press ⌘B to bring it up to date'
            : null
          : 'no built pages for the working tree — press ⌘B to build it'
      }

  return (
    <section className="border-b border-border">
      <header className="flex items-baseline gap-2 border-b border-border bg-muted/30 px-4 py-1.5">
        <h3 className="flex items-center gap-1.5 text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
          <Images className="size-3" />
          Pages
        </h3>
      </header>
      <div className="grid gap-3 p-4 sm:grid-cols-2">
        {/* The tip commit and a clean working tree are the same build, and
            printing one cover twice reads as a rendering bug rather than as the
            fact it is. Say the fact instead. */}
        {left.url && left.url === right.url ? (
          <PageSide
            className="sm:col-span-2"
            side={{ ...left, label: `${left.label} · ${right.label}`, note: 'the same build — nothing to compare' }}
            loading={loading}
          />
        ) : (
          <>
            <PageSide side={left} loading={loading} />
            <PageSide side={right} loading={loading} />
          </>
        )}
      </div>
    </section>
  )
}

type Side = { label: string; url: string | null; count: number; note: string | null }

function PageSide({
  side,
  loading,
  className
}: {
  side: Side
  loading: boolean
  className?: string
}) {
  return (
    <figure className={cn('flex min-w-0 flex-col gap-1.5', className)}>
      <figcaption className="flex items-baseline gap-2">
        <span className="font-mono text-[11px]">{side.label}</span>
        {side.count > 1 && (
          <span className="text-[10px] text-muted-foreground">page 1 of {side.count}</span>
        )}
      </figcaption>
      <div className="flex min-h-24 items-center justify-center rounded-md border border-border bg-muted/30 p-2">
        {loading ? (
          <Skeleton className="h-40 w-28" />
        ) : side.url ? (
          <img
            src={side.url}
            alt={`First page at ${side.label}`}
            className="max-h-72 w-auto max-w-full rounded-sm border border-border bg-background object-contain"
          />
        ) : (
          <p className="px-3 py-6 text-center text-[11px] text-muted-foreground">{side.note}</p>
        )}
      </div>
      {side.url && side.note && (
        <p className="text-[10.5px] text-muted-foreground">{side.note}</p>
      )}
    </figure>
  )
}

// ── Shared furniture ─────────────────────────────────────────────────────────

function Hollow({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center px-8 text-center text-xs text-muted-foreground">
      <div className="max-w-sm">{children}</div>
    </div>
  )
}

function Centre({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 items-start justify-center overflow-auto p-6">{children}</div>
  )
}

function Loading() {
  return (
    <div className="space-y-3 p-4">
      <div className="flex gap-2">
        <Skeleton className="h-16 w-56" />
        <Skeleton className="h-16 w-56" />
        <Skeleton className="h-16 w-56" />
      </div>
      <Skeleton className="h-4 w-48" />
      <Skeleton className="h-24 w-full" />
    </div>
  )
}

/** A command that could not be run, quoted rather than summarised. */
function TroubleCard({
  trouble,
  title,
  note,
  onRetry
}: {
  trouble: Trouble
  title: string
  note?: string
  onRetry?: () => void
}) {
  return (
    <Card className="max-w-xl">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <TriangleAlert className="size-4 text-destructive" />
          {title}
        </CardTitle>
        {note && <CardDescription>{note}</CardDescription>}
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="font-mono text-[10.5px] text-muted-foreground">{trouble.command}</p>
        <pre className="max-h-40 overflow-auto rounded-md border border-border bg-muted/40 p-2.5 font-mono text-[10.5px] whitespace-pre-wrap">
          {trouble.message}
        </pre>
        {onRetry && (
          <Button variant="secondary" size="sm" className="h-7 gap-1.5 text-xs" onClick={onRetry}>
            <RefreshCw className="size-3.5" />
            Try again
          </Button>
        )}
      </CardContent>
    </Card>
  )
}
