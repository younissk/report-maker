/**
 * The whole of the front end's knowledge about a vault: none.
 *
 * Every function here is one HTTP call and one cast. Nothing in this file parses
 * a report, evaluates the citation rule, counts a finding, or decides whether a
 * report is stale — the engine already answered all of that and the server hands
 * the answer over verbatim. A shape in this file that drifts from what the
 * server prints is a bug in this file, not a model to maintain.
 *
 * Three things it does own, because they are properties of *talking to* a
 * server rather than facts about a vault:
 *
 *   1. `credentials: 'include'`, so the HttpOnly session cookie travels.
 *   2. One error shape, mapped from `{"error": {code, message, detail}}`.
 *   3. `SessionExpired` as its own class, so the shell can seed a new session
 *      instead of showing somebody a 401 they cannot act on.
 */

// ── the error shape ──────────────────────────────────────────────────────────

/** The server's error body, exactly as the spec defines it. */
export type ApiErrorBody = {
  error: { code: string; message: string; detail?: string }
}

/**
 * Any refusal from the server, with the engine's own words kept intact.
 *
 * `detail` is usually stderr from a `report-maker` subprocess. Show it — the
 * engine's refusals name the command that fixes them, and paraphrasing one into
 * "something went wrong" throws away the only useful half.
 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly detail: string | null

  constructor(status: number, code: string, message: string, detail?: string | null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail ?? null
  }
}

/**
 * 401. Distinct from every other failure because it has a repair the user never
 * has to see: the session expired (24 h TTL) or the vault was swept, and the
 * shell can POST a new one. Anything that catches this and renders it as an
 * error message has made a recoverable state look like a dead end.
 */
export class SessionExpired extends ApiError {
  constructor(message = 'This working session has expired.', detail?: string | null) {
    super(401, 'session_expired', message, detail)
    this.name = 'SessionExpired'
  }
}

/** 429, with the limit that was hit named in `code` — see the quota table. */
export class QuotaExceeded extends ApiError {
  constructor(code: string, message: string, detail?: string | null) {
    super(429, code, message, detail)
    this.name = 'QuotaExceeded'
  }
}

/** True for the exception an `AbortController` throws, which is never a failure. */
export function isAbort(error: unknown): boolean {
  return (
    error instanceof DOMException &&
    (error.name === 'AbortError' || error.name === 'TimeoutError')
  )
}

/** Human-facing text for anything thrown by this module. */
export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return String(error)
}

// ── payloads ─────────────────────────────────────────────────────────────────
//
// These mirror what `report-maker <cmd> --json` prints, via the server. Where
// the engine has a documented dataclass the type is copied from it; where the
// shape is the server's own invention it is marked, so the API agent and this
// file can be checked against each other in one place.

/** A working session. `POST /api/session`, `GET /api/session`. */
export type Session = {
  /**
   * The server's display name for this session — `POST /api/session` returns a
   * `label` and deliberately never the id, which is the credential and lives in
   * an HttpOnly cookie. Everything the UI needs a session "id" for is identity
   * within this tab, so the label is it.
   */
  id: string
  label: string
  mode: 'try' | 'github'
  /** The vault's own name for display. Never a filesystem path. */
  vault: string
  quota: QuotaUsage
  /** True when this vault is still the untouched starter, so `check` is red. */
  starterFindings?: boolean
  /** The server's own words for why it is red. Shown verbatim on the first run. */
  starterExplainer?: string | null
  /**
   * Whether GitHub mode is configured on this server at all. When it is off the
   * UI says so plainly rather than showing a button that cannot work.
   */
  github?: GitHubAvailability
  /** ISO 8601. */
  created?: string
  /** Seconds until this session is swept. */
  expiresIn?: number
  /** The report `new` seeded into a fresh try-mode vault, when there was one. */
  seeded?: string | null
}

/**
 * Quota, as the session record carries it. Every group is optional so a server
 * that reports three of the four does not break the meter, and each is
 * `{used, limit}` so a bar can be drawn without knowing which quota it is.
 */
export type QuotaUsage = {
  disk?: { used: number; limit: number }
  commands?: { used: number; limit: number; window?: string }
  reports?: { used: number; limit: number }
  [key: string]: unknown
}

export type GitHubAvailability = {
  /** The server has a client id and secret, or a single-user token. */
  enabled: boolean
  /** This session has already connected a repository. */
  connected: boolean
  /** The connected account, for display only. Never a token. */
  login?: string | null
  repo?: string | null
  branch?: string | null
}

