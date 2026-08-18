import { useCallback, useEffect, useRef, useState } from 'react'
import {
  CalendarClock,
  CornerDownLeft,
  FileCode,
  ListTodo,
  Loader2,
  NotebookPen,
  Plus,
  Square,
  SquareCheck
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import {
  dueState,
  label,
  openCount,
  overdueCount,
  pathOf,
  touched,
  useNotes,
  type Todo,
  type UseNotes
} from '@/lib/notes'
import { cn } from '@/lib/utils'

type Props = {
  vault: string
  /** The report whose pad this is; null when the open file is not in one. */
  reportId: string | null
  /**
   * The hook, when something else on screen needs the same answer — the
   * status-bar chip does. Left out, the panel asks for itself, which is right
   * when it is the only thing looking.
   */
  pad?: UseNotes
  /** The parent's "something happened" counter. Ignored when `pad` is given:
   *  whoever owns the hook owns its reloading. */
  revision?: number
  /** Put the cursor on a line of a vault-relative file, the same call the
   *  sources and problems panels make. */
  onReveal: (path: string, line: number) => void
  className?: string
}

/**
 * The thinking that is not the report.
 *
 * Two sections over the two files `engine/notes.py` describes: a checklist you
 * can tick, and a scratch pad you can type in. Neither is compiled into the PDF
 * and the citation rule does not reach either of them, which is the whole reason
 * this panel exists rather than a comment block at the top of `main.typ` — a
 * half-formed thought that had to be cited before it could be written down would
 * not get written down.
 *
 * The panel holds no rule about a vault. What a task is, which `// TODO:` in the
 * report counts as one, and which line a new task lands on are all the engine's
 * answers; ticking a box is a `todos --check` away rather than a rewrite of the
 * file, so a click cannot eat what the editor has open. The one thing done
 * locally is editing `notes.md`, because prose in a text file is a text file.
 */
export function NotesPanel({ vault, reportId, pad, revision = 0, onReveal, className }: Props) {
  // The hook is always called — conditionally calling one is illegal — and made
  // inert with a null vault when the caller has already run it.
  const own = useNotes(pad ? null : vault, pad ? null : reportId, revision)
  const state = pad ?? own

  const { todos, open, done, notes, hasNotes, modified, loading, busy, error } = state
  const untouched = !loading && !error && todos.length === 0 && !hasNotes

  return (
    <TooltipProvider delayDuration={200}>
      <div className={cn('flex h-full min-h-0 flex-col', className)}>
        <div className="flex h-8 shrink-0 items-center justify-between gap-2 px-3 text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
          <span>Notes</span>
          <span>
            {open > 0 ? `${open} open` : todos.length > 0 ? 'all done' : 'nothing yet'}
            {modified && ` · ${touched(modified)}`}
          </span>
        </div>
        <Separator />

        {!reportId ? (
          <Empty>
            Open a <span className="font-mono">main.typ</span> to see the report's pad.
          </Empty>
        ) : (
          <>
            {/* Todos take the larger share: the list is what the panel is opened
                for, and the scratch pad is where you land after reading it. */}
            <section className="flex min-h-0 flex-[3] flex-col">
              <SectionLabel
                icon={<ListTodo className="size-3" />}
                label="Todos"
                right={done > 0 ? `${done} done` : undefined}
              />
              <ScrollArea className="min-h-0 flex-1">
                <div className="p-1">
                  {untouched && <Blurb />}

                  {loading && todos.length === 0 && !untouched && (
                    <div className="space-y-2 p-2">
                      {[0, 1, 2].map((row) => (
                        <Skeleton key={row} className="h-5 w-full" />
                      ))}
                    </div>
                  )}

                  {error && (
                    <div className="space-y-2 p-2">
                      {/* Verbatim: the engine refuses a tick for reasons worth
                          reading — a comment in main.typ, a line that is not a
                          checklist item, a file that is not valid UTF-8. */}
                      <pre className="max-h-40 overflow-auto rounded-md border border-destructive/50 p-2 font-mono text-[11px] whitespace-pre-wrap">
                        {error}
                      </pre>
                      <Button size="xs" variant="secondary" onClick={state.reload}>
                        Try again
                      </Button>
                    </div>
                  )}

                  {/* Notes started, nothing on the checklist: the blurb has
                      already been read, so this is just the state of the list. */}
                  {!untouched && !loading && !error && todos.length === 0 && (
                    <Empty>Nothing on the checklist.</Empty>
                  )}

                  {todos.map((todo, index) => (
                    <TodoRow
                      // Two `// TODO:` markers can share a line of `main.typ`,
                      // so the position is part of what makes a row unique.
                      key={`${todo.source}:${todo.line}:${index}`}
                      todo={todo}
                      busy={busy}
                      onToggle={(next) => void state.toggle(todo.line, next, todo.source)}
                      onJump={() => onReveal(pathOf(reportId, todo), todo.line)}
                    />
                  ))}
                </div>
              </ScrollArea>
              <Separator />
              <AddTodo onAdd={state.add} />
            </section>

            <Separator />

            {/* Keyed on the report so the draft, the timer and the saved
                indicator belong to one file and can never be carried across to
                another. The unmount that gives is also where the last keystrokes
                get flushed. */}
            <NotesEditor
              key={`${vault}:${reportId}`}
              text={notes}
              onSave={state.saveNotes}
              className="flex min-h-0 flex-[2] flex-col"
            />
          </>
        )}
      </div>
    </TooltipProvider>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="px-3 py-4 text-xs leading-relaxed text-muted-foreground">{children}</p>
}

/** The invitation a report with no pad gets. It says what the two files are for,
 *  because the useful thing to know about them is what they are exempt from. */
function Blurb() {
  return (
    <p className="px-2 py-3 text-xs leading-relaxed text-muted-foreground">
      Todos and notes live beside the report, in{' '}
      <span className="font-mono">todos.md</span> and{' '}
      <span className="font-mono">notes.md</span> — never compiled into the PDF, never
      subject to the citation rule. That is what they are for.
    </p>
  )
}

function SectionLabel({
  icon,
  label: text,
  right
}: {
  icon: React.ReactNode
  label: string
  right?: React.ReactNode
}) {
  return (
    <div className="flex h-6 shrink-0 items-center gap-1.5 px-2 text-[10px] tracking-wide text-muted-foreground uppercase">
      {icon}
      <span>{text}</span>
      <span className="ml-auto normal-case">{right}</span>
    </div>
  )
}

// ── One task ─────────────────────────────────────────────────────────────────

function TodoRow({
  todo,
  busy,
  onToggle,
  onJump
}: {
  todo: Todo
  busy: boolean
  onToggle: (done: boolean) => void
  onJump: () => void
}) {
  const inReport = todo.source === 'main.typ'
  const due = dueState(todo)
  const text = label(todo)

  return (
    <div className="group flex items-start gap-1.5 rounded-sm px-1 py-1 hover:bg-accent/60">
      {inReport ? (
        // No checkbox, because the engine refuses to flip one: a `// TODO:` in a
        // Typst comment is prose, and a box that looked tickable and was not
        // would be worse than no box at all.
        <Tooltip>
          <TooltipTrigger asChild>
            <span tabIndex={0} className="mt-px shrink-0 text-muted-foreground">
              <FileCode className="size-3.5" />
            </span>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-[260px]">
            A <span className="font-mono">// TODO:</span> written in the report itself. It has
            no checkbox to tick — edit the line, or move the task to{' '}
            <span className="font-mono">todos.md</span>.
          </TooltipContent>
        </Tooltip>
      ) : (
        <button
          type="button"
          // Every box goes quiet while one is being written. Two `todos --check`
          // runs against the same file would each rewrite the whole of it, and
          // the second would land on the copy the first had already replaced.
          disabled={busy}
          aria-pressed={todo.done}
          title={todo.done ? 'Mark it open again' : 'Mark it done'}
          onClick={() => onToggle(!todo.done)}
          className={cn(
            'mt-px shrink-0 rounded-sm disabled:opacity-50',
            todo.done ? 'text-muted-foreground' : 'text-foreground/70 hover:text-foreground'
          )}
        >
          {todo.done ? <SquareCheck className="size-3.5" /> : <Square className="size-3.5" />}
        </button>
      )}

      <button
        type="button"
        onClick={onJump}
        title={`${todo.source}:${todo.line}`}
        className="min-w-0 flex-1 rounded-sm text-left"
      >
        <div
          className={cn(
            'text-[11.5px] leading-4 break-words',
            todo.done ? 'text-muted-foreground line-through' : 'text-foreground'
          )}
        >
          {text}
        </div>

        {(inReport || todo.tags.length > 0 || todo.due) && (
          <div className="mt-0.5 flex flex-wrap items-center gap-1">
            {inReport && (
              <Badge
                variant="outline"
                className="h-4 rounded px-1 text-[9.5px] font-normal text-muted-foreground"
              >
                in the report
              </Badge>
            )}
            {todo.tags.map((tag) => (
              <Badge
                key={tag}
                variant="secondary"
                className="h-4 rounded px-1 font-mono text-[9.5px] font-normal"
              >
                #{tag}
              </Badge>
            ))}
            {todo.due && (
              <span
                title={
                  due === 'overdue'
                    ? `due ${todo.due} — overdue`
                    : due === 'today'
                      ? `due today, ${todo.due}`
                      : `due ${todo.due}`
                }
                className={cn(
                  'inline-flex items-center gap-0.5 text-[10px]',
                  due === 'overdue'
                    ? 'text-destructive'
                    : due === 'today'
                      ? 'text-foreground'
                      : 'text-muted-foreground'
                )}
              >
                <CalendarClock className="size-3" />
                {todo.due}
                {due === 'overdue' && ' · overdue'}
              </span>
            )}
          </div>
        )}
      </button>
    </div>
  )
}

// ── Adding one ───────────────────────────────────────────────────────────────

/**
 * One line, committed on Enter.
 *
 * Deliberately not a dialog. The gesture this is for is remembering something
 * mid-sentence, and anything with a Cancel button in it is long enough that the
 * thought is gone before the form is open.
 */
function AddTodo({ onAdd }: { onAdd: (text: string) => Promise<boolean> }) {
  const [text, setText] = useState('')
  // Its own flag rather than the pad's: a checkbox being written elsewhere is no
  // reason for the field to stop taking a sentence somebody is halfway through.
  const [sending, setSending] = useState(false)

  async function commit(): Promise<void> {
    const task = text.trim()
    if (!task || sending) return
    setSending(true)
    const landed = await onAdd(task)
    setSending(false)
    // Cleared only once the engine has taken it. A refusal that also swallowed
    // the sentence would teach you not to write the next one down.
    if (landed) setText('')
  }

  return (
    <div className="relative shrink-0 p-1.5">
      <Plus className="pointer-events-none absolute top-1/2 left-3.5 size-3 -translate-y-1/2 text-muted-foreground" />
      <Input
        value={text}
        spellCheck={false}
        placeholder="Add a todo"
        title="A task is one line. #tag and @2026-09-01 are read by the engine."
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== 'Enter') return
          event.preventDefault()
          void commit()
        }}
        className="h-7 pr-7 pl-7 text-xs"
      />
      <span className="pointer-events-none absolute top-1/2 right-3.5 -translate-y-1/2 text-muted-foreground">
        {sending ? (
          <Loader2 className="size-3 animate-spin" />
        ) : (
          text.trim() && <CornerDownLeft className="size-3" />
        )}
      </span>
    </div>
  )
}

