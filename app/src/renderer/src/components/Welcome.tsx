import { FolderOpen, FolderPlus, FolderX, Pin, PinOff, Sparkles, X } from 'lucide-react'
import type { VaultEntry } from '../../../shared/types'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

type Props = {
  onOpen: () => void
  onCreate: () => void
  /** The folders opened before, pinned first then most recent — `vaults.ts`
   *  decides that order, and this screen shows it rather than re-sorting. */
  entries?: VaultEntry[]
  onSelect?: (path: string) => void
  /** Drop one from the list. Nothing on disk is touched. */
  onForget?: (path: string) => void
  onPin?: (path: string, pinned: boolean) => void
  /** Absolute path of the demo vault shipped with the engine, when one was found
   *  next to it. Absent on a machine that has the CLI but not the repo. */
  demo?: string | null
  onDemo?: () => void
  /** Where `engine.locate()` found the CLI. */
  engine: string
  /** What that CLI calls itself, when it can say. Null while asking, and on an
   *  engine with no `--version` — printed as such rather than guessed. */
  version?: string | null
  error?: string
}

/**
 * What a fresh install shows — and, after the first day, the way back in.
 *
 * The app ships with no vault, the way an editor ships with no document: a vault
 * is a folder somewhere on the disk — in Documents, in a git repo, in a synced
 * drive — and opening one is the first thing anybody does here. Once some have
 * been opened, this screen is a list of them, because the second thing anybody
 * does is come back to the one they were in.
 *
 * A vault whose folder has gone is **kept, greyed and removable** rather than
 * hidden. An unmounted drive is not a decision to forget a vault, and a list
 * that quietly shrinks makes the app look like it lost your work — so the entry
 * stays, says what is wrong, and offers the only honest action.
 *
 * It also prints which engine answered. That is the one question a first run can
 * fail on — the app is a front end for a CLI that may not be installed — and
 * naming the path is the difference between "nothing works" and "it is looking
 * in the wrong place".
 */
export function Welcome({
  onOpen,
  onCreate,
  entries = [],
  onSelect,
  onForget,
  onPin,
  demo,
  onDemo,
  engine,
  version,
  error
}: Props) {
  const recent = onSelect ? entries : []

  return (
    <div className="flex h-full flex-col items-center justify-center gap-6 px-8">
      <div className="text-center">
        <h1 className="text-xl font-medium">report-maker</h1>
        <p className="mt-1.5 max-w-md text-xs text-muted-foreground">
          A vault is a folder holding <span className="font-mono">report-maker.toml</span>, with your
          reports, designs and brand packs inside it. Open one, or make a new one anywhere on your
          disk.
        </p>
      </div>

      <div className="flex gap-2">
        <Button size="sm" className="gap-1.5" onClick={onOpen}>
          <FolderOpen className="size-3.5" />
          Open a vault…
        </Button>
        <Button size="sm" variant="secondary" className="gap-1.5" onClick={onCreate}>
          <FolderPlus className="size-3.5" />
          Create a vault…
        </Button>
      </div>

      {recent.length > 0 && (
        <section className="w-full max-w-lg">
          <h2 className="px-1 pb-1 text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
            Recent
          </h2>
          <ScrollArea className="max-h-56 rounded-md border border-border">
            <ul className="p-1">
              {recent.map((entry) => (
                <RecentVault
                  key={entry.path}
                  entry={entry}
                  onSelect={() => onSelect?.(entry.path)}
                  onPin={onPin ? () => onPin(entry.path, !entry.pinned) : undefined}
                  onForget={onForget ? () => onForget(entry.path) : undefined}
                />
              ))}
            </ul>
          </ScrollArea>
        </section>
      )}

      {/* Offered only when it is actually there: the demo vault lives in the
          engine's own checkout, which a packaged install may not have. */}
      {demo && onDemo && (
        <div className="flex flex-col items-center gap-1">
          <Button size="sm" variant="ghost" className="gap-1.5" onClick={onDemo}>
            <Sparkles className="size-3.5" />
            Open the demo vault
          </Button>
          <span className="max-w-lg truncate font-mono text-[10px] text-muted-foreground">
            {demo}
          </span>
        </div>
      )}

      {error && (
        <p className="max-w-lg text-center font-mono text-[11px] whitespace-pre-wrap text-destructive">
          {error}
        </p>
      )}

      <p className="text-center text-[11px] text-muted-foreground">
        engine: <span className="font-mono">{engine}</span>
        <br />
        <span className="font-mono text-[10px]">{version ?? 'version unavailable'}</span>
      </p>
    </div>
  )
}

/**
 * One remembered folder.
 *
 * The row is a button and the two controls are buttons beside it rather than
 * inside it — a button inside a button is invalid HTML and, more to the point,
 * the click that removes a vault must not also be a click that opens it.
 */
function RecentVault({
  entry,
  onSelect,
  onPin,
  onForget
}: {
  entry: VaultEntry
  onSelect: () => void
  onPin?: () => void
  onForget?: () => void
}) {
  return (
    <li className="group flex items-center gap-1 rounded-sm pr-1 hover:bg-accent/60">
      <button
        type="button"
        // Disabled rather than hidden: the entry is still a true statement about
        // what was opened, and the row explains itself instead of vanishing.
        disabled={entry.missing}
        onClick={onSelect}
        title={entry.missing ? `${entry.path} — no report-maker.toml there any more` : entry.path}
        className={cn(
          'flex min-w-0 flex-1 items-center gap-2 rounded-sm px-2 py-1.5 text-left',
          entry.missing && 'opacity-45'
        )}
      >
        {entry.missing ? (
          <FolderX className="size-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <FolderOpen className="size-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="min-w-0">
          <span className="flex items-center gap-1.5">
            <span className="truncate text-xs font-medium">{entry.name}</span>
            {entry.pinned && <Pin className="size-2.5 shrink-0 text-muted-foreground" />}
          </span>
          <span className="block truncate font-mono text-[10px] text-muted-foreground">
            {entry.missing ? 'not found — the folder moved, or the drive is not mounted' : entry.path}
          </span>
        </span>
      </button>

      {onPin && !entry.missing && (
        <button
          type="button"
          onClick={onPin}
          aria-pressed={entry.pinned}
          title={entry.pinned ? 'Unpin' : 'Keep this one at the top'}
          className="rounded-sm p-1 text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-foreground focus-visible:opacity-100"
        >
          {entry.pinned ? <PinOff className="size-3" /> : <Pin className="size-3" />}
        </button>
      )}
      {onForget && (
        <button
          type="button"
          onClick={onForget}
          title="Forget this vault. Nothing on disk is touched."
          className={cn(
            'rounded-sm p-1 text-muted-foreground hover:text-destructive focus-visible:opacity-100',
            // A missing vault's only useful action is this one, so it is not
            // hidden behind a hover the way it is on a working row.
            entry.missing ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
          )}
        >
          <X className="size-3" />
        </button>
      )}
    </li>
  )
}