/** One row of `list --json`. */
export type ReportRow = {
  id: string
  group: string
  template: string
  built: boolean
  stale: boolean
  title?: string
  kind?: string
  date?: string
  author?: string
  status?: string
}

/** A file or folder inside a report. `GET /reports/:id`. */
export type FileNode = {
  name: string
  /** Vault-relative POSIX. The only form a path is ever allowed to take here. */
  path: string
  kind: 'dir' | 'file'
  children?: FileNode[]
  size?: number
}

/** `GET /reports/:id` — the row, plus what is in the folder. */
export type ReportDetail = ReportRow & { files: FileNode[] }

/** What `new` was asked for. `POST /reports`. */
export type NewReportRequest = {
  title: string
  group?: string
  template?: string
  kind?: string
  author?: string
  withDiagram?: boolean
}

/** One `report-maker` subprocess, reported rather than interpreted. */
export type Run = {
  code: number
  stdout: string
  stderr: string
  command?: string
}

/**
 * What the build produced. The known keys are named; the map is open because
 * `all` grows outputs (`--html` already adds one) and a closed type would make
 * every addition a front-end change.
 */
export type BuildArtefacts = {
  pdf?: string | null
  html?: string | null
  pages?: string | null
  pageCount?: number
  [key: string]: unknown
}

/** `POST /reports/:id/build` — `all <id>`, run synchronously. */
export type BuildResult = {
  ok: boolean
  code: number
  stdout: string
  stderr: string
  artefacts: BuildArtefacts
}

/** `out/pages/<id>/pages.json`, plus the URLs to fetch each page from. */
export type PagesIndex = {
  id: string
  slug: string
  ppi: number
  count: number
  /** File names, as the engine writes them. */
  pages: string[]
  /** Absolute-path URLs on this origin, one per page, in order. */
  urls?: string[]
}

/** One citation-rule finding — `check --json`, from `engine/check.py`. */
export type Finding = {
  level: 'error' | 'warning'
  code: string
  /** Vault-relative POSIX path. */
  path: string
  line: number
  message: string
  report: string
}

export type CheckResult = {
  vault: string
  errors: number
  warnings: number
  findings: Finding[]
  /** Present only when the run passed `--score`. */
  score?: ScoreResult
}

/** How one line of a report reads to the citation rule — drives the evidence rail. */
export type LineClass = {
  line: number
  kind: 'cited' | 'assessed' | 'unmarked' | 'neutral'
}

export type ScoreSection = {
  title: string
  level: number
  cited: number
  assessed: number
  unmarked: number
  density: number
  line: number
}

/** Evidence density for one report — `engine/score.py`. */
export type ReportScore = {
  id: string
  cited: number
  assessed: number
  unmarked: number
  /** (cited + assessed) / (cited + assessed + unmarked). */
  density: number
  sections: ScoreSection[]
  lines: LineClass[]
  sourcesTotal: number
  sourcesCited: number
}

/**
 * `score --json`, and the `score` field of `check --json --score`.
 *
 * The totals are the engine's sums. Never re-average them here: a density
 * averaged over reports in a browser weights a one-line draft the same as a
 * forty-page audit, and then disagrees with what the CLI prints.
 */
export type ScoreResult = {
  reports: ReportScore[]
  cited: number
  assessed: number
  unmarked: number
  density: number
  sourcesTotal: number
  sourcesCited: number
}

/** One entry of `sources <target> --json`. */
export type SourceRow = {
  key: string
  type: string
  title: string
  author: string
  url: string | null
  accessed: string | null
  /** 1-based line of the key in sources.yml, so a panel can jump to it. */
  line: number
  snapshot: { sha256: string; fetched: string } | null
  /** How many claims cite this key; 0 is an orphan (W001). */
  uses: number
}

/**
 * `POST /sources/:id/cite`. Server shape — the CLI prints prose.
 * `created: false` means the URL was already a source and kept its key, which
 * is the documented idempotent path, not a failure.
 */
export type CiteResult = {
  key: string
  url: string
  title?: string | null
  created: boolean
  snapshot?: { path: string; sha256: string; bytes: number } | null
  run?: Run
}

