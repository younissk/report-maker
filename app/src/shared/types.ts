/**
 * The IPC vocabulary, shared by all three processes.
 *
 * It lives outside main/, preload/ and renderer/ because all three compile as
 * separate TypeScript projects: a type reached across that boundary has to sit
 * somewhere every project includes.
 */

/**
 * One remembered vault.
 *
 * The app's only state about a vault is that it was opened: a vault is a folder
 * holding `report-maker.toml` and it knows nothing about this list, so
 * everything here is either the path itself or a fact the app observed about it.
 */
export type VaultEntry = {
  path: string
  /** The folder's own name — what a person calls the vault. */
  name: string
  /** ISO 8601, the last time this vault was made current. */
  openedAt: string | null
  /** Kept above the rest, ahead of recency. */
  pinned: boolean
  /**
   * The folder is gone, or no longer holds `report-maker.toml`. A missing vault
   * stays on the list, greyed, with a remove action: a vault on an unmounted
   * drive is not a vault to forget, and silently dropping it would make the list
   * disagree with what the user remembers doing.
   */
  missing: boolean
}

export type VaultList = {
  /**
   * Paths in display order — pinned first, then most recently opened. The bare
   * strings are kept because most callers only need to know which folders exist;
   * `entries` carries the same list with everything else known about it.
   */
  vaults: string[]
  entries: VaultEntry[]
  current: string | null
}

export type Run = { code: number; stdout: string; stderr: string; command: string }

/**
 * What recovering the login shell's PATH found — see `main/env.ts` for why the
 * app has to go looking for it at all. Reported rather than merely logged,
 * because "typst is not installed" and "typst is not on the PATH this app was
 * launched with" are the same symptom and need different fixes.
 */
export type PathProbe = {
  /** The login shell that was asked, or null when there was none to ask. */
  shell: string | null
  /** The shell answered. False means only the hardcoded fallback applied. */
  ok: boolean
  /** In words: what the shell said, or why it said nothing. */
  detail: string
  /** Entries the shell contributed that were not on PATH already. */
  fromShell: string[]
  /** Entries the hardcoded fallback contributed, which happens either way. */
  fromFallback: string[]
  /** The PATH the app will actually spawn with. */
  path: string
}

/**
 * Everything the app can say about its own installation: which CLI it will
 * spawn, what version answered, `report-maker doctor` verbatim, and the PATH
 * that search happened on. The doctor text is the engine's answer, reproduced
 * rather than parsed — the app has no opinion about what is installed.
 */
export type Diagnostics = {
  /** The path `engine.locate()` settled on, or "not found". */
  engine: string
  /** null when the engine predates `--version`. */
  version: string | null
  /** `report-maker doctor`, stdout and stderr as printed. */
  doctor: string
  /** Non-zero when `doctor` itself could not run. */
  code: number
  path: PathProbe | null
}

/**
 * A menu item the renderer has to act on, because the state it changes lives
 * there. Main handles alone everything it can — the folder dialogs, the
 * diagnostics box — and sends only what it cannot.
 */
export type MenuCommand =
  | { kind: 'new-report' }
  | { kind: 'open-vault' }
  | { kind: 'create-vault' }
  | { kind: 'select-vault'; path: string }
  | { kind: 'save' }
  | { kind: 'build' }

export type Node = {
  name: string
  path: string
  rel: string
  kind: 'dir' | 'file'
  children?: Node[]
}

export type OpenResult = { ok: true; list: VaultList } | { ok: false; reason: string }

/** One row of `report-maker list --json`. */
export type ReportRow = {
  id: string
  group: string
  template: string
  built: boolean
  stale: boolean
  title?: string
  kind?: string
  date?: string
}

/** One value of `report-maker templates --json`, which is a map keyed by design id. */
export type TemplateRow = {
  title: string
  group: string
  description: string
  extends: string | null
  brand: string
  builtin: boolean
  folder: string
}

// ── The engine's --json output ───────────────────────────────────────────────
//
// These mirror the dataclasses in engine/ exactly. They are declarations of what
// the CLI prints, not a model the app maintains: nothing here is ever computed
// in the renderer, and a shape that drifts from the engine is a bug in this file.

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
  /** Every line of the file, so the rail can be drawn without re-parsing. */
  lines: LineClass[]
  sourcesTotal: number
  sourcesCited: number
}

/**
 * `score --json`, and the `score` field of `check --json --score`.
 *
 * The totals are the engine's own sums, not something to recompute here: a
 * density averaged over reports in the renderer would weight a one-line draft
 * the same as a forty-page audit, and disagree with what the CLI prints. There
 * is deliberately no `vault` key — `engine/score.py:to_json` does not emit one,
 * and the surrounding `CheckResult` is where the vault path comes from.
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

/** One entry of `sources <target> --json` — `engine/sources.py` plus use counts. */
export type SourceRow = {
  key: string
  type: string
  title: string
  author: string
  url: string | null
  accessed: string | null
  /** 1-based line of the key in sources.yml, so the panel can jump to it. */
  line: number
  snapshot: { sha256: string; fetched: string } | null
  /** How many claims cite this key; 0 is an orphan (W001). */
  uses: number
}

