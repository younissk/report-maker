/**
 * The pad — `todos.md` and `notes.md`, over `/api/todos` and `/api/notes/:id`.
 *
 * Two files that live beside a report and are neither compiled into the PDF nor
 * subject to the citation rule. That exemption is the entire reason they exist:
 * a half-formed thought that had to be cited before it could be written down
 * would not get written down. Every screen this module feeds says so out loud,
 * because "what is this for" and "what is this exempt from" are the same
 * sentence here.
 *
 * Nothing in this file decides what a task is. Which line of which file is a
 * checkbox, which `// TODO:` in `main.typ` counts, where a new task lands, what
 * a `#tag` or an `@2026-09-01` is — all of that is `engine/notes.py`'s answer,
 * arriving through the API and rendered as given. The only two computations
 * here are arithmetic over that answer (is a due date in the past, is a task
 * still open) and a presentational strip of the exact tokens the engine already
 * reported separately, so the row does not say everything twice.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorText, isAbort, ApiError, type Todo, type TodoReport } from './api'
import { guard } from './session'

/** A refusal, with the engine's own words kept whole. */
export type Failure = { message: string; detail: string | null }

function describe(error: unknown): Failure {
  if (error instanceof ApiError) return { message: error.message, detail: error.detail }
  return { message: errorText(error), detail: null }
}

// ── Which tasks can be ticked ────────────────────────────────────────────────

/**
 * A checkbox this UI is allowed to flip.
 *
 * Two separate reasons a task is read-only here, and both matter:
 *
 *   `main.typ` — the engine refuses outright. A `// TODO:` in a Typst comment is
 *   prose, not state, and `engine/notes.py:toggle` says so with an error. A box
 *   that looked tickable and was not would be worse than no box at all.
 *
 *   `notes.md` — the engine *would* flip it, but `POST /api/todos/:id` carries
 *   `{line, done}` and no file name, so the write lands in `todos.md`. The same
 *   line number exists in both files, which means a tick on a notes.md task
 *   would silently rewrite an unrelated line of the checklist. Until the route
 *   carries `source`, these render with their origin named and no checkbox.
 */
export function isTickable(todo: Todo): boolean {
  return todo.source === 'todos.md'
}

/** Why a task has no checkbox, in the words the reader needs. Null when it has one. */
export function readOnlyReason(todo: Todo): string | null {
  if (todo.source === 'main.typ') {
    return 'Written in the report itself. A // TODO: in a Typst comment is prose, not state — edit the line, or move the task to todos.md.'
  }
  if (todo.source === 'notes.md') {
    return 'Written in notes.md. Tick it there, or move it to todos.md.'
  }
  return null
}

/** The file a task lives in, vault-relative, for a jump-to-line. */
export function pathOf(reportId: string, todo: Todo): string {
  return `reports/${reportId}/${todo.source}`
}

// ── Dates ────────────────────────────────────────────────────────────────────

/**
 * Today, local. `Date#toISOString` is UTC and would call a task overdue an
 * evening early for anybody west of Greenwich.
 */
export function localToday(): string {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

export type DueState = 'overdue' | 'today' | null

/**
 * How a due date reads today. A done task has no due state — telling somebody
 * they are late for something they have already finished is the fastest way to
 * get the dates ignored. ISO dates compare correctly as strings, which is why
 * the engine emits them in that form.
 */
export function dueState(todo: Todo, today: string = localToday()): DueState {
  if (todo.done || !todo.due) return null
  if (todo.due < today) return 'overdue'
  if (todo.due === today) return 'today'
  return null
}

/** `2026-08-18 09:12:33` → `09:12` when that was today, else `2026-08-18`. */
export function touched(modified: string | null | undefined): string {
  if (!modified) return ''
  const [day, time = ''] = modified.split(' ')
  return day === localToday() ? time.slice(0, 5) : day
}

// ── Text ─────────────────────────────────────────────────────────────────────

/**
 * The task without the tokens that are about to be drawn as chips beside it.
 *
 * Only the exact `#tag`s and the one `@date` the engine itself reported are
 * removed, so this is a presentation of the engine's answer and not a second
 * parse of the line: a `#` the engine did not call a tag stays where the writer
 * put it. Stripping by a rule of our own would eventually strip something the
 * engine kept, and the row would disagree with the file.
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
  return todos.reduce((n, todo) => n + (todo.done ? 0 : 1), 0)
}

export function overdueCount(todos: readonly Todo[], today: string = localToday()): number {
  return todos.reduce((n, todo) => n + (dueState(todo, today) === 'overdue' ? 1 : 0), 0)
}

// ── The hook ─────────────────────────────────────────────────────────────────

export type UsePad = {
  reportId: string | null
  todos: Todo[]
  /** The engine's counts, not ours. */
  open: number
  done: number
  notes: string
  hasNotes: boolean
  modified: string | null
  loading: boolean
  /** A write is in flight. Every checkbox goes quiet while one is. */
  busy: boolean
  error: Failure | null
  reload: () => void
  /** True when the engine took the task; false when it refused and `error` says why. */
  add: (text: string) => Promise<boolean>
  toggle: (todo: Todo, done: boolean) => Promise<boolean>
  saveNotes: (text: string) => Promise<void>
}

