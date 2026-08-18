/**
 * The pad beside a report: `todos.md` and `notes.md`.
 *
 * Two files, both optional, both plain markdown, neither ever compiled into the
 * PDF — see `engine/notes.py` for why they live beside the report rather than in
 * a separate app. The citation rule does not reach them, which is the point: a
 * half-formed thought that had to be cited before it could be written down would
 * simply not get written down.
 *
 * The split of responsibility here is deliberate and is the only interesting
 * thing in this file.
 *
 * - **The list is the engine's.** `todos --json` decides what a task is, which
 *   `#tag` and `@date` it carries, and which `// TODO:` in `main.typ` counts as a
 *   comment rather than prose (that last one reuses the linter's own scrubber, so
 *   the pad and `check` can never disagree about what a comment is). Nothing here
 *   parses a checklist.
 * - **Ticking a box is the engine's.** `toggle` rewrites one character on one
 *   line and leaves the rest of the file byte for byte, which matters because the
 *   same file may be open in the editor while a checkbox is clicked. A whole-file
 *   write from the app would eat whatever was typed a second earlier.
 * - **Editing the notes is not.** `notes.md` is prose in a text file and the app
 *   already has a guarded reader and writer for those. Routing a textarea through
 *   a subprocess would add a spawn per autosave and answer no question the app
 *   could not answer itself; there is no rule about a vault in reading a file.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { describeError } from '@/lib/sources'

// ── What the engine reports ──────────────────────────────────────────────────
//
// These mirror `engine/notes.py` exactly — `Todo`, and the row `scan()` builds
// per report. Declared here rather than in shared/types.ts only because that file
// has an owner; they belong beside the rest of the engine's shapes once the two
// land together.

/** Which file a task was written in. `main.typ` items are harvested comments. */
export type TodoSource = 'todos.md' | 'notes.md' | 'main.typ'

export type Todo = {
  text: string
  done: boolean
  /** 1-based line in `source`, so a click can jump to it. */
  line: number
  tags: string[]
  /** `YYYY-MM-DD`, or null. The engine drops a date that is not a real one. */
  due: string | null
  source: TodoSource
}

/** One report with something on its pad. Reports with neither file are absent. */
export type TodoReport = {
  id: string
  /** Open and done count everything found, whatever the list happens to show. */
  open: number
  done: number
  todos: Todo[]
  has_notes: boolean
  /** The later of the two files' mtimes, `YYYY-MM-DD HH:MM:SS`, or null. */
  modified: string | null
}

export type TodosResponse = { reports: TodoReport[] }

// ── Where the pad lives ──────────────────────────────────────────────────────
//
// Vault-relative POSIX, the form `Finding.path`, the sources panel and the search
// results all speak, so a jump from this panel takes the same shape as any other.

export function todosPath(reportId: string): string {
  return `reports/${reportId}/todos.md`
}

export function notesPath(reportId: string): string {
  return `reports/${reportId}/notes.md`
}

/** The file a task was written in — `todos.md`, `notes.md` or the report itself. */
export function pathOf(reportId: string, todo: Pick<Todo, 'source'>): string {
  return `reports/${reportId}/${todo.source}`
}

// ── The commands ─────────────────────────────────────────────────────────────
//
// Written down once, here, so the panel never assembles an argv inline and the
// shape the app depends on is readable in one place.

export function listArgs(target?: string | null): string[] {
  return ['todos', ...(target ? [target] : []), '--json']
}

export function addArgs(reportId: string, text: string): string[] {
  return ['todos', reportId, '--add', text]
}

/**
 * Tick or untick one box.
 *
 * `--in` names the file because a checklist item may sit in `notes.md` as easily
 * as in `todos.md`, and the engine needs to know which one it is rewriting. A
 * `main.typ` task never reaches here: `notes.toggle` refuses it, and the panel
 * does not offer a checkbox for one.
 */
export function toggleArgs(
  reportId: string,
  line: number,
  done: boolean,
  source: TodoSource = 'todos.md'
): string[] {
  return ['todos', reportId, done ? '--check' : '--uncheck', String(line), '--in', source]
}

