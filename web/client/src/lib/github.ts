/**
 * Connecting a repository, over `/api/github/*` and `/api/git/*`.
 *
 * Two states and one transition. Before: the session's vault is ephemeral and
 * will be swept in a day. After: the vault is a clone of a repo the user owns,
 * and `sync` is how work leaves the browser. The repo is the store — this
 * server persists nothing of its own — so the connect action is the moment a
 * try-mode session stops being disposable, and it is worth saying so plainly.
 *
 * Three things this file will not do:
 *
 *   1. It never handles a token. GitHub credentials live in the session record
 *      server-side and no route returns one. There is no field for one here and
 *      there must never be.
 *   2. It never pushes on its own. `sync(message, push)` is called with `push`
 *      true only from a control the user tapped, because a push is the one
 *      action in this product that leaves the machine.
 *   3. It never paraphrases a refusal. `engine/gitsync.py` declines a push with
 *      the exact command that fixes it — no upstream, behind the remote, nothing
 *      staged — and that text is the useful half. `Failure.detail` carries it.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  api,
  ApiError,
  errorText,
  isAbort,
  type GitHubAvailability,
  type GitState,
  type Repo,
  type SyncResult,
} from './api'
import { guard, refreshSession, useSession } from './session'

export type Failure = { message: string; detail: string | null }

function describe(error: unknown): Failure {
  if (error instanceof ApiError) return { message: error.message, detail: error.detail }
  return { message: errorText(error), detail: null }
}

/**
 * Whether this session is working against a repository.
 *
 * Read from the session rather than inferred from whether `git/state` answered:
 * a vault that happens to contain a `.git` directory is not the same thing as a
 * session the server connected to a repo, and only the server knows which it is
 * looking at.
 */
export function isConnected(github: GitHubAvailability | null, mode: string | undefined): boolean {
  return Boolean(github?.connected) || mode === 'github'
}

/** Nothing to commit, nothing ahead: the sentence a status line should say. */
export function isClean(state: GitState | null): boolean {
  return Boolean(state && state.dirty.length === 0 && state.ahead === 0)
}

/**
 * Why a push cannot be attempted right now, or null when it can.
 *
 * A pre-flight for the button's own disabled state only — the engine runs the
 * real one and refuses with better words. Never treat a null here as permission
 * for anything; it only means "worth sending".
 */
export function pushBlocked(state: GitState | null): string | null {
  if (!state) return 'No git state yet.'
  if (!state.repo) return 'This vault is not a git repository.'
  if (!state.upstream) return `No upstream for ${state.branch ?? 'this branch'} yet.`
  if (state.behind > 0) {
    const plural = state.behind === 1 ? '' : 's'
    return `${state.branch ?? 'This branch'} is ${state.behind} commit${plural} behind ${state.upstream}.`
  }
  return null
}

// ── The hook ─────────────────────────────────────────────────────────────────

export type UseConnect = {
  /** What the server says about GitHub, or null while the session is loading. */
  availability: GitHubAvailability | null
  /** The server has a client id and secret, or a single-user token. */
  enabled: boolean
  connected: boolean

  repos: Repo[]
  loadingRepos: boolean
  reposError: Failure | null
  loadRepos: () => void

  /** The `full_name` currently being cloned, or null. */
  connecting: string | null
  connectError: Failure | null
  connect: (repo: string, branch?: string) => Promise<boolean>

  state: GitState | null
  loadingState: boolean
  stateError: Failure | null
  refreshState: () => void

  syncing: boolean
  syncError: Failure | null
  lastSync: SyncResult | null
  /** `push` is only ever true because somebody tapped the push control. */
  sync: (message: string, push: boolean) => Promise<SyncResult | null>
}

