/**
 * Getting a vault, on the fly.
 *
 * The whole "no account" promise lives in this file. Somebody lands on the page,
 * the browser has no session cookie, so we ask for one — and the server runs
 * `report-maker init` and then `report-maker new` into a fresh temporary vault
 * before it answers. That is two engine subprocesses, which is why this shows a
 * real loading state rather than a spinner nobody believes.
 *
 * It is also the one place that knows what to do about a 401. A session has a
 * 24-hour TTL and a background sweeper, so "expired" is a normal end of life,
 * not an error: `guard()` catches `SessionExpired`, seeds a new session, and
 * retries the call once. Nothing else in the app should ever have to think
 * about it.
 */

import { useCallback, useEffect, useSyncExternalStore } from 'react'
import { api, ApiError, SessionExpired, isAbort, type Session } from './api'

/** What the shell is allowed to render. */
export type SessionState =
  | { status: 'idle' }
  /** Two engine commands are running server-side. `step` says which. */
  | { status: 'seeding'; step: string }
  | { status: 'ready'; session: Session; /** true when this load created it */ fresh: boolean }
  | { status: 'failed'; message: string; detail: string | null }

/**
 * The seeding captions, in the order the server does the work. They are timed
 * rather than streamed because the server answers one request, not a progress
 * feed — but each line is a true statement about what is happening, which is the
 * difference between a loading state and a lie with a spinner on it.
 */
const STEPS: { at: number; text: string }[] = [
  { at: 0, text: 'Setting up a vault for you…' },
  { at: 900, text: 'Scaffolding your first report…' },
  { at: 2600, text: 'Almost there — this only happens once.' },
]

// ── the store ────────────────────────────────────────────────────────────────
//
// Module-level rather than context-level so two components mounting at once
// cannot each POST a session and leave the loser's vault orphaned on disk.

let state: SessionState = { status: 'idle' }
const listeners = new Set<() => void>()
let inflight: Promise<Session> | null = null
let stepTimers: number[] = []

function emit(next: SessionState) {
  state = next
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function snapshot(): SessionState {
  return state
}

function startSteps() {
  clearSteps()
  for (const step of STEPS) {
    stepTimers.push(
      window.setTimeout(() => {
        if (state.status === 'seeding') emit({ status: 'seeding', step: step.text })
      }, step.at)
    )
  }
}

function clearSteps() {
  for (const id of stepTimers) window.clearTimeout(id)
  stepTimers = []
}

function describe(error: unknown): { message: string; detail: string | null } {
  if (error instanceof ApiError) return { message: error.message, detail: error.detail }
  if (error instanceof Error) return { message: error.message, detail: null }
  return { message: String(error), detail: null }
}

// ── bootstrap ────────────────────────────────────────────────────────────────

/**
 * The session for this browser, creating one if there is none.
 *
 * `force` skips the `GET` and mints a new session outright — what `guard()` does
 * after a 401, and what a "start over" button does.
 *
 * Concurrent callers share one flight. This matters: a session that gets created
 * twice leaves a vault directory nobody will ever open again, and the server
 * only sweeps it a day later.
 */
export function ensureSession(force = false): Promise<Session> {
  if (!force && state.status === 'ready') return Promise.resolve(state.session)
  if (inflight) return inflight

  emit({ status: 'seeding', step: STEPS[0].text })
  startSteps()

  const flight = (async () => {
    let existing: Session | null = null
    if (!force) {
      try {
        existing = await api.getSession()
      } catch (error) {
        // 401 and 404 both mean the same thing here: there is no session behind
        // this cookie. Anything else is a server that is actually broken, and
        // creating a session on top of it would only hide the real message.
        if (!(error instanceof ApiError) || (error.status !== 401 && error.status !== 404)) {
          throw error
        }
      }
    }

    const session = existing ?? (await api.createSession())
    emit({ status: 'ready', session, fresh: existing === null })
    return session
  })()

  inflight = flight

  // An observer, not the returned promise: the caller still gets the rejection,
  // and the store still learns about it even when nobody was awaiting.
  void flight
    .catch((error) => {
      if (isAbort(error)) return
      emit({ status: 'failed', ...describe(error) })
    })
    .finally(() => {
      clearSteps()
      inflight = null
    })

  return flight
}

/** Re-read the session — quota usage moves as commands run. */
export async function refreshSession(): Promise<Session | null> {
  try {
    const session = await api.getSession()
    emit({ status: 'ready', session, fresh: false })
    return session
  } catch (error) {
    if (error instanceof SessionExpired) {
      return ensureSession(true)
    }
    return state.status === 'ready' ? state.session : null
  }
}

/** Throw the vault away and start again. The server removes the directory. */
export async function resetSession(): Promise<Session> {
  try {
    await api.destroySession()
  } catch {
    // Destroying a session that is already gone is the outcome we wanted.
  }
  return ensureSession(true)
}

/**
 * Run an API call with the session repaired underneath it.
 *
 * On `SessionExpired` this mints a new session and retries **once**. The retry
 * is deliberately not general: a second 401 means something is wrong that a
 * third request will not fix, and a loop of quiet retries is how a broken auth
 * path turns into a hang.
 *
 * Note what this cannot do — the new session is a new *vault*. Work in the old
 * one is gone, and any caller whose retry could silently write into an empty
 * vault should tell the user rather than pretend the retry succeeded.
 *
 *     const reports = await guard((signal) => api.listReports(signal))
 */
export async function guard<T>(call: (signal?: AbortSignal) => Promise<T>, signal?: AbortSignal): Promise<T> {
  try {
    return await call(signal)
  } catch (error) {
    if (!(error instanceof SessionExpired)) throw error
    await ensureSession(true)
    return call(signal)
  }
}

// ── the hook ─────────────────────────────────────────────────────────────────

export type UseSession = {
  state: SessionState
  /** Convenience: the session, or null while it is still being made. */
  session: Session | null
  /** Try again after a failure. */
  retry: () => void
  /** Throw this vault away and get a new one. */
  reset: () => Promise<Session>
  /** Re-read quota usage. */
  refresh: () => Promise<Session | null>
}

/**
 * The session, bootstrapped on first mount.
 *
 * Every consumer sees the same state — the store is module-level — so calling
 * this in three components costs three subscriptions and no extra requests.
 */
export function useSession(): UseSession {
  const current = useSyncExternalStore(subscribe, snapshot, snapshot)

  useEffect(() => {
    // The rejection is already on the store as `failed`; swallowing it here only
    // stops the console filling with an unhandled rejection nobody can act on.
    if (state.status === 'idle') void ensureSession().catch(() => {})
  }, [])

  const retry = useCallback(() => {
    // Not `force`: the failure may have been the `GET`, and a session may well
    // exist behind the cookie. Minting a second one would orphan the first.
    void ensureSession().catch(() => {})
  }, [])

  return {
    state: current,
    session: current.status === 'ready' ? current.session : null,
    retry,
    reset: resetSession,
    refresh: refreshSession,
  }
}
