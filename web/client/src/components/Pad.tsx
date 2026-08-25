import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import {
  CalendarClock,
  CornerDownLeft,
  FileCode,
  ListTodo,
  Loader2,
  NotebookPen,
  Plus,
  Square,
  SquareCheck,
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
import type { Todo } from '@/lib/api'
import {
  dueState,
  isTickable,
  label,
  overdueCount,
  pathOf,
  readOnlyReason,
  touched,
  usePad,
  type Failure,
  type UsePad,
} from '@/lib/pad'
import { cn, useIsDesktop } from '@/lib/utils'

/**
 * The pad: a checklist and a scratch pad, beside the report and outside the rule.
 *
 * `todos.md` and `notes.md` are the only two files in a report folder that a
 * reader never sees. Neither is compiled into the PDF and the citation rule
 * reaches neither, which is the whole reason they are here rather than as a
 * comment block at the top of `main.typ`: a half-formed thought that had to be
 * cited before it could be written down would not get written down. The empty
 * state says exactly that, because it is the useful thing to know about them.
 *
 * The panel holds no rule about a vault. What a task is, which line it lives on,
 * which `// TODO:` in the report counts as one — all of it arrives from the API
 * and is drawn as given.
 */

// ── The overlay ──────────────────────────────────────────────────────────────

export type PadProps = {
  reportId: string | null
  open?: boolean
  onOpenChange?: (open: boolean) => void
  /** Anything that opens it. Wrapped in a trigger; omit when driving `open`. */
  trigger?: ReactNode
  /** The caller's "something wrote to the vault" counter — `useApp().revision`. */
  revision?: number
  /** Put the cursor on a line of a vault-relative file. Rows are inert without it. */
  onJump?: (path: string, line: number) => void
  /** Share one hook with a count elsewhere on screen. */
  pad?: UsePad
}

/**
 * A sheet from the bottom on a phone, a panel from the right on a desktop.
 *
 * Both are the same component: below 1024px it rises from the edge a thumb can
 * reach, above it slides in beside the report the way the desktop app's panel
 * does. The side is chosen from the same breakpoint everything else uses.
 */
export function Pad({
  reportId,
  open,
  onOpenChange,
  trigger,
  revision = 0,
  onJump,
  pad,
}: PadProps) {
  const desktop = useIsDesktop()

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      {trigger ? <SheetTrigger asChild>{trigger}</SheetTrigger> : null}
      <SheetContent
        side={desktop ? 'right' : 'bottom'}
        className={cn('gap-0', !desktop && 'h-[88dvh] max-h-[88dvh]')}
      >
        <SheetHeader>
          <SheetTitle>The pad</SheetTitle>
          <SheetDescription>
            Never compiled into the PDF, never subject to the citation rule.
          </SheetDescription>
        </SheetHeader>
        <Separator />
        <PadPanel
          reportId={reportId}
          revision={revision}
          onJump={onJump}
          pad={pad}
          className="min-h-0 flex-1"
        />
      </SheetContent>
    </Sheet>
  )
}

// ── The panel ────────────────────────────────────────────────────────────────

export type PadPanelProps = {
  reportId: string | null
  revision?: number
  onJump?: (path: string, line: number) => void
  pad?: UsePad
  className?: string
}

/**
 * The pad itself, filling whatever it is dropped into.
 *
 * One scroll region rather than two. A checklist above a pad above a text field
 * on a 375px screen, each scrolling separately, gives every one of them too
 * little room to be worth having — and with the soft keyboard up, a fixed
 * composer at the bottom of a bottom sheet is precisely the bar the keyboard
 * covers. So the add field sits at the top of the list, in the flow, where the
 * keyboard cannot reach it.
 */