// ── The scratch pad ──────────────────────────────────────────────────────────

/** How long the pad sits on a keystroke before writing. Long enough that a
 *  sentence is one write rather than forty, short enough that closing the window
 *  a moment after typing cannot lose it. */
const SAVE_DEBOUNCE_MS = 800

type SaveState = 'idle' | 'typing' | 'saving' | 'saved' | 'failed'

/**
 * `notes.md`, as a textarea.
 *
 * No toolbar, no preview, no ceremony — it is a scratch pad, and every control
 * added to one is a reason to write somewhere else instead. The file is markdown
 * because markdown is what a text file full of notes already is, not because
 * anything renders it.
 *
 * A plain `textarea` rather than the ui one: that component sizes itself to its
 * content, which is right for a form field and wrong for a pad that has to fill
 * the pane and scroll inside it.
 */
function NotesEditor({
  text,
  onSave,
  className
}: {
  text: string
  onSave: (text: string) => Promise<void>
  className?: string
}) {
  const [draft, setDraft] = useState(text)
  const [state, setState] = useState<SaveState>('idle')
  const [at, setAt] = useState('')

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const dirty = useRef(false)
  const latest = useRef(text)

  // The file moved underneath us — a git pull, a second editor, the same file
  // opened in the centre pane. Adopt it, unless there are keystrokes here that
  // have not been written yet: those are the newer truth.
  useEffect(() => {
    if (dirty.current) return
    latest.current = text
    setDraft(text)
  }, [text])

  const commit = useCallback(
    async (value: string) => {
      timer.current = null
      setState('saving')
      try {
        await onSave(value)
        // Anything typed while the write was in flight is still unsaved; only a
        // write of the newest text clears the flag.
        if (latest.current === value) dirty.current = false
        setState('saved')
        setAt(clock())
      } catch {
        setState('failed')
      }
    },
    [onSave]
  )

  // Switching report unmounts this instance (the parent keys it), and whatever
  // is still sitting on the timer has to be written before `onSave` starts
  // pointing at another report's file. This is the last moment it does not.
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
      if (dirty.current) void onSave(latest.current).catch(() => undefined)
    },
    [onSave]
  )

  return (
    <section className={className}>
      <SectionLabel
        icon={<NotebookPen className="size-3" />}
        label="Notes"
        right={
          <span
            className={cn('text-[10px]', state === 'failed' ? 'text-destructive' : 'text-muted-foreground')}
          >
            {state === 'typing' && 'unsaved'}
            {state === 'saving' && 'saving…'}
            {state === 'saved' && `saved ${at}`}
            {state === 'failed' && 'not saved'}
          </span>
        }
      />
      <textarea
        value={draft}
        spellCheck={false}
        placeholder="Anything. This file is never compiled into the report."
        onChange={(event) => {
          const value = event.target.value
          setDraft(value)
          latest.current = value
          dirty.current = true
          setState('typing')
          if (timer.current) clearTimeout(timer.current)
          timer.current = setTimeout(() => void commit(value), SAVE_DEBOUNCE_MS)
        }}
        onKeyDown={(event) => {
          // ⌘S here means this pad, not the file in the centre pane. Writing it
          // now rather than in 800ms is the whole of what the key is asking for.
          if (!(event.key === 's' && (event.metaKey || event.ctrlKey))) return
          event.preventDefault()
          event.stopPropagation()
          if (timer.current) clearTimeout(timer.current)
          void commit(latest.current)
        }}
        className="min-h-0 w-full flex-1 resize-none bg-transparent px-2.5 py-1.5 font-mono text-[11.5px] leading-[1.75] outline-none placeholder:text-muted-foreground"
      />
    </section>
  )
}