export function useConnect(): UseConnect {
  const { session } = useSession()
  const availability = session?.github ?? null
  const enabled = Boolean(availability?.enabled)
  const connected = isConnected(availability, session?.mode)

  const [repos, setRepos] = useState<Repo[]>([])
  const [loadingRepos, setLoadingRepos] = useState(false)
  const [reposError, setReposError] = useState<Failure | null>(null)
  const [reposNonce, setReposNonce] = useState(0)
  const [reposWanted, setReposWanted] = useState(false)

  const [connecting, setConnecting] = useState<string | null>(null)
  const [connectError, setConnectError] = useState<Failure | null>(null)

  const [state, setState] = useState<GitState | null>(null)
  const [loadingState, setLoadingState] = useState(false)
  const [stateError, setStateError] = useState<Failure | null>(null)
  const [stateNonce, setStateNonce] = useState(0)

  const [syncing, setSyncing] = useState(false)
  const [syncError, setSyncError] = useState<Failure | null>(null)
  const [lastSync, setLastSync] = useState<SyncResult | null>(null)

  // Repositories are only listed once somebody has asked to see them. The call
  // hits GitHub's API on the user's behalf, and doing that because a panel
  // happened to mount would spend somebody's rate limit on a glance.
  useEffect(() => {
    if (!enabled || !reposWanted) return
    const controller = new AbortController()
    setLoadingRepos(true)
    setReposError(null)
    void guard((signal) => api.githubRepos(signal), controller.signal)
      .then((rows) => {
        if (controller.signal.aborted) return
        setRepos(rows)
        setLoadingRepos(false)
      })
      .catch((failure) => {
        if (isAbort(failure) || controller.signal.aborted) return
        setReposError(describe(failure))
        setLoadingRepos(false)
      })
    return () => controller.abort()
  }, [enabled, reposWanted, reposNonce])

  useEffect(() => {
    if (!connected) {
      setState(null)
      return
    }
    const controller = new AbortController()
    setLoadingState(true)
    setStateError(null)
    void guard((signal) => api.gitState(signal), controller.signal)
      .then((next) => {
        if (controller.signal.aborted) return
        setState(next)
        setLoadingState(false)
      })
      .catch((failure) => {
        if (isAbort(failure) || controller.signal.aborted) return
        setStateError(describe(failure))
        setLoadingState(false)
      })
    return () => controller.abort()
  }, [connected, stateNonce])

  const loadRepos = useCallback(() => {
    setReposWanted(true)
    setReposNonce((n) => n + 1)
  }, [])

  const refreshState = useCallback(() => setStateNonce((n) => n + 1), [])

  const connect = useCallback(
    async (repo: string, branch?: string): Promise<boolean> => {
      setConnecting(repo)
      setConnectError(null)
      try {
        await guard((signal) => api.githubConnect(repo, branch, signal))
        // The session's mode and repo both changed. Re-read it rather than
        // patching a copy here: the server's record is the one that decides
        // what this session now is.
        await refreshSession()
        setStateNonce((n) => n + 1)
        return true
      } catch (failure) {
        if (!isAbort(failure)) setConnectError(describe(failure))
        return false
      } finally {
        setConnecting(null)
      }
    },
    []
  )

  const sync = useCallback(
    async (message: string, push: boolean): Promise<SyncResult | null> => {
      const text = message.trim()
      if (!text) return null
      setSyncing(true)
      setSyncError(null)
      try {
        const result = await guard((signal) => api.gitSync(text, push, signal))
        setLastSync(result)
        // `SyncResult extends GitState`, so the answer to "what happened" is
        // also the answer to "what is the state now".
        setState(result)
        return result
      } catch (failure) {
        if (!isAbort(failure)) setSyncError(describe(failure))
        return null
      } finally {
        setSyncing(false)
      }
    },
    []
  )

  return {
    availability,
    enabled,
    connected,
    repos,
    loadingRepos,
    reposError,
    loadRepos,
    connecting,
    connectError,
    connect,
    state,
    loadingState,
    stateError,
    refreshState,
    syncing,
    syncError,
    lastSync,
    sync,
  }
}