/** One archived source checked against the live page — `verify --json`. */
export type Drift = {
  report: string
  key: string
  url: string
  state: 'ok' | 'changed' | 'gone' | 'error' | 'unsnapshotted' | 'offline'
  detail: string
  fetched: string | null
  /** 0..1 over the extracted text, when both the old and the new text exist. */
  similarity: number | null
}

export type VerifyResult = {
  drifts: Drift[]
  counts: Record<string, number>
}

/** One task on a report's pad. Never cited, never compiled — see The pad. */
export type Todo = {
  text: string
  done: boolean
  line: number
  tags: string[]
  due: string | null
  /** "todos.md" | "main.typ" | "notes.md". `main.typ` markers are read-only. */
  source: string
}

export type TodoReport = {
  id: string
  open: number
  done: number
  todos: Todo[]
  has_notes: boolean
  modified: string | null
}

export type TodosResult = { reports: TodoReport[] }

/** `notes <id> --json`, or null when the report has never had a `notes.md`. */
export type Note = {
  report: string
  path: string
  text: string
  lines: number
  modified: string
}

/** One search hit — `find --json`. `marks` index into `excerpt`. */
export type SearchHit = {
  kind: string
  report: string
  key: string
  path: string
  line: number | null
  offset: number | null
  score: number
  excerpt: string
  marks: [number, number][]
  title: string
  fetched: string | null
}

export type SearchResult = { count: number; hits: SearchHit[] }

/** `templates --json` is a map keyed by design id. */
export type TemplateRow = {
  title: string
  group: string
  description: string
  extends: string | null
  brand: string
  builtin: boolean
  folder: string
}

export type TemplateMap = Record<string, TemplateRow>

/** A brand pack. Open, because a pack may carry keys nothing here has heard of
 *  and they must survive a round trip. */
export type BrandPack = {
  org?: Record<string, string | null>
  colors?: Record<string, string>
  fonts?: Record<string, string[]>
  sizes?: Record<string, string>
  space?: Record<string, string>
  defaults?: Record<string, string>
  [key: string]: unknown
}

/** `sync --status --json`. */
export type GitState = {
  repo: boolean
  branch: string | null
  upstream: string | null
  /** Porcelain paths, vault-relative. */
  dirty: string[]
  ahead: number
  behind: number
  remote: string | null
}

export type SyncResult = GitState & { result?: Run }

/** One repository the connected account can write to. */
export type Repo = {
  full_name: string
  name: string
  owner: string
  private: boolean
  default_branch: string
  updated_at?: string
}

/** `POST /share/:id`. Immutable: re-sharing mints a new token. */
export type Share = { url: string; token: string }

// ── the transport ────────────────────────────────────────────────────────────

const BASE = '/api'

type Query = Record<string, string | number | boolean | null | undefined>

export type RequestOptions = {
  query?: Query
  signal?: AbortSignal
}

