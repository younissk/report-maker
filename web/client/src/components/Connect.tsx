import { useEffect, useId, useState, type ReactNode } from 'react'
import {
  Check,
  CloudUpload,
  GitBranch,
  GitCommitHorizontal,
  Loader2,
  Lock,
  RotateCw,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
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
import type { GitState, Repo, Run } from '@/lib/api'
import { isClean, pushBlocked, useConnect, type Failure } from '@/lib/github'
import { cn, useIsDesktop } from '@/lib/utils'

/**
 * Connecting a repository, and getting work back out of the browser.
 *
 * A try-mode session is a temporary vault that the server sweeps after a day.
 * Connecting a repo is the transition out of that: the repo becomes the store,
 * and `sync` is how anything written here leaves the machine. So this panel is
 * mostly two screens — pick a repo, then commit and push — with the git state
 * in between so the second one is never a guess.
 *
 * Three rules it keeps:
 *
 *   No dead buttons. When the server has no GitHub app configured, it says so
 *   in one sentence and stops, rather than showing a control that cannot work.
 *
 *   No token anywhere. Credentials live in the session record server-side and
 *   no route returns one. There is no field for one here and there never is.
 *
 *   No push that nobody asked for. Commit and push are separate taps, and the
 *   engine's own refusal — no upstream, behind the remote, nothing staged — is
 *   printed verbatim, because each one names the command that fixes it.
 */

// ── The overlay ──────────────────────────────────────────────────────────────

export type ConnectProps = {
  open?: boolean
  onOpenChange?: (open: boolean) => void
  /** Anything that opens it. Wrapped in a trigger; omit when driving `open`. */
  trigger?: ReactNode
}

/** A sheet from the bottom on a phone, a panel from the right on a desktop. */
export function Connect({ open, onOpenChange, trigger }: ConnectProps) {
  const desktop = useIsDesktop()

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      {trigger ? <SheetTrigger asChild>{trigger}</SheetTrigger> : null}
      <SheetContent
        side={desktop ? 'right' : 'bottom'}
        className={cn('gap-0', !desktop && 'max-h-[88dvh]')}
      >
        <SheetHeader>
          <SheetTitle>Repository</SheetTitle>
          <SheetDescription>
            Where this vault lives, and how work leaves the browser.
          </SheetDescription>
        </SheetHeader>
        <Separator />
        <ConnectPanel className="min-h-0 flex-1" />
      </SheetContent>
    </Sheet>
  )
}

// ── The panel ────────────────────────────────────────────────────────────────