// ── Reading ──────────────────────────────────────────────────────────────────

/**
 * The rows out of `todos --json`.
 *
 * Accepts the wrapped form and a bare list. The wrapper is the CLI's — `scan()`
 * returns the list itself — and a reader that only understood one of them would
 * break on whichever one it was not written against.
 */
export function readRows(response: TodosResponse | TodoReport[] | null): TodoReport[] {
  const rows = Array.isArray(response) ? response : (response?.reports ?? [])
  return Array.isArray(rows) ? rows : []
}

function loadRows(vault: string, target?: string | null): Promise<TodoReport[]> {
  return window.api.engine
    .json<TodosResponse | TodoReport[]>(vault, listArgs(target))
    .then(readRows)
}

/**
 * `notes.md`, as text.
 *
 * An absent file and an empty one read the same in a textarea but not in the
 * panel: one is a scratch pad nobody has started, the other is one somebody
 * emptied. Both come back, so the caller can tell them apart.
 */
async function loadNotes(
  vault: string,
  reportId: string
): Promise<{ text: string; exists: boolean }> {
  const path = `${vault}/${notesPath(reportId)}`
  if (!(await window.api.files.exists(vault, path))) return { text: '', exists: false }
  return { text: await window.api.files.read(vault, path), exists: true }
}

// ── Dates ────────────────────────────────────────────────────────────────────

/** Today, in the local zone. `Date#toISOString` is UTC and would call a task
 *  overdue an evening early for anyone west of Greenwich. */