function withQuery(path: string, query?: Query): string {
  if (!query) return BASE + path
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === '') continue
    params.set(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${BASE}${path}?${qs}` : BASE + path
}

/**
 * Turn any non-2xx response into one of our own errors.
 *
 * A body that is not the documented envelope still has to produce something a
 * person can read, so the status text is the floor. A server that returns HTML
 * on a 502 must not surface as `undefined`.
 */
async function fail(response: Response): Promise<never> {
  let code = `http_${response.status}`
  let message = response.statusText || `Request failed (${response.status})`
  let detail: string | null = null

  const text = await response.text().catch(() => '')
  if (text) {
    try {
      const body = JSON.parse(text) as Partial<ApiErrorBody>
      if (body && typeof body === 'object' && body.error) {
        code = body.error.code || code
        message = body.error.message || message
        detail = body.error.detail ?? null
      } else {
        detail = text.slice(0, 4000)
      }
    } catch {
      detail = text.slice(0, 4000)
    }
  }

  if (response.status === 401) throw new SessionExpired(message, detail)
  if (response.status === 429) throw new QuotaExceeded(code, message, detail)
  throw new ApiError(response.status, code, message, detail)
}

async function request<T>(
  method: string,
  path: string,
  init: RequestOptions & { body?: unknown } = {}
): Promise<T> {
  const { query, signal, body } = init
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  const response = await fetch(withQuery(path, query), {
    method,
    headers,
    // The session cookie is HttpOnly. Without this it does not travel, and every
    // request looks like a stranger's.
    credentials: 'include',
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: signal ?? null,
  })

  if (!response.ok) await fail(response)
  if (response.status === 204) return undefined as T

  const text = await response.text()
  if (!text) return undefined as T
  return JSON.parse(text) as T
}


/**
 * A request that supersedes itself.
 *
 * Search-as-you-type and check-on-idle both fire faster than the server answers,
 * and the wrong answer arriving last is worse than no answer. Hold one of these
 * per concern; calling it again aborts the flight already in the air.
 *
 *     const search = abortable(api.find)
 *     const hits = await search(query)     // the previous call is cancelled
 *
 * The aborted call rejects with an `AbortError`, which `isAbort()` recognises.
 * It is not a failure and must never be rendered as one.
 */
export function abortable<A extends unknown[], R>(
  fn: (...args: [...A, AbortSignal]) => Promise<R>
): ((...args: A) => Promise<R>) & { cancel: () => void } {
  let controller: AbortController | null = null

  const run = (...args: A): Promise<R> => {
    controller?.abort()
    controller = new AbortController()
    return fn(...args, controller.signal)
  }
  run.cancel = () => {
    controller?.abort()
    controller = null
  }
  return run
}

// ── URLs for things the browser fetches itself ───────────────────────────────
//
// A PNG belongs in an `<img src>` and an HTML bundle in an `<iframe src>`; going
// through `fetch` to make a blob URL would buy nothing and lose the browser's
// own caching. The session cookie rides along on these exactly as it does on a
// JSON call, because they are same-origin.

export const urls = {
  pdf: (id: string) => `${BASE}/reports/${encodePath(id)}/pdf`,
  /** `n` is 1-based, as the page numbers on the pages themselves are. */
  page: (id: string, n: number) => `${BASE}/reports/${encodePath(id)}/page/${n}`,
  html: (id: string) => `${BASE}/reports/${encodePath(id)}/html`,
  /** Public, no session, no path — the whole point of a share link. */
  share: (token: string) => `/s/${encodeURIComponent(token)}`,
}

/**
 * A report id is a path — `clients/acme/2026-08-12-audit` — and its slashes are
 * structural. Encode each segment, keep the separators.
 */
export function encodePath(id: string): string {
  return id.split('/').filter(Boolean).map(encodeURIComponent).join('/')
}

// ── envelopes ────────────────────────────────────────────────────────────────
//
// The server wraps most answers in a named key — `{"reports": […]}`,
// `{"sources": […]}`, `{"notes": null}` — so that a body is always an object and
// can always carry a second field later without breaking a caller. Unwrapping
// happens here, in one place, and nowhere else: a screen that reached into
// `.reports` itself would be a screen that has to be edited when the envelope
// changes.
//
// This is unwrapping, not interpretation. Nothing below counts a finding,
// decides whether a report is stale, or computes a density. Where a field is
// moved it is moved verbatim.

type Enveloped<K extends string, T> = { [P in K]: T }

function pickTodoReport(body: TodosResult, id: string): TodoReport {
  const rows = body?.reports ?? []
  return (
    rows.find((row) => row.id === id) ??
    rows[0] ?? { id, open: 0, done: 0, todos: [], has_notes: false, modified: null }
  )
}

function unwrap<K extends string, T>(body: Enveloped<K, T>, key: K, fallback: T): T {
  const value = body?.[key]
  return value === undefined ? fallback : value
}

/**
 * `GET /api/session`, to the shape the UI codes against.
 *
 * The server answers with a `label`, a `github` connection and a separate
 * `githubStatus` saying whether GitHub is configured on this server at all.
 * The UI asks one question — "should the button exist, and is it connected" —
 * so the two are joined here rather than in every panel that cares.
 */
function adaptSession(body: RawSession): Session {
  const status = body.githubStatus
  const connection = body.github
  return {
    ...body,
    id: body.label,
    label: body.label,
    vault: body.label,
    mode: body.mode ?? 'try',
    quota: body.quota ?? {},
    github: {
      enabled: Boolean(status?.available ?? status?.configured),
      connected: Boolean(connection?.connected),
      login: connection?.login ?? null,
      repo: connection?.repo ?? body.repo ?? null,
      branch: connection?.branch ?? body.branch ?? null,
    },
  }
}

type RawSession = {
  label: string
  mode?: 'try' | 'github'
  quota?: QuotaUsage
  repo?: string | null
  branch?: string | null
  github?: { connected?: boolean; login?: string | null; repo?: string | null; branch?: string | null }
  githubStatus?: { configured?: boolean; available?: boolean; mode?: string; reason?: string }
  [key: string]: unknown
}

/**
 * The manifest entry, flattened to the row every list already uses.
 *
 * `GET /api/reports/:id` answers with the manifest's own nested entry — `meta`,
 * `source`, `state` — while `GET /api/reports` answers with rows that are
 * already flat. One shape reaches the UI, and it is the flat one, because a
 * pane should not have to know which route its report came from.
 */
function adaptReportDetail(body: RawDetail): ReportDetail {
  const entry = body.report ?? {}
  const meta = entry.meta ?? {}
  const state = entry.state ?? {}
  return {
    id: String(entry.id ?? ''),
    group: String(entry.group ?? ''),
    template: String(entry.template ?? ''),
    built: Boolean(state.built),
    stale: Boolean(state.stale),
    title: meta.title,
    kind: meta.kind,
    date: meta.date,
    author: meta.author,
    status: meta.status,
    files: (body.files ?? []).map((file) => ({
      name: file.name,
      path: file.path,
      kind: 'file' as const,
      size: file.bytes,
    })),
  }
}

type RawDetail = {
  report?: {
    id?: string
    group?: string
    template?: string
    meta?: Record<string, string | undefined>
    state?: { built?: boolean; stale?: boolean }
    [key: string]: unknown
  }
  files?: { name: string; path: string; bytes?: number; editable?: boolean }[]
}

// ── the routes ───────────────────────────────────────────────────────────────

export const api = {
  // session
  createSession: (signal?: AbortSignal) =>
    request<RawSession>('POST', '/session', { signal }).then(adaptSession),
  getSession: (signal?: AbortSignal) =>
    request<RawSession>('GET', '/session', { signal }).then(adaptSession),
  destroySession: (signal?: AbortSignal) =>
    request<void>('DELETE', '/session', { signal }),

  // reports
  listReports: (signal?: AbortSignal) =>
    request<Enveloped<'reports', ReportRow[]>>('GET', '/reports', { signal }).then((body) =>
      unwrap(body, 'reports', [])
    ),

  /**
   * `new`, and then the id the engine actually chose.
   *
   * The server answers `{created, reports}` — `created` is the id, and the list
   * beside it is the vault after the write. The id is never re-derived from the
   * title here: slugging is the engine's rule, and a second implementation of it
   * in a browser would disagree the first time somebody uses a colon.
   */
  createReport: (body: NewReportRequest, signal?: AbortSignal) =>
    request<{ created: string | null; reports: ReportRow[] }>('POST', '/reports', {
      body,
      signal,
    }).then((result) => {
      const row = (result.reports ?? []).find((r) => r.id === result.created)
      if (row) return row
      if (result.created) return { id: result.created, group: '', template: '', built: false, stale: true }
      throw new ApiError(500, 'no_id', 'The report was created but the server did not name it.')
    }),

  getReport: (id: string, signal?: AbortSignal) =>
    request<RawDetail>('GET', `/reports/${encodePath(id)}`, { signal }).then(adaptReportDetail),

  /** File text, verbatim. `path` is vault-relative; the server rejects escapes. */
  readFile: (id: string, path: string, signal?: AbortSignal) =>
    request<{ path: string; text: string }>('GET', `/reports/${encodePath(id)}/file`, {
      query: { path },
      signal,
    }).then((body) => body.text ?? ''),
  /**
   * The write is JSON, not a raw body. `{"text": …}` keeps the payload a single
   * shape the server can validate before it touches the disk, and leaves room
   * for the write to grow a field without changing its content type.
   */
  writeFile: (id: string, path: string, text: string, signal?: AbortSignal) =>
    request<{ path: string; bytes: number }>('PUT', `/reports/${encodePath(id)}/file`, {
      query: { path },
      body: { text },
      signal,
    }),

  /**
   * `all <id>`, synchronous, under the server's 60 s cap. A non-zero `code` is
   * an answer, not an exception: a build that fails the citation rule is the
   * normal state of an unfinished report and the findings are the product.
   */
  build: (id: string, signal?: AbortSignal) =>
    request<BuildResult>('POST', `/reports/${encodePath(id)}/build`, { signal }),

  /** Read this on mobile, never the PDF — iOS Safari cannot show one usefully. */
  pages: (id: string, signal?: AbortSignal) =>
    request<{ count: number; ppi?: number; pages?: string[] }>(
      'GET',
      `/reports/${encodePath(id)}/pages`,
      { signal }
    ).then((body) => ({
      id,
      slug: id,
      ppi: body.ppi ?? 0,
      count: body.count ?? 0,
      // The server hands back URLs, not file names — which is exactly what a
      // phone needs, and why this route exists at all.
      pages: body.pages ?? [],
      urls: body.pages ?? [],
    })),

  // the rule
  check: (target?: string, signal?: AbortSignal) =>
    request<CheckResult>('GET', '/check', { query: { target }, signal }),
  score: (target?: string, signal?: AbortSignal) =>
    request<ScoreResult>('GET', '/score', { query: { target }, signal }),

  // evidence
  sources: (id: string, signal?: AbortSignal) =>
    request<Enveloped<'sources', SourceRow[]>>('GET', `/sources/${encodePath(id)}`, {
      signal,
    }).then((body) => unwrap(body, 'sources', [])),
  /** Fetch a page, archive it, add it to sources.yml. SSRF-checked server-side. */
  cite: (id: string, url: string, signal?: AbortSignal) =>
    request<CiteResult>('POST', `/sources/${encodePath(id)}/cite`, {
      body: { url },
      signal,
    }),
  /** Offline by default: report the archive without dialling out. */
  /**
   * Offline by default. The server takes `online` — and refuses an online run
   * for a whole vault, because every URL it would fetch has to be judged first
   * and there is no single list to judge.
   */
  verify: (target?: string, offline = true, signal?: AbortSignal) =>
    request<VerifyResult>('GET', '/verify', {
      query: { target, online: offline ? undefined : true },
      signal,
    }),

  // the pad
  todos: (target?: string, open?: boolean, signal?: AbortSignal) =>
    request<TodosResult>('GET', '/todos', { query: { target, open }, signal }),
  // A write to the pad answers with the whole vault's todos, same as the read.
  // The row for the report that was written is picked out here so a caller sees
  // what it asked about rather than what it did not.
  addTodo: (id: string, text: string, signal?: AbortSignal) =>
    request<TodosResult>('POST', `/todos/${encodePath(id)}`, { body: { text }, signal }).then(
      (body) => pickTodoReport(body, id)
    ),
  setTodo: (id: string, line: number, done: boolean, signal?: AbortSignal) =>
    request<TodosResult>('POST', `/todos/${encodePath(id)}`, {
      body: { line, done },
      signal,
    }).then((body) => pickTodoReport(body, id)),
  notes: (id: string, signal?: AbortSignal) =>
    request<Enveloped<'notes', Note | null>>('GET', `/notes/${encodePath(id)}`, {
      signal,
    }).then((body) => unwrap(body, 'notes', null)),
  writeNotes: (id: string, text: string, signal?: AbortSignal) =>
    request<{ bytes: number }>('PUT', `/notes/${encodePath(id)}`, { body: { text }, signal }),

  // reading the vault back
  find: (q: string, signal?: AbortSignal) =>
    request<SearchResult>('GET', '/find', { query: { q }, signal }),
  templates: (signal?: AbortSignal) =>
    request<Enveloped<'templates', TemplateMap>>('GET', '/templates', { signal }).then((body) =>
      unwrap(body, 'templates', {})
    ),
  brand: (signal?: AbortSignal) => request<BrandPack>('GET', '/brand', { signal }),
  writeBrand: (pack: BrandPack, signal?: AbortSignal) =>
    request<BrandPack>('PUT', '/brand', { body: pack, signal }),

  // git, github mode only
  gitState: (signal?: AbortSignal) => request<GitState>('GET', '/git/state', { signal }),
  gitSync: (message: string, push = false, signal?: AbortSignal) =>
    request<SyncResult>('POST', '/git/sync', { body: { message, push }, signal }),
  githubRepos: (signal?: AbortSignal) =>
    request<Enveloped<'repos', Repo[]>>('GET', '/github/repos', { signal }).then((body) =>
      unwrap(body, 'repos', [])
    ),
  githubConnect: (repo: string, branch?: string, signal?: AbortSignal) =>
    request<RawSession>('POST', '/github/connect', { body: { repo, branch }, signal }).then(
      adaptSession
    ),

  // the point
  share: (id: string, signal?: AbortSignal) =>
    request<Share>('POST', `/share/${encodePath(id)}`, { signal }),
}

export type Api = typeof api
