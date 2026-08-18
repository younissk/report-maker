import { useCallback, useEffect, useMemo, useState } from 'react'
import { CornerDownLeft, FileText } from 'lucide-react'
import type { Node } from '../../../shared/types'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut
} from '@/components/ui/command'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { GROUPS, shortcutLabel, type Command as Verb } from '@/lib/commands'
import { cn } from '@/lib/utils'

type Props = {
  /** The vault as the tree already has it — the palette never walks the disk itself. */
  nodes: Node[]
  /** `buildCommands(ctx)`, resolved against the app as it stands. */
  commands: Verb[]
  onOpen: (node: Node) => void
  /** Controlled open state. Omit both and the palette owns its own. */
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

type Mode = 'all' | 'commands'

/** What the editor can hold. The same list FileTree greys out, for the same
 *  reason: a row you cannot open should not be offered. */
const OPENABLE = /\.(typ|yml|yaml|json|toml|mmd|md|txt|csv)$/i

/** cmdk keeps every rendered row in the DOM, and a vault may hold thousands of
 *  files, so the list is cut here rather than left to the browser to survive. */
const LIMIT = 200

const RECENT_MAX = 20
const RECENT_SHOWN = 8

type FileEntry = { node: Node; rel: string; name: string; dir: string }

/** One line of the palette. The key doubles as cmdk's row value, so it has to be
 *  unique across both kinds — hence the prefix. */
type Row =
  | { kind: 'verb'; key: string; fit: number; verb: Verb }
  | { kind: 'file'; key: string; fit: number; file: FileEntry }

// ── Recents ──────────────────────────────────────────────────────────────────
//
// Module state on purpose. It has to outlive the component, which unmounts every
// time the shell falls back to the welcome screen, and it must not outlive the
// process — a palette that remembers last week is a palette that is wrong about
// what you are doing now.

const recents: string[] = []

function remember(key: string): void {
  const at = recents.indexOf(key)
  if (at !== -1) recents.splice(at, 1)
  recents.unshift(key)
  recents.length = Math.min(recents.length, RECENT_MAX)
}

/** A bonus that decays with age, so recency tilts the ranking instead of
 *  overriding it: an exact title match still beats something you opened an hour
 *  ago and have not thought about since. */
function recentRank(key: string): number {
  const at = recents.indexOf(key)
  return at === -1 ? 0 : (RECENT_MAX - at) * 4
}

// ── Matching ─────────────────────────────────────────────────────────────────

const BOUNDARY = /[^a-z0-9]/

/**
 * How well one word of the query fits, or -1 when it does not fit at all.
 *
 * A substring hit wins, a hit at the start of a word wins harder, and only when
 * there is no substring at all does an in-order subsequence count — which is
 * what lets `rmt` find `report-maker.toml` without letting it outrank a file
 * actually called that. `loose` is off for text nobody types at directly: a
 * subsequence match over a sentence matches almost anything, and a hint that
 * matches everything is a hint that ranks nothing.
 */
function scoreTerm(haystack: string, term: string, loose: boolean): number {
  const at = haystack.indexOf(term)
  if (at !== -1) {
    const boundary = at === 0 || BOUNDARY.test(haystack[at - 1])
    return (boundary ? 100 : 60) - Math.min(at, 40)
  }
  if (!loose) return -1

  let from = 0
  let last = -1
  let gaps = 0
  for (const character of term) {
    const found = haystack.indexOf(character, from)
    if (found === -1) return -1
    if (last !== -1 && found > last + 1) gaps += 1
    last = found
    from = found + 1
  }
  return Math.max(1, 30 - gaps * 3)
}

/** Every word of the query has to fit somewhere, so "acme audit" finds the file
 *  whichever order the two words sit in the path. */
function score(haystack: string, query: string, loose = true): number {
  const text = haystack.toLowerCase()
  let total = 0
  for (const term of query.toLowerCase().split(/\s+/)) {
    if (!term) continue
    const fit = scoreTerm(text, term, loose)
    if (fit < 0) return -1
    total += fit
  }
  return total
}

/** The name carries more signal than the folders above it. */
function scoreFile(file: FileEntry, query: string): number {
  const byName = score(file.name, query)
  const byPath = score(file.rel, query)
  return byName >= 0 ? Math.max(byName + 25, byPath) : byPath
}

/** A command matches on its title first; its group, hint and keywords are there
 *  for people who call the thing something other than this table does. */
function scoreVerb(verb: Verb, query: string): number {
  const byTitle = score(verb.title, query)
  if (byTitle >= 0) return byTitle + 30
  return score(`${verb.group} ${verb.hint ?? ''} ${(verb.keywords ?? []).join(' ')}`, query, false)
}

function flatten(nodes: Node[]): FileEntry[] {
  const found: FileEntry[] = []
  const walk = (list: Node[]): void => {
    for (const node of list) {
      if (node.kind === 'dir') walk(node.children ?? [])
      else if (OPENABLE.test(node.name)) {
        const cut = node.rel.lastIndexOf('/')
        found.push({
          node,
          rel: node.rel,
          name: node.name,
          dir: cut === -1 ? '' : node.rel.slice(0, cut)
        })
      }
    }
  }
  walk(nodes)
  return found
}

/**
 * ⌘K for everything, ⌘⇧P for commands alone.
 *
 * Files come from the tree the shell already loaded and commands from
 * `lib/commands.ts`, so this component ranks rows and draws them and decides
 * nothing else. cmdk's own filter is off: the ranking here folds in what you
 * opened recently and cuts the list at {@link LIMIT}, and two filters
 * disagreeing about order is worse than one filter doing both jobs.
 */
export function Palette({ nodes, commands, onOpen, open, onOpenChange }: Props) {
  const [selfOpen, setSelfOpen] = useState(false)
  const [mode, setMode] = useState<Mode>('all')
  const [query, setQuery] = useState('')
  // Bumped on every open. Recents live outside React, so this is what tells the
  // ranking below to run again when the query has not changed but the order has.
  const [session, setSession] = useState(0)

  const isOpen = open ?? selfOpen
  const platform = window.api.platform

  const setOpen = useCallback(
    (next: boolean) => {
      if (open === undefined) setSelfOpen(next)
      onOpenChange?.(next)
    },
    [open, onOpenChange]
  )

  const show = useCallback(
    (next: Mode) => {
      setMode(next)
      setQuery('')
      setSession((n) => n + 1)
      setOpen(true)
    },
    [setOpen]
  )

  // Window-level, like the shell's own shortcuts: the palette has to be reachable
  // whatever holds focus, including CodeMirror.
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (!(event.metaKey || event.ctrlKey)) return
      const key = event.key.toLowerCase()
      if (key === 'k' && !event.shiftKey) {
        event.preventDefault()
        show('all')
      } else if (key === 'p' && event.shiftKey) {
        event.preventDefault()
        show('commands')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [show])

  const files = useMemo(() => flatten(nodes), [nodes])

  const results = useMemo(() => {
    const q = query.trim()

    const verbs: Row[] = []
    for (const verb of commands) {
      const key = `cmd:${verb.id}`
      const fit = q ? scoreVerb(verb, q) : 0
      if (fit < 0) continue
      verbs.push({ kind: 'verb', key, fit: fit + recentRank(key), verb })
    }
    // Array.sort is stable, so equal scores keep the table's own order, which is
    // editorial rather than alphabetical.
    verbs.sort((a, b) => b.fit - a.fit)

    // With no query there is nothing to rank files by, and listing a whole vault
    // is the lag the cap exists to avoid. Recents cover the real case — jumping
    // back to the file you just left.
    const matches: Row[] = []
    if (mode !== 'commands') {
      for (const file of files) {
        const key = `file:${file.rel}`
        if (!q && !recents.includes(key)) continue
        const fit = q ? scoreFile(file, q) : 0
        if (fit < 0) continue
        matches.push({ kind: 'file', key, fit: fit + recentRank(key), file })
      }
      matches.sort((a, b) => b.fit - a.fit)
    }

    // Recent rows are their own group at the top and drop out of the groups
    // below: cmdk keys selection on the row value, so no row may appear twice.
    const found = new Map<string, Row>()
    for (const row of verbs) found.set(row.key, row)
    for (const row of matches) found.set(row.key, row)

    const recent: Row[] = q
      ? []
      : recents
          .map((key) => found.get(key))
          .filter((row): row is Row => row !== undefined)
          .slice(0, RECENT_SHOWN)

    const shown = new Set(recent.map((row) => row.key))
    const rest = matches.filter((row) => !shown.has(row.key))
    const restVerbs = verbs.filter((row) => !shown.has(row.key))

    return {
      recent,
      verbs: restVerbs,
      files: rest.slice(0, LIMIT),
      hidden: Math.max(0, rest.length - LIMIT)
    }
    // `session` is a dependency because `recents` is mutable module state the
    // compiler cannot see change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, mode, commands, files, session])

  const choose = (row: Row): void => {
    remember(row.key)
    // Closed first, so a command that opens a dialog of its own is not competing
    // with this one for the focus it is about to take.
    setOpen(false)
    if (row.kind === 'verb') void row.verb.run()
    else onOpen(row.file.node)
  }

  const render = (row: Row): React.ReactElement =>
    row.kind === 'verb' ? (
      <VerbRow key={row.key} verb={row.verb} platform={platform} onSelect={() => choose(row)} />
    ) : (
      <FileRow key={row.key} file={row.file} onSelect={() => choose(row)} />
    )

  const fileSection = (
    <div key="files">
      {results.files.length > 0 && (
        <CommandGroup heading="Files">{results.files.map(render)}</CommandGroup>
      )}
      {results.hidden > 0 && (
        <div className="px-3 pb-2 text-[11px] text-muted-foreground">
          …and {results.hidden} more — keep typing to narrow it down.
        </div>
      )}
    </div>
  )

  /**
   * Every section, ordered by the score of its own best row.
   *
   * This is not cosmetic, and it is the fix for a bug that shipped. Ranking is
   * global but drawing is grouped, and cmdk selects the first row it finds in
   * the DOM — so a fixed group order means Enter runs whatever the topmost
   * *group* happened to contain, not the best match. Typing "designs" scored
   * View ▸ Designs above Build ▸ Stage the designs and then ran
   * `report-maker stage`, because Build is printed first: the status bar read
   * `staged · → .build/design/brief/report.typ` while the highlighted row said
   * Designs. A palette that runs a different command from the one it highlights
   * is worse than no palette.
   *
   * Files are one more section here rather than a special case, which is what
   * the old `filesFirst` flag was: the same rule, applied to one of the sections
   * instead of all of them.
   */
  const sections = [
    ...GROUPS.flatMap((group) => {
      const rows = results.verbs.filter((row) => row.kind === 'verb' && row.verb.group === group)
      if (rows.length === 0) return []
      // `results.verbs` is already sorted descending, and filtering preserves
      // order, so the first row of a slice is that group's best.
      return [
        {
          best: rows[0].fit,
          node: (
            <CommandGroup key={group} heading={group}>
              {rows.map(render)}
            </CommandGroup>
          )
        }
      ]
    }),
    { best: results.files[0]?.fit ?? -1, node: fileSection }
  ]
    // Stable, so equal scores — which is every row when nothing has been typed —
    // keep the editorial order GROUPS declares.
    .sort((a, b) => b.best - a.best)
    .map((section) => section.node)

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(next) => {
        if (!next) setQuery('')
        setOpen(next)
      }}
    >
      <DialogContent showCloseButton={false} className="overflow-hidden p-0 sm:max-w-2xl">
        {/* The palette reads as a search box, not a form: Radix wants a title and
            a description, a screen reader wants them, and neither belongs on
            screen. */}
        <DialogHeader className="sr-only">
          <DialogTitle>Command palette</DialogTitle>
          <DialogDescription>Search the vault and run any command</DialogDescription>
        </DialogHeader>

        <Command shouldFilter={false} loop>
          <CommandInput
            value={query}
            onValueChange={setQuery}
            placeholder={mode === 'commands' ? 'Run a command…' : 'Search files and commands…'}
          />
          <CommandList className="max-h-[420px]">
            <CommandEmpty>
              {query.trim() ? `Nothing matches “${query.trim()}”.` : 'Nothing to show.'}
            </CommandEmpty>

            {results.recent.length > 0 && (
              <CommandGroup heading="Recent">{results.recent.map(render)}</CommandGroup>
            )}

            {sections}
          </CommandList>

          <footer className="flex items-center gap-3 border-t border-border px-3 py-1.5 text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <CornerDownLeft className="size-3" /> open
            </span>
            <span>↑↓ move</span>
            <span>⎋ close</span>
            <span className="ml-auto font-mono">
              {mode === 'commands'
                ? `${shortcutLabel('Mod+K', platform)} searches files too`
                : `${shortcutLabel('Mod+Shift+P', platform)} for commands only`}
            </span>
          </footer>
        </Command>
      </DialogContent>
    </Dialog>
  )
}

// ── Rows ─────────────────────────────────────────────────────────────────────

function FileRow({ file, onSelect }: { file: FileEntry; onSelect: () => void }) {
  return (
    <CommandItem
      value={`file:${file.rel}`}
      onSelect={onSelect}
      className="gap-2 overflow-hidden"
      title={file.rel}
    >
      <FileText className="size-3.5 shrink-0" />
      <span className="max-w-[55%] shrink-0 truncate">{file.name}</span>
      <span className="min-w-0 truncate text-[11px] text-muted-foreground">{file.dir}</span>
    </CommandItem>
  )
}

function VerbRow({
  verb,
  platform,
  onSelect
}: {
  verb: Verb
  platform: string
  onSelect: () => void
}) {
  const Icon = verb.icon
  return (
    <CommandItem
      value={`cmd:${verb.id}`}
      disabled={verb.disabled}
      onSelect={onSelect}
      className={cn('gap-2 overflow-hidden', verb.disabled && 'opacity-50')}
    >
      {Icon ? <Icon className="size-3.5 shrink-0" /> : <span className="size-3.5 shrink-0" />}
      <span className="max-w-[55%] shrink-0 truncate">{verb.title}</span>
      {verb.hint && (
        <span className="min-w-0 truncate text-[11px] text-muted-foreground">{verb.hint}</span>
      )}
      <span className="ml-auto flex shrink-0 items-center gap-2 pl-2">
        {verb.reason && <span className="text-[10px] text-muted-foreground">{verb.reason}</span>}
        {verb.shortcut && (
          <CommandShortcut className="ml-0 font-mono">
            {shortcutLabel(verb.shortcut, platform)}
          </CommandShortcut>
        )}
      </span>
    </CommandItem>
  )
}