/** One archived source checked against the live page — `verify --json`. */
export type Drift = {
  report: string
  key: string
  url: string
  state: 'ok' | 'changed' | 'gone' | 'error' | 'unsnapshotted' | 'offline'
  detail: string
  /** 0..1 over the extracted text, when both the old and new text exist. */
  similarity: number | null
  /** ISO datetime of the original snapshot; the engine's dataclass carries it. */
  fetched?: string | null
}

/** `sync --status --json` — `engine/gitsync.py`. */
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

/** One commit touching a report — `sync --log <path> --json`. */
export type GitLogEntry = {
  sha: string
  short: string
  subject: string
  author: string
  date: string
}

/** One semantic change between two revisions of a report — `diff --json`. */
export type Change = {
  /** "source-added" | "claim-changed" | "assessment-removed" | … */
  kind: string
  /** A source key, or a short digest of the claim. */
  key: string
  before: string | null
  after: string | null
  line: number | null
}

export type ReportDiff = {
  id: string
  rev: string
  changes: Change[]
  /** e.g. {"claims": {"added": 2, "removed": 0, "changed": 1}, "sources": {…}}. */
  counts: Record<string, Record<string, number>>
}

/**
 * A brand pack as it sits on disk — `brand/brand.json`, or the resolved pack the
 * engine prints. The nested groups are open maps because the brand studio renders
 * whatever keys the pack actually has rather than a list it hard-codes.
 */
export type BrandPack = {
  org: {
    name: string
    url: string | null
    logo: string | null
    'logo-inverse': string | null
    'logo-width': string
    'logo-width-header': string
  }
  colors: Record<string, string>
  fonts: Record<string, string[]>
  sizes: Record<string, string>
  space: Record<string, string>
  'page-margin': Record<string, string>
  defaults: Record<string, string>
  mermaid?: Record<string, string>
  /** Packs may carry keys this app has never heard of; they must survive a round trip. */
  [key: string]: unknown
}

// ── App state ────────────────────────────────────────────────────────────────

/**
 * Preferences. Global rather than per-vault — they describe how you work, not
 * what a vault contains, and a vault must stay a folder anyone can open with any
 * copy of the app. Persisted next to vaults.json in userData.
 */
export type Settings = {
  appearance: {
    theme: 'system' | 'light' | 'dark'
    accent: string
    density: 'comfortable' | 'compact'
  }
  editor: {
    fontFamily: string
    fontSize: number
    lineHeight: number
    lineNumbers: boolean
    wordWrap: boolean
    tabSize: number
    highlightActiveLine: boolean
    bracketMatching: boolean
    evidenceRail: boolean
    lintGutter: boolean
    /**
     * `'auto'` follows the app chrome and is the default. The two `report-*`
     * themes name their own polarity, which is exactly the trap: a light machine
     * on `theme: 'system'` used to get light chrome wrapped around a dark
     * editor, because the default named a polarity instead of deferring to one.
     */
    syntaxTheme: 'auto' | 'report-light' | 'report-dark' | 'mono' | 'solarized' | 'high-contrast'
  }
  build: {
    /** null disables autosave. */
    autoSaveMs: number | null
    buildOnSave: boolean
    watch: boolean
    checkOnIdleMs: number
  }
  git: {
    autoCommit: boolean
    autoPush: boolean
    debounceMs: number
    messageTemplate: string
  }
  vaults: {
    /** Vault path → the target last built there. */
    lastTarget: Record<string, string>
  }
  /**
   * What the app does with no vault named on the command line. `RM_OPEN_VAULT`
   * and an argv path still win over both of these — being able to name a vault
   * at launch is what makes the app scriptable, and the smoke test drives it.
   */
  startup: {
    reopenLast: boolean
    /** Written every time a vault is made current, read only at launch. */
    lastVault: string | null
  }
  /**
   * Pane geometry. It lives here rather than in localStorage because it is a
   * preference like any other: it should survive a cleared web store, move with
   * the rest of the settings file, and be resettable from the same button.
   */
  layout: {
    /** Panel id → percentage width, as the resizable group reports it. */
    panes: Record<string, number>
    sidebar: boolean
    viewer: boolean
    problems: boolean
  }
}

/** A patch may name a single leaf — `{ editor: { fontSize: 14 } }` — and the main
 *  process merges it over what is stored. */
export type DeepPartial<T> = {
  [K in keyof T]?: T[K] extends Record<string, unknown> ? DeepPartial<T[K]> : T[K]
}

/** One line of a live `report-maker watch` run, streamed to the renderer. */
export type WatchEvent = {
  kind: 'start' | 'stdout' | 'stderr' | 'exit'
  text?: string
  code?: number
}