/** `09:12`, local. The pad is saved constantly; the second is never the thing
 *  being read. */
function clock(): string {
  const now = new Date()
  return `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`
}

// ── The status-bar chip ──────────────────────────────────────────────────────

type ChipProps = {
  /** Whatever the caller is counting — one report's pad, or the whole vault's. */
  todos: Todo[]
  /** The notes panel is showing. */
  active: boolean
  onClick: () => void
  className?: string
}

/**
 * `n open`, and the handle for the panel.
 *
 * It counts the tasks it is given rather than asking anything, so the same chip
 * serves a report and a vault. Overdue is the one state worth colouring: a due
 * date nobody surfaces is a comment, and surfacing it is the whole of what the
 * date is for.
 */
export function TodoChip({ todos, active, onClick, className }: ChipProps) {
  const open = openCount(todos)
  const overdue = overdueCount(todos)

  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={
        open === 0
          ? todos.length > 0
            ? 'Everything on the pad is done'
            : 'Nothing on the pad — open the notes panel'
          : `${open} open${overdue > 0 ? `, ${overdue} overdue` : ''} — open the notes panel`
      }
      className={cn(
        'inline-flex h-5 items-center gap-1 rounded-sm px-1.5 text-[11px] hover:bg-accent',
        overdue > 0 ? 'text-destructive' : open > 0 ? 'text-foreground' : 'text-muted-foreground',
        active && 'bg-accent',
        className
      )}
    >
      <ListTodo className="size-3" />
      {open === 0 ? 'no todos' : `${open} open`}
      {overdue > 0 && <span>· {overdue} overdue</span>}
    </button>
  )
}