export function localToday(): string {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

export type DueState = 'overdue' | 'today' | null

/**
 * How a due date reads today — the app's half of `engine/notes.py:_flag`, which
 * says the same thing on the terminal. A done task has no due state: it was
 * finished, and telling somebody they are late for something they have already
 * done is the fastest way to get the dates ignored.
 *
 * ISO dates compare correctly as strings, which is the whole reason the engine
 * emits them in that form.
 */
export function dueState(todo: Todo, today: string = localToday()): DueState {
  if (todo.done || !todo.due) return null
  if (todo.due < today) return 'overdue'
  if (todo.due === today) return 'today'
  return null
}

/** `2026-08-18 09:12:33` → `09:12` when that was today, else `2026-08-18`. The
 *  seconds are never the thing being read. */
export function touched(modified: string | null | undefined): string {
  if (!modified) return ''
  const [day, time = ''] = modified.split(' ')
  return day === localToday() ? time.slice(0, 5) : day
}

// ── Text ─────────────────────────────────────────────────────────────────────

/**
 * The task without the tokens that are about to be drawn as chips.
 *
 * Only the exact `#tag`s and the one `@date` the engine reported are removed, so
 * this is a presentation of the engine's answer rather than a second parse of the
 * line: a `#` the engine did not call a tag stays in the text where the writer
 * put it. Leaving them in would say everything twice; stripping by our own rule
 * would eventually strip something the engine kept.
 */
export function label(todo: Todo): string {
  let text = todo.text
  for (const tag of todo.tags) {
    // Tags are `[A-Za-z][\w-]*` — nothing in one is a regex metacharacter.
    text = text.replace(new RegExp(`(^|\\s)#${tag}(?![\\w-])`), '$1')
  }
  if (todo.due) text = text.replace(`@${todo.due}`, '')
  return text.replace(/\s{2,}/g, ' ').trim()
}

// ── Counting ─────────────────────────────────────────────────────────────────

export function openCount(todos: readonly Todo[]): number {
  return todos.reduce((count, todo) => count + (todo.done ? 0 : 1), 0)
}

export function overdueCount(todos: readonly Todo[], today: string = localToday()): number {
  return todos.reduce((count, todo) => count + (dueState(todo, today) === 'overdue' ? 1 : 0), 0)
}

/** One optimistic tick, counts and all. Matching on line *and* file because the
 *  same line number exists in `todos.md` and in `notes.md`. */
function flip(row: TodoReport | null, line: number, source: TodoSource, done: boolean): TodoReport | null {
  if (!row) return row
  let moved = false
  const todos = row.todos.map((todo) => {
    if (todo.line !== line || todo.source !== source || todo.done === done) return todo
    moved = true
    return { ...todo, done }
  })
  if (!moved) return row
  const step = done ? 1 : -1
  return { ...row, todos, done: row.done + step, open: row.open - step }
}

// ── One report's pad ─────────────────────────────────────────────────────────

export type UseNotes = {
  /** Every task attached to the report, in the engine's order: the checklist,
   *  then anything in the notes, then the report source. */
  todos: Todo[]
  open: number
  done: number
  /** `notes.md` as it is on disk; empty when there is no file. */
  notes: string
  /** Whether the file exists at all, which an empty string cannot say. */
  hasNotes: boolean
  modified: string | null
  loading: boolean
  /** A write is in flight — a tick, or a new task. */
  busy: boolean
  /** Whatever last went wrong, in the engine's own words. */
  error: string | null
  reload: () => void
  toggle: (line: number, done: boolean, source?: TodoSource) => Promise<void>
  /** Resolves true when the task landed. The field keeps what was typed when it
   *  did not — a refusal that costs you the sentence teaches you not to write
   *  the next one down. */
  add: (text: string) => Promise<boolean>
  /** Write `notes.md`. Throws on failure so the editor can say it did not save. */
  saveNotes: (text: string) => Promise<void>
}

const EMPTY: Todo[] = []

/**
 * The pad for one report, kept current.
 *
 * `revision` is the parent's "something happened" counter — a build, a save, a
 * vault switch — exactly as `useSources` uses it. The hook is meant to be owned
 * by the parent rather than by the panel, because the status-bar chip needs the
 * same count and the panel is a sidebar tab that only exists while it is
 * selected: a chip fed by a hook inside a hidden tab would show nothing.
 */
export function useNotes(
  vault: string | null,
  reportId: string | null,
  revision = 0
): UseNotes {
  const [row, setRow] = useState<TodoReport | null>(null)
  const [notes, setNotes] = useState('')
  const [hasNotes, setHasNotes] = useState(false)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  // A promise cannot be cancelled, only disowned: switching report while a slow
  // answer is in flight must not paint the previous report's pad over the new one.
  const generation = useRef(0)

  useEffect(() => {
    const run = ++generation.current

    if (!vault || !reportId) {
      setRow(null)
      setNotes('')
      setHasNotes(false)
      setError(null)
      setLoading(false)
      return
    }

    setLoading(true)
    void (async () => {
      try {
        const [rows, scratch] = await Promise.all([
          loadRows(vault, reportId),
          loadNotes(vault, reportId)
        ])
        if (run !== generation.current) return
        setRow(rows.find((entry) => entry.id === reportId) ?? null)
        setNotes(scratch.text)
        setHasNotes(scratch.exists)
        setError(null)
      } catch (err) {
        if (run !== generation.current) return
        setRow(null)
        setNotes('')
        setHasNotes(false)
        setError(describeError(err))
      } finally {
        if (run === generation.current) setLoading(false)
      }
    })()

    return () => {
      generation.current += 1
    }
  }, [vault, reportId, revision, nonce])

  // `saveNotes` has to stay stable per report so the editor can flush its last
  // keystrokes on the way out through the binding they were typed under; reading
  // existence from a ref keeps it out of the dependency list.
  const exists = useRef(hasNotes)
  exists.current = hasNotes

  /**
   * Tick or untick, painted before the engine is asked.
   *
   * A checkbox that waits for a subprocess feels broken, so the flip happens
   * locally and is undone if the engine refuses — and it does refuse, on a
   * `main.typ` comment, on a line that is not a checklist item, on a file that is
   * not valid UTF-8. The rollback flips back rather than restoring a snapshot,
   * which is the form that survives two clicks landing at once.
   */
  const toggle = useCallback(
    async (line: number, done: boolean, source: TodoSource = 'todos.md') => {
      if (!vault || !reportId) return
      setRow((current) => flip(current, line, source, done))
      setBusy(true)
      const result = await window.api.engine.run(vault, toggleArgs(reportId, line, done, source))
      setBusy(false)
      if (result.code !== 0) {
        setRow((current) => flip(current, line, source, !done))
        setError((result.stderr || result.stdout || `exit ${result.code}`).trimEnd())
        return
      }
      setError(null)
      reload()
    },
    [vault, reportId, reload]
  )

  /**
   * Append a task.
   *
   * Not optimistic, unlike the tick: the engine chooses the line it lands on,
   * creates `todos.md` with its heading when there is none, and strips a bullet
   * off a line pasted out of another markdown file. A row invented here would
   * carry a line number that does not exist yet, and the first click on its
   * checkbox would rewrite the wrong line.
   */
  const add = useCallback(
    async (text: string): Promise<boolean> => {
      const task = text.trim()
      if (!vault || !reportId || !task) return false
      setBusy(true)
      const result = await window.api.engine.run(vault, addArgs(reportId, task))
      setBusy(false)
      if (result.code !== 0) {
        setError((result.stderr || result.stdout || `exit ${result.code}`).trimEnd())
        return false
      }
      setError(null)
      reload()
      return true
    },
    [vault, reportId, reload]
  )

  const saveNotes = useCallback(
    async (text: string) => {
      if (!vault || !reportId) return
      // Focusing an empty textarea is not a reason to create a file. Once the
      // file exists, emptying it is a real edit and is written.
      if (!exists.current && text.trim() === '') return
      try {
        await window.api.files.write(vault, `${vault}/${notesPath(reportId)}`, text)
      } catch (err) {
        setError(describeError(err))
        throw err
      }
      // The hook's own copy moves with the write, so the next reload agrees with
      // what is on screen instead of racing the editor back to an older text.
      setNotes(text)
      setHasNotes(true)
    },
    [vault, reportId]
  )

  return {
    todos: row?.todos ?? EMPTY,
    open: row?.open ?? 0,
    done: row?.done ?? 0,
    notes,
    hasNotes,
    modified: row?.modified ?? null,
    loading,
    busy,
    error,
    reload,
    toggle,
    add,
    saveNotes
  }
}

// ── The whole vault ──────────────────────────────────────────────────────────

export type UseVaultTodos = {
  /** Only reports with something on the pad; the engine leaves the rest out. */
  reports: TodoReport[]
  /** Every task in the vault, flattened — what a chip counts. */
  todos: Todo[]
  /** Report id → its row, for a dashboard card that wants one number. */
  byId: Map<string, TodoReport>
  open: number
  overdue: number
  loading: boolean
  error: string | null
  reload: () => void
}

const NO_ROWS: TodoReport[] = []

/**
 * The pad across the vault, in one subprocess.
 *
 * `todos --json` with no target already walks every report, so the dashboard
 * badges every card from a single call rather than one per card. The reports it
 * omits are the ones with nothing on the pad, and `byId` returning undefined for
 * those is the answer, not a gap.
 */
export function useVaultTodos(vault: string | null, revision = 0): UseVaultTodos {
  const [reports, setReports] = useState<TodoReport[]>(NO_ROWS)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    if (!vault) {
      setReports(NO_ROWS)
      setError(null)
      setLoading(false)
      return
    }
    let stale = false
    setLoading(true)
    loadRows(vault)
      .then((rows) => {
        if (stale) return
        setReports(rows)
        setError(null)
      })
      .catch((err) => {
        if (stale) return
        setReports(NO_ROWS)
        setError(describeError(err))
      })
      .finally(() => {
        if (!stale) setLoading(false)
      })
    return () => {
      stale = true
    }
  }, [vault, revision, nonce])

  const byId = useMemo(
    () => new Map(reports.map((row) => [row.id, row] as const)),
    [reports]
  )
  const todos = useMemo(() => reports.flatMap((row) => row.todos), [reports])
  // Summed from the rows rather than from `todos`, because a row counts every
  // task it found while the list it carries may have been filtered by the engine.
  const open = useMemo(() => reports.reduce((count, row) => count + row.open, 0), [reports])
  const overdue = useMemo(() => overdueCount(todos), [todos])

  return { reports, todos, byId, open, overdue, loading, error, reload }
}