export function ConnectPanel({ className }: { className?: string }) {
  const connect = useConnect()
  const { availability, enabled, connected } = connect

  if (!availability) {
    return (
      <div className={cn('pane', className)}>
        <div className="flex flex-col gap-2 p-4">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-12 w-full" />
        </div>
      </div>
    )
  }

  if (!enabled) {
    return (
      <div className={cn('pane', className)}>
        <div className="px-4 py-4 text-sm leading-relaxed">
          <p>This server has no GitHub connection configured, so a repository cannot be connected here.</p>
          <p className="mt-3 text-muted-foreground">
            Everything else works: the vault you are in is this session's own, and
            a share link publishes a built report with its evidence to anyone who
            has the link.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className={cn('pane', className)}>
      {connected ? <Connected connect={connect} /> : <Picker connect={connect} />}
    </div>
  )
}

// ── Picking a repository ─────────────────────────────────────────────────────

function Picker({ connect }: { connect: ReturnType<typeof useConnect> }) {
  const { repos, loadingRepos, reposError, loadRepos, connecting, connectError } = connect
  const [chosen, setChosen] = useState<Repo | null>(null)
  const [branch, setBranch] = useState('')
  const branchId = useId()

  // Asked for once, when the panel is opened. Listing somebody's repositories
  // spends their GitHub rate limit, so it is not done on a glance.
  useEffect(() => loadRepos(), [loadRepos])

  return (
    <div className="pb-4">
      <div className="px-4 pt-3 pb-2 text-sm leading-relaxed text-muted-foreground">
        <p>
          Pick a repository and it is cloned into this session. From then on the
          repository is the store — this server keeps nothing of its own.
        </p>
        <p className="mt-2">
          A clone brings the repository's own contents. Anything written in this
          temporary vault and not committed does not travel with it.
        </p>
      </div>

      {connectError && <Refusal failure={connectError} />}
      {reposError && <Refusal failure={reposError} onRetry={loadRepos} />}

      {loadingRepos && repos.length === 0 && (
        <div className="flex flex-col gap-2 p-3">
          {[0, 1, 2, 3].map((row) => (
            <Skeleton key={row} className="h-12 w-full" />
          ))}
        </div>
      )}

      {!loadingRepos && !reposError && repos.length === 0 && (
        <div className="px-4 py-3 text-sm text-muted-foreground">
          <p>No repositories came back for this account.</p>
          <Button variant="outline" size="sm" className="mt-3" onClick={loadRepos}>
            <RotateCw aria-hidden />
            Look again
          </Button>
        </div>
      )}

      <ul className="flex flex-col px-1" role="radiogroup" aria-label="Repositories">
        {repos.map((repo) => {
          const picked = chosen?.full_name === repo.full_name
          return (
            <li key={repo.full_name}>
              <button
                type="button"
                role="radio"
                aria-checked={picked}
                onClick={() => {
                  setChosen(repo)
                  setBranch(repo.default_branch ?? '')
                }}
                className={cn(
                  'flex w-full min-h-11 items-center gap-2 rounded-md px-3 py-2 text-left outline-none',
                  'focus-visible:ring-[3px] focus-visible:ring-ring/50 active:bg-accent',
                  picked && 'bg-accent'
                )}
              >
                <span className="flex size-5 shrink-0 items-center justify-center text-muted-foreground">
                  {picked ? <Check className="size-4" aria-hidden /> : null}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{repo.full_name}</span>
                  <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground">
                    <span className="font-mono">{repo.default_branch}</span>
                    {repo.updated_at && <span>updated {repo.updated_at.slice(0, 10)}</span>}
                  </span>
                </span>
                {repo.private && (
                  <Badge variant="outline" className="shrink-0 font-normal">
                    <Lock aria-hidden />
                    private
                  </Badge>
                )}
              </button>
            </li>
          )
        })}
      </ul>

      {chosen && (
        // In the flow, under the list, rather than pinned to the bottom edge:
        // a fixed bar here is the one the soft keyboard covers the moment the
        // branch field takes focus.
        <div className="mx-3 mt-3 rounded-lg border p-3">
          <p className="text-sm break-anywhere">
            Clone <span className="font-medium">{chosen.full_name}</span> into this session.
          </p>
          <label className="mt-3 block text-[11px] font-medium tracking-widest text-muted-foreground uppercase" htmlFor={branchId}>
            Branch
          </label>
          <Input
            id={branchId}
            value={branch}
            spellCheck={false}
            autoComplete="off"
            placeholder={chosen.default_branch || 'main'}
            onChange={(event) => setBranch(event.target.value)}
            className="mt-1"
          />
          <Button
            className="mt-3 w-full lg:w-auto"
            size="lg"
            disabled={connecting !== null}
            onClick={() => void connect.connect(chosen.full_name, branch.trim() || undefined)}
          >
            {connecting === chosen.full_name ? (
              <>
                <Loader2 className="animate-spin" aria-hidden />
                Cloning…
              </>
            ) : (
              <>
                <GitBranch aria-hidden />
                Connect this repository
              </>
            )}
          </Button>
        </div>
      )}
    </div>
  )
}

// ── Connected ────────────────────────────────────────────────────────────────

function Connected({ connect }: { connect: ReturnType<typeof useConnect> }) {
  const { availability, state, loadingState, stateError, refreshState, syncing, syncError, lastSync } =
    connect
  const [message, setMessage] = useState('')
  const messageId = useId()

  const blocked = pushBlocked(state)
  const clean = isClean(state)
  const canCommit = message.trim().length > 0 && !syncing

  /**
   * The message is cleared only when the engine took the commit. A refusal that
   * also emptied the box would make somebody retype the sentence they had just
   * been told was fine — the problem was never the message.
   */
  async function run(push: boolean): Promise<void> {
    const result = await connect.sync(message, push)
    if (result && (result.result?.code ?? 0) === 0) setMessage('')
  }

  return (
    <div className="pb-4">
      <div className="px-4 pt-3 pb-2">
        <p className="text-sm font-medium break-anywhere">
          {availability?.repo ?? state?.remote ?? 'This vault is a git repository.'}
        </p>
        <p className="mt-0.5 text-[11px] text-muted-foreground break-anywhere">
          {availability?.login ? `${availability.login} · ` : ''}
          {state?.branch ?? availability?.branch ?? '—'}
          {state?.upstream ? ` → ${state.upstream}` : ' · no upstream'}
        </p>
      </div>

      <StateStrip state={state} loading={loadingState} onRefresh={refreshState} />

      {stateError && <Refusal failure={stateError} onRetry={refreshState} />}

      {state && state.dirty.length > 0 && (
        <details className="mx-3 mt-2 rounded-lg border">
          <summary className="flex min-h-11 cursor-pointer items-center px-3 text-sm">
            {state.dirty.length} changed {state.dirty.length === 1 ? 'file' : 'files'}
          </summary>
          <ul className="border-t px-3 py-2">
            {state.dirty.map((path) => (
              <li key={path} className="font-mono text-[11px] text-muted-foreground break-anywhere">
                {path}
              </li>
            ))}
          </ul>
        </details>
      )}

      <Separator className="my-3" />

      <div className="px-3">
        <label
          className="block text-[11px] font-medium tracking-widest text-muted-foreground uppercase"
          htmlFor={messageId}
        >
          Commit message
        </label>
        <Input
          id={messageId}
          value={message}
          spellCheck
          enterKeyHint="done"
          placeholder="What changed, and why"
          onChange={(event) => setMessage(event.target.value)}
          onFocus={(event) => {
            const target = event.currentTarget
            window.setTimeout(
              () => target.scrollIntoView({ block: 'center', behavior: 'smooth' }),
              250
            )
          }}
          className="mt-1"
        />

        <div className="mt-3 flex flex-col gap-2 lg:flex-row">
          <Button
            variant="outline"
            size="lg"
            className="w-full lg:w-auto"
            disabled={!canCommit}
            onClick={() => void run(false)}
          >
            {syncing ? (
              <Loader2 className="animate-spin" aria-hidden />
            ) : (
              <GitCommitHorizontal aria-hidden />
            )}
            Commit
          </Button>
          <Button
            size="lg"
            className="w-full lg:w-auto"
            // A push is the one action here that leaves the machine, so it is
            // its own tap. Nothing in this file ever passes `push: true` on
            // anybody's behalf.
            disabled={!canCommit || blocked !== null}
            onClick={() => void run(true)}
          >
            {syncing ? (
              <Loader2 className="animate-spin" aria-hidden />
            ) : (
              <CloudUpload aria-hidden />
            )}
            Commit and push
          </Button>
        </div>

        {/* Why the push button is off, written out. A disabled control whose
            reason lives in a tooltip is a dead end on a touch screen. */}
        {blocked && <p className="mt-2 text-[12px] leading-snug text-muted-foreground break-anywhere">{blocked}</p>}
        {!blocked && clean && (
          <p className="mt-2 text-[12px] text-muted-foreground">
            Nothing to commit — the working tree is clean and the branch is level
            with its upstream.
          </p>
        )}
      </div>

      {syncError && <Refusal failure={syncError} />}
      {lastSync?.result && <RunOutput run={lastSync.result} />}
    </div>
  )
}

function StateStrip({
  state,
  loading,
  onRefresh,
}: {
  state: GitState | null
  loading: boolean
  onRefresh: () => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 px-3 py-1">
      {loading && !state ? (
        <Skeleton className="h-6 w-48" />
      ) : state ? (
        <>
          <Badge variant="outline" className="font-normal">
            <GitBranch aria-hidden />
            {state.branch ?? 'detached'}
          </Badge>
          {state.dirty.length > 0 ? (
            <Badge variant="warning">{state.dirty.length} changed</Badge>
          ) : (
            <Badge variant="secondary" className="font-normal">
              clean
            </Badge>
          )}
          {state.ahead > 0 && <Badge variant="secondary">{state.ahead} to push</Badge>}
          {state.behind > 0 && <Badge variant="error">{state.behind} behind</Badge>}
        </>
      ) : (
        <span className="text-[12px] text-muted-foreground">No git state.</span>
      )}
      <Button
        variant="ghost"
        size="icon"
        className="ml-auto"
        aria-label="Re-read the git state"
        onClick={onRefresh}
      >
        {loading ? <Loader2 className="animate-spin" aria-hidden /> : <RotateCw aria-hidden />}
      </Button>
    </div>
  )
}

/** What the engine printed. Verbatim, and horizontally scrollable inside its
 *  own box so a long line can never drag the page sideways. */
function RunOutput({ run }: { run: Run }) {
  const text = [run.stdout, run.stderr].filter(Boolean).join('\n').trimEnd()
  if (!text) return null
  return (
    <div className="mx-3 mt-3">
      <p className="pb-1 text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
        {run.command ?? 'report-maker sync'}
      </p>
      <pre
        className={cn(
          'scroll-x max-h-56 overflow-y-auto rounded-md border bg-muted p-3 font-mono text-[11px] whitespace-pre',
          run.code !== 0 && 'border-destructive/40'
        )}
      >
        {text}
      </pre>
    </div>
  )
}

function Refusal({ failure, onRetry }: { failure: Failure; onRetry?: () => void }) {
  return (
    <div className="mx-3 my-2 rounded-lg border border-destructive/40 p-3">
      <p className="text-sm break-anywhere">{failure.message}</p>
      {failure.detail && (
        // The engine's refusals name the command that fixes them. Paraphrasing
        // one throws away the only useful half.
        <pre className="scroll-x mt-2 max-h-56 overflow-y-auto rounded-md border bg-muted p-2 font-mono text-[11px] whitespace-pre">
          {failure.detail}
        </pre>
      )}
      {onRetry && (
        <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  )
}