export function PadPanel({
  reportId,
  revision = 0,
  onJump,
  pad,
  className,
}: PadPanelProps) {
  // The hook is always called — calling one conditionally is illegal — and made
  // inert with a null report when the caller has already run it.
  const own = usePad(pad ? null : reportId, revision)
  const state = pad ?? own

  const { todos, open, done, loading, busy, error } = state
  const untouched = !loading && !error && todos.length === 0 && !state.hasNotes
  const overdue = overdueCount(todos)

  if (!reportId) {
    return (
      <div className={cn('pane', className)}>
        <p className="px-4 py-6 text-sm leading-relaxed text-muted-foreground">
          Choose a report to see its pad.
        </p>
      </div>
    )
  }

  return (
    <div className={cn('pane', className)}>
      {/* One line of state, and nothing that needs a hover to be read. */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 pt-3 pb-2 text-[11px] text-muted-foreground">
        <span className="tabular-nums">
          {open > 0 ? `${open} open` : todos.length > 0 ? 'all done' : 'nothing yet'}
        </span>
        {done > 0 && <span className="tabular-nums">{done} done</span>}
        {overdue > 0 && (
          <span className="tabular-nums text-destructive">{overdue} overdue</span>
        )}
        {state.modified && <span>edited {touched(state.modified)}</span>}
      </div>

      <Section icon={<ListTodo className="size-3.5" aria-hidden />} title="Todos">
        {todos.length > 0 ? `${todos.length}` : null}
      </Section>

      <div className="px-3 pb-2">
        <AddTodo onAdd={state.add} />
      </div>

      {error && <Refusal failure={error} onRetry={state.reload} />}

      {loading && todos.length === 0 && !error && (
        <div className="flex flex-col gap-2 px-4 py-2">
          {[0, 1, 2].map((row) => (
            <Skeleton key={row} className="h-9 w-full" />
          ))}
        </div>
      )}

      {untouched && <Blurb />}

      {!untouched && !loading && !error && todos.length === 0 && (
        <p className="px-4 py-3 text-sm text-muted-foreground">Nothing on the checklist.</p>
      )}

      <ul className="flex flex-col px-1">
        {todos.map((todo, index) => (
          <TodoRow
            // Two `// TODO:` markers can share a line of `main.typ`, so the
            // position in the list is part of what makes a row unique.
            key={`${todo.source}:${todo.line}:${index}`}
            todo={todo}
            busy={busy}
            onToggle={(next) => void state.toggle(todo, next)}
            onJump={onJump ? () => onJump(pathOf(reportId, todo), todo.line) : undefined}
          />
        ))}
      </ul>

      <ReadOnlyNote todos={todos} />

      <Separator className="mt-3" />

      {/* Keyed on the report so the draft, the timer and the saved indicator
          belong to one file and can never be carried across to another. The
          unmount that gives is also where the last keystrokes get flushed. */}
      <NotesEditor key={reportId} text={state.notes} onSave={state.saveNotes} />
    </div>
  )
}

/**
 * Why some rows have no checkbox, said once rather than on every row.
 *
 * Two different facts, and each is only printed when the list actually holds a
 * task it applies to — a phone has no hover, so the reason cannot live in a
 * tooltip, and a paragraph repeated under six rows is noise rather than an
 * explanation.
 */
function ReadOnlyNote({ todos }: { todos: Todo[] }) {
  const harvested = todos.some((todo) => todo.source === 'main.typ')
  const elsewhere = todos.some((todo) => todo.source === 'notes.md')
  if (!harvested && !elsewhere) return null

  return (
    <div className="mx-4 mt-2 text-[11px] leading-snug text-muted-foreground">
      {harvested && (
        <p>
          A row marked <span className="text-foreground">in the report</span> is a{' '}
          <span className="font-mono">// TODO:</span> written in{' '}
          <span className="font-mono">main.typ</span>. It has no checkbox to tick —
          edit the line, or move the task to{' '}
          <span className="font-mono">todos.md</span>.
        </p>
      )}
      {elsewhere && (
        <p className={cn(harvested && 'mt-1')}>
          A row marked <span className="text-foreground">in notes.md</span> is a
          checkbox in the scratch pad. Tick it there, or move it to{' '}
          <span className="font-mono">todos.md</span>.
        </p>
      )}
    </div>
  )
}

function Section({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex items-center gap-1.5 px-4 pt-2 pb-1 text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
      {icon}
      <span>{title}</span>
      {children ? <span className="ml-auto tabular-nums normal-case">{children}</span> : null}
    </div>
  )
}

/**
 * The invitation a report with no pad gets.
 *
 * It leads with the exemption rather than with the feature, because the
 * exemption is the point: everywhere else in this product a sentence is either
 * cited or marked as an opinion, and here it is neither.
 */
function Blurb() {
  return (
    <div className="mx-3 my-1 rounded-lg border border-dashed p-3 text-sm leading-relaxed text-muted-foreground">
      <p>
        Todos and notes live beside the report, in{' '}
        <span className="font-mono text-[12px]">todos.md</span> and{' '}
        <span className="font-mono text-[12px]">notes.md</span>.
      </p>
      <p className="mt-2 text-foreground">
        Neither is ever compiled into the PDF, and neither is subject to the
        citation rule.
      </p>
      <p className="mt-2">
        That is what they are for: a half-formed thought you would have to cite
        before you could write it down does not get written down.
      </p>
    </div>
  )
}

/** A refusal, in the engine's own words — they name the command that fixes them. */
function Refusal({ failure, onRetry }: { failure: Failure; onRetry?: () => void }) {
  return (
    <div className="mx-3 my-2 rounded-lg border border-destructive/40 p-3">
      <p className="text-sm break-anywhere">{failure.message}</p>
      {failure.detail && (
        <pre className="scroll-x mt-2 max-h-40 overflow-y-auto rounded-md border bg-muted p-2 font-mono text-[11px] whitespace-pre">
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

// ── One task ─────────────────────────────────────────────────────────────────

function TodoRow({
  todo,
  busy,
  onToggle,
  onJump,
}: {
  todo: Todo
  busy: boolean
  onToggle: (done: boolean) => void
  onJump?: () => void
}) {
  const tickable = isTickable(todo)
  const reason = readOnlyReason(todo)
  const due = dueState(todo)
  const text = label(todo)

  return (
    <li className="flex items-stretch gap-1">
      {tickable ? (
        <button
          type="button"
          // Every box goes quiet while one is being written: two writes against
          // the same file each rewrite the whole of it, and the second would
          // land on the copy the first had already replaced.
          disabled={busy}
          role="checkbox"
          aria-checked={todo.done}
          aria-label={todo.done ? `Mark "${text}" open again` : `Mark "${text}" done`}
          onClick={() => onToggle(!todo.done)}
          className={cn(
            'tap flex shrink-0 items-center justify-center rounded-md outline-none',
            'focus-visible:ring-[3px] focus-visible:ring-ring/50 active:bg-accent disabled:opacity-50',
            todo.done ? 'text-muted-foreground' : 'text-foreground'
          )}
        >
          {todo.done ? (
            <SquareCheck className="size-5" aria-hidden />
          ) : (
            <Square className="size-5" aria-hidden />
          )}
        </button>
      ) : (
        // No checkbox, and the reason is written out rather than hidden in a
        // tooltip — a phone has no hover, and a box that looked tickable and
        // was not would be worse than no box at all.
        <span
          className="tap flex shrink-0 items-center justify-center text-muted-foreground"
          title={reason ?? undefined}
        >
          <FileCode className="size-4" aria-hidden />
        </span>
      )}

      <button
        type="button"
        onClick={onJump}
        disabled={!onJump}
        aria-label={onJump ? `Open ${todo.source} at line ${todo.line}` : undefined}
        className={cn(
          'min-w-0 flex-1 rounded-md py-2 pr-2 text-left outline-none',
          'focus-visible:ring-[3px] focus-visible:ring-ring/50',
          onJump ? 'active:bg-accent' : 'cursor-default'
        )}
      >
        <span
          className={cn(
            'block text-sm leading-snug break-anywhere',
            todo.done ? 'text-muted-foreground line-through' : 'text-foreground'
          )}
        >
          {text}
        </span>

        {(!tickable || todo.tags.length > 0 || todo.due) && (
          <span className="mt-1 flex flex-wrap items-center gap-1">
            {!tickable && (
              <Badge variant="outline" className="font-normal text-muted-foreground">
                {todo.source === 'main.typ' ? 'in the report' : `in ${todo.source}`}
              </Badge>
            )}
            {todo.tags.map((tag) => (
              <Badge key={tag} variant="secondary" className="font-mono font-normal">
                #{tag}
              </Badge>
            ))}
            {todo.due && (
              <span
                className={cn(
                  'inline-flex items-center gap-1 text-[11px]',
                  due === 'overdue'
                    ? 'text-destructive'
                    : due === 'today'
                      ? 'text-foreground'
                      : 'text-muted-foreground'
                )}
              >
                <CalendarClock className="size-3" aria-hidden />
                {todo.due}
                {due === 'overdue' && ' · overdue'}
              </span>
            )}
          </span>
        )}

      </button>
    </li>
  )
}

// ── Adding one ───────────────────────────────────────────────────────────────

/**
 * One line, committed on Enter.
 *
 * A form rather than a bare field, so a soft keyboard offers a Go key and the
 * gesture is one tap rather than a hunt for a button. The button is there too:
 * a keyboard whose return key inserts a newline is common enough that Enter
 * alone would be an affordance some phones do not have.
 */
function AddTodo({ onAdd }: { onAdd: (text: string) => Promise<boolean> }) {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const id = useId()

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
    <form
      onSubmit={(event) => {
        event.preventDefault()
        void commit()
      }}
      className="flex items-center gap-2"
    >
      <label className="sr-only" htmlFor={id}>
        Add a task
      </label>
      <div className="relative min-w-0 flex-1">
        <Plus
          className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <Input
          id={id}
          value={text}
          spellCheck={false}
          enterKeyHint="done"
          placeholder="Add a task"
          onChange={(event) => setText(event.target.value)}
          onFocus={(event) => {
            // A field inside a sheet, with the soft keyboard rising underneath
            // it. Nudging it into the visual viewport is the difference between
            // typing blind and not.
            const target = event.currentTarget
            window.setTimeout(
              () => target.scrollIntoView({ block: 'center', behavior: 'smooth' }),
              250
            )
          }}
          className="pl-8"
        />
      </div>
      <Button
        type="submit"
        size="icon"
        variant="secondary"
        disabled={sending || text.trim().length === 0}
        aria-label="Add this task"
      >
        {sending ? (
          <Loader2 className="animate-spin" aria-hidden />
        ) : (
          <CornerDownLeft aria-hidden />
        )}
      </Button>
    </form>
  )
}

// ── The scratch pad ──────────────────────────────────────────────────────────

/**
 * How long the pad sits on a keystroke before writing. Long enough that a
 * sentence is one write rather than forty, short enough that closing the tab a
 * moment after typing cannot lose it.
 */
const SAVE_DEBOUNCE_MS = 800

type SaveState = 'idle' | 'typing' | 'saving' | 'saved' | 'failed'

/**
 * `notes.md`, as a textarea.
 *
 * No toolbar, no preview, no markdown rendering — it is a scratch pad, and
 * every control added to one is a reason to write somewhere else instead. The
 * file is markdown because a text file full of notes already is markdown, not
 * because anything renders it.
 *
 * A plain `textarea` rather than the ui one: that component sizes itself to its
 * content, which is right for a form field and wrong for a pad. 16px on a
 * phone, because below that iOS zooms the viewport the moment the caret lands.
 */
function NotesEditor({
  text,
  onSave,
}: {
  text: string
  onSave: (text: string) => Promise<void>
}) {
  const [draft, setDraft] = useState(text)
  const [state, setState] = useState<SaveState>('idle')
  const [at, setAt] = useState('')
  const id = useId()

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const dirty = useRef(false)
  const latest = useRef(text)

  // The file moved underneath us — a git pull, the same file open elsewhere.
  // Adopt it, unless there are keystrokes here that have not been written yet:
  // those are the newer truth.
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

  // Switching report unmounts this instance (the panel keys it), and whatever
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
    <section className="px-3 pt-2 pb-4">
      <div className="flex items-center gap-1.5 pb-1 text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
        <NotebookPen className="size-3.5" aria-hidden />
        <label htmlFor={id}>Notes</label>
        <span
          role="status"
          aria-live="polite"
          className={cn(
            'ml-auto text-[10px] normal-case',
            state === 'failed' ? 'text-destructive' : 'text-muted-foreground'
          )}
        >
          {state === 'typing' && 'unsaved'}
          {state === 'saving' && 'saving…'}
          {state === 'saved' && `saved ${at}`}
          {state === 'failed' && 'not saved'}
        </span>
      </div>
      <textarea
        id={id}
        value={draft}
        spellCheck={false}
        placeholder="Anything at all. This file is never compiled into the report."
        rows={8}
        onChange={(event) => {
          const value = event.target.value
          setDraft(value)
          latest.current = value
          dirty.current = true
          setState('typing')
          if (timer.current) clearTimeout(timer.current)
          timer.current = setTimeout(() => void commit(value), SAVE_DEBOUNCE_MS)
        }}
        onFocus={(event) => {
          const target = event.currentTarget
          window.setTimeout(
            () => target.scrollIntoView({ block: 'center', behavior: 'smooth' }),
            250
          )
        }}
        onKeyDown={(event) => {
          // ⌘S here means this pad. Writing it now rather than in 800ms is the
          // whole of what the key is asking for.
          if (!(event.key === 's' && (event.metaKey || event.ctrlKey))) return
          event.preventDefault()
          event.stopPropagation()
          if (timer.current) clearTimeout(timer.current)
          void commit(latest.current)
        }}
        className={cn(
          'min-h-40 w-full resize-y rounded-md border border-input bg-transparent px-3 py-2',
          'font-mono text-base leading-[1.8] outline-none lg:text-[13px]',
          'placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50',
          'dark:bg-input/30'
        )}
      />
      <p className="mt-2 text-[11px] leading-snug text-muted-foreground">
        Committed with the report, never compiled into it, never cited.
      </p>
    </section>
  )
}

/** `09:12`, local. The pad is saved constantly; the second is never the thing
 *  being read. */
function clock(): string {
  const now = new Date()
  return `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`
}
