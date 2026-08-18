import { Eye, GitBranch, Loader2, MapPin, Terminal } from 'lucide-react'
import type { Finding, GitState, ReportScore } from '../../../shared/types'
import { TodoChip } from './NotesPanel'
import { ProblemsChip } from './Problems'
import type { Todo } from '@/lib/notes'
import { cn } from '@/lib/utils'

/** What the last `report-maker sync` said, in its own words. */
export type GitNote = { text: string; failed: boolean }

/**
 * Where the cursor is, and which page that probably is. `page` is an estimate —
 * see {@link StatusBar} — so it is printed with a `~` and never without `pages`.
 */
export type Cursor = { line: number; page: number | null; pages: number | null }

type Props = {
  /** Absolute path of the CLI the app found, from `engine.where()`. */
  engine: string
  /** The last thing that happened, in the engine's words. */
  status: string
  busy: boolean
  git: GitState | null
  gitNote: GitNote | null
  syncing: boolean
  /** The branch chip was clicked — show the version timeline. */
  onGit: () => void
  /** The sync note was clicked — show the output that produced it. */
  onGitNote: () => void
  findings: Finding[]
  problemsOpen: boolean
  onToggleProblems: () => void
  /**
   * The open report's pad, as the shell already has it.
   *
   * Handed down rather than fetched: the Notes panel is a sidebar tab that only
   * exists while it is selected, so a hook living in it could not feed a chip
   * that has to be readable with the sidebar shut. One `todos --json` answers
   * both.
   */
  todos: Todo[]
  /** True while the sidebar is showing the Notes tab, so the chip reads as the
   *  handle for a panel that is already open. */
  notesOpen: boolean
  /** Show the sidebar and put it on Notes. */
  onTodos: () => void
  /** Evidence density for the open report, when the engine has answered. */
  score: ReportScore | null
  /** True while the buffer differs from the file the score was measured on. */
  scoreStale: boolean
  onScore: () => void
  cursor: Cursor | null
  watching: boolean
  onWatch: () => void
  onEngine: () => void
}

/**
 * The bottom rail: git, findings, evidence density, where the cursor is, and
 * which engine is answering.
 *
 * Every item is a button that opens the thing it describes, because a status bar
 * that only reports is a status bar people stop reading. Nothing here is
 * computed: the branch comes from `sync --status`, the findings from `check`,
 * the density from `score`. The one exception is the page estimate, and it is
 * marked as an estimate wherever it appears.
 */
export function StatusBar({
  engine,
  status,
  busy,
  git,
  gitNote,
  syncing,
  onGit,
  onGitNote,
  findings,
  problemsOpen,
  onToggleProblems,
  todos,
  notesOpen,
  onTodos,
  score,
  scoreStale,
  onScore,
  cursor,
  watching,
  onWatch,
  onEngine
}: Props) {
  return (
    <footer className="flex h-6 shrink-0 items-center gap-1 border-t border-border px-2 text-[11px] text-muted-foreground">
      <GitChip git={git} syncing={syncing} onClick={onGit} />
      {gitNote && (
        <button
          type="button"
          onClick={onGitNote}
          title="Show the output this came from"
          className={cn(
            'inline-flex h-5 max-w-[280px] items-center rounded-sm px-1.5 hover:bg-accent',
            gitNote.failed ? 'text-destructive' : 'text-muted-foreground'
          )}
        >
          <span className="truncate">{gitNote.text}</span>
        </button>
      )}

      <button
        type="button"
        onClick={onWatch}
        aria-pressed={watching}
        title={watching ? 'Stop the live rebuild' : 'Rebuild this report on every change'}
        className={cn(
          'inline-flex h-5 items-center gap-1 rounded-sm px-1.5 hover:bg-accent',
          watching ? 'bg-accent text-foreground' : 'text-muted-foreground'
        )}
      >
        <Eye className="size-3" />
        watch
      </button>

      {/* The running commentary. The Problems panel holds the whole output; this
          is the one line worth glancing at. */}
      <span className="min-w-0 flex-1 truncate px-1.5" title={status}>
        {busy ? <Loader2 className="mr-1 inline size-3 animate-spin align-[-2px]" /> : null}
        {status}
      </span>

      {cursor && <CursorChip cursor={cursor} />}
      {score && <Density score={score} stale={scoreStale} onClick={onScore} />}

      {/* Beside the problems chip on purpose: both count things waiting to be
          dealt with, and the pad is the half of that list the citation rule does
          not reach. */}
      <TodoChip todos={todos} active={notesOpen} onClick={onTodos} />

      <ProblemsChip
        findings={findings}
        open={problemsOpen}
        busy={busy}
        onToggle={onToggleProblems}
      />

      <button
        type="button"
        onClick={onEngine}
        title={`report-maker: ${engine}`}
        className="inline-flex h-5 max-w-[220px] items-center gap-1 rounded-sm px-1.5 hover:bg-accent"
      >
        <Terminal className="size-3 shrink-0" />
        <span className="truncate font-mono">{basename(engine)}</span>
      </button>
    </footer>
  )
}