const EMPTY: Todo[] = []

/**
 * One report's pad.
 *
 * `revision` is the caller's "something wrote to the vault" counter — pass
 * `useApp().revision` and the pad reloads when anything else touches the report.
 */
export function usePad(reportId: string | null, revision = 0): UsePad {
  const [row, setRow] = useState<TodoReport | null>(null)
  const [notes, setNotes] = useState('')
  const [hasNotes, setHasNotes] = useState(false)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Failure | null>(null)
  const [nonce, setNonce] = useState(0)

  // The report the state on screen belongs to. A response that arrives after
  // the selection moved is discarded rather than painted onto another report's
  // pad — which is the bug where a tick lands on the wrong file.
  const showing = useRef<string | null>(null)

  useEffect(() => {
    showing.current = reportId
    if (!reportId) {
      setRow(null)
      setNotes('')
      setHasNotes(false)
      setError(null)
      setLoading(false)
      return
    }

    const controller = new AbortController()
    setLoading(true)
    setError(null)

    void (async () => {
      try {
        const [todos, note] = await Promise.all([
          guard((signal) => api.todos(reportId, undefined, signal), controller.signal),
          guard((signal) => api.notes(reportId, signal), controller.signal),
        ])
        if (controller.signal.aborted || showing.current !== reportId) return
        setRow(todos.reports.find((r) => r.id === reportId) ?? todos.reports[0] ?? null)
        setNotes(note?.text ?? '')
        setHasNotes(Boolean(note))
        setLoading(false)
      } catch (failure) {
        if (isAbort(failure) || controller.signal.aborted) return
        setError(describe(failure))
        setLoading(false)
      }
    })()

    return () => controller.abort()
  }, [reportId, revision, nonce])

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  const add = useCallback(
    async (text: string): Promise<boolean> => {
      const task = text.trim()
      if (!reportId || !task) return false
      setBusy(true)
      setError(null)
      try {
        const updated = await guard((signal) => api.addTodo(reportId, task, signal))
        if (showing.current === reportId) setRow(updated)
        return true
      } catch (failure) {
        if (!isAbort(failure)) setError(describe(failure))
        return false
      } finally {
        setBusy(false)
      }
    },
    [reportId]
  )

  const toggle = useCallback(
    async (todo: Todo, done: boolean): Promise<boolean> => {
      if (!reportId) return false
      if (!isTickable(todo)) {
        // Never send it. The server would write to the wrong file, or refuse —
        // and the refusal would look like a bug in the checkbox rather than a
        // fact about where the task lives.
        setError({ message: readOnlyReason(todo) ?? 'That task has no checkbox.', detail: null })
        return false
      }
      setBusy(true)
      setError(null)
      try {
        const updated = await guard((signal) => api.setTodo(reportId, todo.line, done, signal))
        if (showing.current === reportId) setRow(updated)
        return true
      } catch (failure) {
        if (!isAbort(failure)) setError(describe(failure))
        return false
      } finally {
        setBusy(false)
      }
    },
    [reportId]
  )

  const saveNotes = useCallback(
    async (text: string): Promise<void> => {
      if (!reportId) return
      await guard((signal) => api.writeNotes(reportId, text, signal))
      if (showing.current === reportId) setHasNotes(text.trim().length > 0)
    },
    [reportId]
  )

  return {
    reportId,
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
    add,
    toggle,
    saveNotes,
  }
}