// ── the pieces ───────────────────────────────────────────────────────────────

function GitChip({
  git,
  syncing,
  onClick
}: {
  git: GitState | null
  syncing: boolean
  onClick: () => void
}) {
  const dirty = git?.dirty.length ?? 0
  const label = !git?.repo ? 'no git' : (git.branch ?? 'detached')
  const behind = git?.behind ?? 0
  const ahead = git?.ahead ?? 0

  return (
    <button
      type="button"
      onClick={onClick}
      title={
        git?.repo
          ? [
              `branch ${git.branch ?? 'detached HEAD'}`,
              git.upstream ? `upstream ${git.upstream}` : 'no upstream',
              `${dirty} uncommitted file(s)`,
              `${ahead} ahead, ${behind} behind`
            ].join(' · ')
          : 'This vault is not a git repository — open the timeline to start one'
      }
      className={cn(
        'inline-flex h-5 items-center gap-1 rounded-sm px-1.5 hover:bg-accent',
        // Behind the remote is the one state that stops `sync --push`, so it is
        // the one state worth colouring.
        behind > 0 ? 'text-destructive' : 'text-muted-foreground'
      )}
    >
      {syncing ? <Loader2 className="size-3 animate-spin" /> : <GitBranch className="size-3" />}
      <span className="max-w-[160px] truncate">{label}</span>
      {dirty > 0 && <span className="text-foreground">●{dirty}</span>}
      {ahead > 0 && <span>↑{ahead}</span>}
      {behind > 0 && <span>↓{behind}</span>}
    </button>
  )
}

function CursorChip({ cursor }: { cursor: Cursor }) {
  const { line, page, pages } = cursor
  return (
    <span
      className="inline-flex h-5 items-center gap-1 px-1.5 font-mono"
      title={
        page && pages
          ? `line ${line} · roughly page ${page} of ${pages}. Estimated from how far ` +
            'through the file the cursor sits — Typst reports no source map, so this ' +
            'is proportion, not SyncTeX.'
          : `line ${line}`
      }
    >
      <MapPin className="size-3" />
      {line}
      {page && pages ? <span className="text-muted-foreground">{` ~p${page}/${pages}`}</span> : null}
    </span>
  )
}

/**
 * The citation rule as a bar: cited, assessed, unmarked. Three segments rather
 * than one percentage because the rule has three answers, and a report that is
 * four-fifths assessment reads nothing like one that is four-fifths cited.
 */
function Density({
  score,
  stale,
  onClick
}: {
  score: ReportScore
  stale: boolean
  onClick: () => void
}) {
  const total = score.cited + score.assessed + score.unmarked
  const percent = Math.round((score.density || 0) * 100)

  return (
    <button
      type="button"
      onClick={onClick}
      title={
        `${score.cited} cited · ${score.assessed} assessed · ${score.unmarked} unmarked` +
        ` · ${score.sourcesCited}/${score.sourcesTotal} sources used` +
        (stale ? ' — measured on the saved file' : '')
      }
      className={cn(
        'inline-flex h-5 items-center gap-1.5 rounded-sm px-1.5 hover:bg-accent',
        stale && 'opacity-40'
      )}
    >
      <span className="flex h-1.5 w-16 overflow-hidden rounded-full bg-border">
        {total > 0 ? (
          <>
            <span className="rail-cited" style={{ width: `${(score.cited / total) * 100}%` }} />
            <span
              className="rail-assessed"
              style={{ width: `${(score.assessed / total) * 100}%` }}
            />
            <span
              className="rail-unmarked"
              style={{ width: `${(score.unmarked / total) * 100}%` }}
            />
          </>
        ) : null}
      </span>
      {total > 0 ? `${percent}%` : 'no prose'}
    </button>
  )
}

function basename(path: string): string {
  const cut = path.lastIndexOf('/')
  return cut === -1 ? path : path.slice(cut + 1)
}
