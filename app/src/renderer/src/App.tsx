import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  FileText,
  Hammer,
  History,
  LayoutGrid,
  LayoutTemplate,
  Link2,
  ListTodo,
  Palette as PaletteIcon,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  SlidersHorizontal
} from 'lucide-react'
import type {
  CheckResult,
  Drift,
  Finding,
  GitState,
  LineClass,
  Node,
  OpenResult,
  ReportRow,
  ReportScore,
  Run,
  SourceRow,
  VaultList
} from '../../shared/types'
import { BrandStudio } from './components/BrandStudio'
import { CsvEditor, type CsvEditorHandle } from './components/CsvEditor'
import { Dashboard } from './components/Dashboard'
import { Designs } from './components/Designs'
import { Editor, type EditorHandle } from './components/Editor'
import { citationHover } from './components/EvidencePopover'
import { FileTree } from './components/FileTree'
import { MermaidEditor } from './components/MermaidEditor'
import { NewReport } from './components/NewReport'
import { NotesPanel } from './components/NotesPanel'
import { Palette } from './components/Palette'
import { Problems, type ProblemsTab } from './components/Problems'
import { SearchPanel } from './components/SearchPanel'
import { Settings as SettingsDialog } from './components/Settings'
import { AddSourceDialog, SourcesPanel } from './components/SourcesPanel'
import { StatusBar, type Cursor, type GitNote } from './components/StatusBar'
import { Timeline } from './components/Timeline'
import { VaultSwitcher } from './components/VaultSwitcher'
import { Viewer } from './components/Viewer'
import { Welcome } from './components/Welcome'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup
} from '@/components/ui/resizable'
import { Separator } from '@/components/ui/separator'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  buildCommands,
  commandForEvent,
  type CommandActions,
  type Pane,
  type SideTab
} from '@/lib/commands'
import { citationCompletionExtension } from '@/lib/complete'
import { isCsvPath } from '@/lib/csv'
import { useNotes } from '@/lib/notes'
import { locate, relative } from '@/lib/report'
import { useSettings } from '@/lib/settings'
import { describeError, snapshotPath, useSources, useUrlDrop } from '@/lib/sources'

/**
 * The shell.
 *
 * Everything below is wiring: which panel is on screen, which engine command
 * answered it last, and which of them needs re-asking after a save. No question
 * about a vault is answered here — `check --json` says what is wrong,
 * `score --json` says how much of it is cited, `sync --status --json` says what
 * git thinks. This file's whole job is deciding *when* to ask and *where to put
 * the answer*.
 */

/** The screens. Not a router: five states and a switch, which is all a desktop
 *  window with no URL bar has ever needed. */
type View = 'editor' | 'dashboard' | 'brand' | 'timeline' | 'designs'

/** `stdout` as JSON, or null. Used instead of `engine.json` for the commands
 *  whose exit code carries meaning: `check` exits non-zero on an error-level
 *  finding, which is the ordinary state of a report being written, and
 *  `engine.json` throws on that — taking the findings with it. */
function parseJson<T>(text: string): T | null {
  try {
    return JSON.parse(text) as T
  } catch {
    return null
  }
}

/** The last line the CLI printed — what a status bar can hold. */
function tail(run: Run): string {
  const lines = (run.stdout + run.stderr)
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  return lines[lines.length - 1] ?? (run.code === 0 ? 'done' : `exit ${run.code}`)
}

export function App() {
  const [list, setList] = useState<VaultList>({ vaults: [], entries: [], current: null })
  const [nodes, setNodes] = useState<Node[]>([])
  const [reports, setReports] = useState<ReportRow[]>([])
  const [openPath, setOpenPath] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [dirty, setDirty] = useState(false)
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [revision, setRevision] = useState(0)
  const [engineAt, setEngineAt] = useState('locating…')
  const [engineVersion, setEngineVersion] = useState<string | null>(null)
  const [demoVault, setDemoVault] = useState<string | null>(null)

  // ── what is on screen
  const [view, setView] = useState<View>('editor')
  const [sideTab, setSideTab] = useState<SideTab>('files')
  const [panes, setPanes] = useState({ sidebar: true, viewer: true, problems: false })
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [newOpen, setNewOpen] = useState(false)
  /** The design the new-report dialog should open on — set when it was raised
   *  from a card on the Designs screen, cleared otherwise. */
  const [newTemplate, setNewTemplate] = useState<string | undefined>(undefined)
  /** null closed; '' opens the cite dialog with an empty URL. */
  const [addUrl, setAddUrl] = useState<string | null>(null)

  // ── what the engine last said
  const [check, setCheck] = useState<CheckResult | null>(null)
  const [drifts, setDrifts] = useState<Drift[]>([])
  const [lastRun, setLastRun] = useState<Run | null>(null)
  const [problemsTab, setProblemsTab] = useState<ProblemsTab>('findings')
  const [problemsFocus, setProblemsFocus] = useState<{ path: string; line: number } | null>(null)
  const [git, setGit] = useState<GitState | null>(null)
  const [gitNote, setGitNote] = useState<GitNote | null>(null)
  const [syncing, setSyncing] = useState(false)

  // ── the editor
  const editor = useRef<EditorHandle>(null)
  /**
   * The grid, when a `.csv` is open.
   *
   * A CSV does not go through `text`: the editor owns its own buffer, because
   * the bytes it writes back have to be the bytes it read. `sha_of` hashes raw
   * bytes, so a semicolon/CRLF export re-serialised through a generic writer
   * comes back as UTF-8/LF with different quoting — no number moved, the
   * checksum did, and E011 fires on a file nobody edited. Hence a second handle
   * rather than a second path into `files.write`.
   */
  const csvEditor = useRef<CsvEditorHandle>(null)
  const [csvDirty, setCsvDirty] = useState(false)
  const [atLine, setAtLine] = useState<number | null>(null)
  const [cursorLine, setCursorLine] = useState(1)
  const [pageCount, setPageCount] = useState<number | null>(null)
  const [savedAt, setSavedAt] = useState(0)
  const [builtAt, setBuiltAt] = useState(0)
  const [watching, setWatching] = useState(false)

  const { settings, loaded: settingsLoaded, update: updateSettings } = useSettings()
  const vault = list.current

  /**
   * Which panes were open last time.
   *
   * Adopted once, when the settings arrive, and written back on every change
   * after that. The one-way flag is what stops the write-back from fighting the
   * adoption: without it, the first render's defaults would be persisted over
   * whatever was stored a moment before it was read.
   */
  const adoptedPanes = useRef(false)
  useEffect(() => {
    if (!settingsLoaded || adoptedPanes.current) return
    adoptedPanes.current = true
    const { sidebar, viewer, problems } = settings.layout
    setPanes({ sidebar, viewer, problems })
  }, [settingsLoaded, settings.layout])

  useEffect(() => {
    if (!adoptedPanes.current) return
    updateSettings({ layout: { ...panes } })
  }, [panes, updateSettings])

  // ── loading ────────────────────────────────────────────────────────────────

  const refresh = useCallback(async (target: string | null) => {
    if (!target) {
      setNodes([])
      setReports([])
      return
    }
    setNodes(await window.api.files.tree(target))
    try {
      setReports(await window.api.engine.json<ReportRow[]>(target, ['list', '--json']))
    } catch (err) {
      setReports([])
      setStatus(describeError(err))
    }
  }, [])

  useEffect(() => {
    window.api.engine.where().then(setEngineAt)
    window.api.vaults.list().then(async (loaded) => {
      setList(loaded)
      await refresh(loaded.current)
    })
  }, [refresh])

  // The engine ships a demo vault in its own checkout. Offer it on the first-run
  // screen when it is actually there — a packaged install may have the CLI and
  // no repo around it.
  useEffect(() => {
    if (!engineAt.endsWith('/bin/report-maker')) return
    const candidate = `${engineAt.slice(0, -'/bin/report-maker'.length)}/examples/demo-vault`
    void window.api.files
      .exists(candidate, `${candidate}/report-maker.toml`)
      .then((there) => setDemoVault(there ? candidate : null))
      .catch(() => setDemoVault(null))
  }, [engineAt])

  // The version, asked for rather than assumed. An engine without `--version`
  // answers with argparse's usage on stderr, which is not a version — so nothing
  // is shown rather than something wrong.
  //
  // Asked through `engine.version()`, which runs `--version` with no vault at
  // all. It used to go through `run(demoVault, …)`, and that made the answer
  // depend on a folder only a dev checkout has: a packaged app ships `bin/` and
  // `engine/` and no `examples/`, so `demoVault` was null, the effect returned
  // before spawning anything, and the Welcome screen said "version unavailable"
  // for every installed copy — reporting a broken engine when the engine was
  // fine. `--version` is answered before argparse looks for a subcommand
  // precisely so it can be asked without a vault; this asks it that way.
  useEffect(() => {
    void window.api.engine
      .version()
      .then((found) => setEngineVersion(found))
      .catch(() => setEngineVersion(null))
  }, [engineAt])

  const forget = useCallback(() => {
    setOpenPath(null)
    setText('')
    setDirty(false)
    setStatus('')
    setCheck(null)
    setDrifts([])
    setLastRun(null)
    setGitNote(null)
    setView('editor')
  }, [])

  const switchVault = useCallback(
    async (path: string) => {
      const updated = await window.api.vaults.select(path)
      setList(updated)
      forget()
      await refresh(updated.current)
    },
    [refresh, forget]
  )

  const adopt = useCallback(
    async (result: OpenResult) => {
      if (!result.ok) {
        if (result.reason !== 'cancelled') setStatus(result.reason)
        return
      }
      setList(result.list)
      forget()
      await refresh(result.list.current)
    },
    [refresh, forget]
  )

  const openVault = useCallback(async () => adopt(await window.api.vaults.open()), [adopt])
  const createVault = useCallback(async () => adopt(await window.api.vaults.create()), [adopt])

  // Forgetting and pinning only ever change the list, never what is open — main
  // refuses to leave the current vault dangling, so the answer it returns is the
  // whole of the new state.
  const forgetVault = useCallback(async (path: string) => {
    setList(await window.api.vaults.forget(path))
  }, [])
  const pinVault = useCallback(async (path: string, pinned: boolean) => {
    setList(await window.api.vaults.pin(path, pinned))
  }, [])

  // ── opening files ──────────────────────────────────────────────────────────

  const located = useMemo(
    () => (vault && openPath ? locate(vault, openPath, reports.map((r) => r.id)) : null),
    [vault, openPath, reports]
  )
  const reportId = located?.id ?? null

  /**
   * Which surface the open file belongs on.
   *
   * Three, not one. A `.csv` opens as a grid because a spreadsheet spells a
   * missing value and a measured zero the same way and an editor should not; a
   * `.mmd` opens beside a preview of *the build's own input*, since the engine
   * injects brand `classDef`s before mermaid sees the file and a preview of the
   * raw text would show colours the PDF does not use. Everything else is text.
   */
  const isCsv = openPath !== null && isCsvPath(openPath)
  const isMermaid = openPath !== null && openPath.endsWith('.mmd')
  /** What is unsaved right now, whichever surface is holding the buffer. */
  const unsaved = isCsv ? csvDirty : dirty

  const openFile = useCallback(
    async (node: Node) => {
      if (!vault) return
      const contents = await window.api.files.read(vault, node.path)
      setOpenPath(node.path)
      setText(contents)
      setDirty(false)
      setStatus(node.rel)
      setView('editor')
    },
    [vault]
  )

  const openReport = useCallback(
    async (id: string) => {
      if (!vault) return
      const path = `${vault}/reports/${id}/main.typ`
      try {
        const contents = await window.api.files.read(vault, path)
        setOpenPath(path)
        setText(contents)
        setDirty(false)
        setStatus(`reports/${id}/main.typ`)
        setView('editor')
        // Which report you were last in is a preference, not vault content — it
        // belongs beside the window layout, not in the folder anyone can clone.
        updateSettings({ vaults: { lastTarget: { [vault]: id } } })
      } catch (err) {
        setStatus(describeError(err))
      }
    },
    [vault, updateSettings]
  )

  /**
   * Open the file a row points at and put the cursor on its line. `path` is
   * vault-relative POSIX, which is what `Finding.path`, the sources panel and the
   * search results all speak.
   */
  const reveal = useCallback(
    async (path: string, line: number) => {
      if (!vault) return
      const absolute = `${vault}/${path}`
      setView('editor')
      if (absolute === openPath) {
        // Same file: imperative, so clicking the same row twice scrolls again.
        editor.current?.gotoLine(line)
        return
      }
      try {
        const contents = await window.api.files.read(vault, absolute)
        setOpenPath(absolute)
        setText(contents)
        setDirty(false)
        setStatus(`${path}:${line}`)
        // Declarative, because the jump has to wait for the new view to exist.
        setAtLine(line)
      } catch (err) {
        setStatus(describeError(err))
      }
    },
    [vault, openPath]
  )

  /**
   * The archived HTML behind a source, opened in whatever the system opens HTML
   * with. That is the point of keeping the bytes: the answer to "does the page
   * still say that" is the page, and selecting the file in Finder is one click
   * short of it.
   *
   * `files.open` resolves to the OS's own message rather than rejecting, because
   * "no application is registered for .html" is a fact worth printing, not an
   * exception worth swallowing.
   */
  const openSnapshot = useCallback(
    (report: string, key: string) => {
      if (!vault) return
      void window.api.files
        .open(vault, `${vault}/${snapshotPath(report, key)}`)
        .then((problem) => problem && setStatus(problem))
        .catch((err) => setStatus(describeError(err)))
    },
    [vault]
  )

  // Resume where the writer left off, once per vault. Without a memory of this
  // vault we land on the dashboard instead of an empty editor, which is what B6
  // asks for and what a fresh vault deserves.
  const resumed = useRef<string | null>(null)
  useEffect(() => {
    if (!vault || !settingsLoaded || openPath !== null || reports.length === 0) return
    if (resumed.current === vault) return
    resumed.current = vault
    const last = settings.vaults.lastTarget[vault]
    if (last && reports.some((row) => row.id === last)) void openReport(last)
  }, [vault, settingsLoaded, openPath, reports, settings.vaults.lastTarget, openReport])

  // ── acting ─────────────────────────────────────────────────────────────────

  const save = useCallback(async () => {
    // Whichever surface owns the buffer writes it. The grid edits the cell's
    // byte range in place; routing it through this whole-file write would
    // reformat the file and move a checksum nobody asked to move.
    if (isCsv) {
      const grid = csvEditor.current
      if (!grid) return
      await grid.save()
      // The same beat the text path marks: what depends on a saved file — the
      // check, the git poll, build-on-save — depends on it here too.
      setSavedAt(Date.now())
      return
    }
    if (!vault || !openPath || !dirty) return
    await window.api.files.write(vault, openPath, text)
    setDirty(false)
    setStatus(`saved ${relative(vault, openPath)}`)
    setSavedAt(Date.now())
  }, [vault, openPath, text, dirty, isCsv])

  const engine = useCallback(
    async (args: string[], done: string) => {
      if (!vault) return
      setBusy(true)
      setStatus(`${args.join(' ')}…`)
      const result = await window.api.engine.run(vault, args)
      setBusy(false)
      setLastRun(result)
      setStatus(result.code === 0 ? `${done} · ${tail(result)}` : `failed (exit ${result.code}) · ${tail(result)}`)
      // A failure is worth reading in full, and the Build tab is where the whole
      // of it lives now — the status bar only ever holds one line.
      if (result.code !== 0) {
        setProblemsTab('build')
        setPanes((current) => ({ ...current, problems: true }))
      }
      setRevision((n) => n + 1)
      await refresh(vault)
    },
    [vault, refresh]
  )

  const build = useCallback(async () => {
    if (!vault) return
    await save()
    // Whatever report the open file belongs to, or the whole vault when the file
    // is a design or a brand pack and could have changed any of them.
    await engine(['all', ...(reportId ? [reportId] : []), '--warn-only'], 'built')
    setBuiltAt(Date.now())
  }, [vault, save, engine, reportId])

  /**
   * The citation rule, as the engine sees the file **on disk**.
   *
   * `--score` is asked for once; an engine that does not have it answers with
   * argparse's usage, so the app remembers and stops asking rather than paying
   * for a failed spawn on every keystroke.
   */
  const wantsScore = useRef(true)
  const runCheck = useCallback(async () => {
    if (!vault) return
    if (wantsScore.current) {
      const scored = await window.api.engine.run(vault, ['check', '--json', '--score'])
      const parsed = parseJson<CheckResult>(scored.stdout)
      if (parsed) {
        setCheck(parsed)
        return
      }
      wantsScore.current = false
    }
    const plain = await window.api.engine.run(vault, ['check', '--json'])
    setCheck(parseJson<CheckResult>(plain.stdout))
  }, [vault])

  const verify = useCallback(async () => {
    if (!vault) return
    setBusy(true)
    const result = await window.api.engine.run(vault, [
      'verify',
      ...(reportId ? [reportId] : []),
      '--json'
    ])
    setBusy(false)
    setLastRun(result)
    // `verify --json` prints an envelope; a bare array is accepted too so the
    // panel keeps working if the engine ever flattens it.
    const parsed = parseJson<Drift[] | { drifts?: Drift[] }>(result.stdout)
    setDrifts(Array.isArray(parsed) ? parsed : (parsed?.drifts ?? []))
    setProblemsTab('evidence')
    setPanes((current) => ({ ...current, problems: true }))
    setStatus(result.code === 0 ? 'verified' : `verified with drift · ${tail(result)}`)
  }, [vault, reportId])

  const sync = useCallback(
    async (push: boolean) => {
      if (!vault) return
      setSyncing(true)
      const result = await window.api.git.sync(vault, push)
      setSyncing(false)
      setLastRun(result)
      setGitNote({ text: tail(result), failed: result.code !== 0 })
      try {
        setGit(await window.api.git.state(vault))
      } catch {
        setGit(null)
      }
    },
    [vault]
  )

  // ── the answers, kept current ──────────────────────────────────────────────

  const findings: Finding[] = useMemo(() => check?.findings ?? [], [check])

  const score: ReportScore | null = useMemo(() => {
    const rows = check?.score?.reports
    if (!Array.isArray(rows) || !reportId) return null
    return rows.find((row) => row.id === reportId) ?? null
  }, [check, reportId])

  const lineClasses: LineClass[] = useMemo(() => score?.lines ?? [], [score])

  // Re-ask after the writer stops typing, and after anything that touched disk.
  // Always against the saved file — the rail is dimmed while the buffer differs
  // rather than pretending to know what the unsaved text scores.
  useEffect(() => {
    if (!vault) return
    const timer = setTimeout(() => void runCheck(), Math.max(150, settings.build.checkOnIdleMs))
    return () => clearTimeout(timer)
  }, [vault, text, revision, savedAt, settings.build.checkOnIdleMs, runCheck])

  // Git state on a slow timer, and immediately after anything that could dirty
  // the tree. Polling is the honest option: the engine has no watch for it.
  useEffect(() => {
    if (!vault) {
      setGit(null)
      return
    }
    let live = true
    const poll = async (): Promise<void> => {
      try {
        const state = await window.api.git.state(vault)
        if (live) setGit(state)
      } catch {
        // Not a repo, or an engine without `sync` — the chip says "no git".
        if (live) setGit(null)
      }
    }
    void poll()
    const timer = setInterval(() => void poll(), 20_000)
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [vault, savedAt, builtAt, revision])

  // The page count, for the cursor→page estimate. Read from the engine's own
  // page index rather than counted here.
  useEffect(() => {
    setPageCount(null)
    if (!vault || !reportId) return
    let live = true
    const index = `${vault}/out/pages/${reportId}/pages.json`
    void (async () => {
      if (!(await window.api.files.exists(vault, index))) return
      const parsed = parseJson<{ count?: number; pages?: string[] }>(
        await window.api.files.read(vault, index)
      )
      if (live && parsed) setPageCount(parsed.count ?? parsed.pages?.length ?? null)
    })().catch(() => undefined)
    return () => {
      live = false
    }
  }, [vault, reportId, revision])

  /**
   * Cursor → page, by proportion.
   *
   * Typst emits no source map, so this is *not* SyncTeX: it is the fraction of
   * the file the cursor sits at, times the number of pages. It is right often
   * enough to be useful on a linear report and wrong on one with a big figure,
   * which is why it is printed with a `~` and never used to move anything by
   * itself.
   */
  const cursor: Cursor | null = useMemo(() => {
    if (!openPath) return null
    const lines = Math.max(1, text.split('\n').length)
    const pages = pageCount
    if (!pages || pages < 1) return { line: cursorLine, page: null, pages: null }
    const page = Math.min(pages, Math.max(1, Math.ceil((cursorLine / lines) * pages)))
    return { line: cursorLine, page, pages }
  }, [openPath, text, cursorLine, pageCount])

  // ── autosave, build-on-save, auto-commit ───────────────────────────────────

  /**
   * The preferences and the actions, read at the moment something happens rather
   * than depended on.
   *
   * That is deliberate and it is the difference between an automation and a
   * surprise: keyed on the setting, turning "commit after every save" on would
   * immediately commit a save made ten minutes ago. Keyed on the save, with the
   * setting read when the save lands, turning it on affects the next save and
   * nothing that already happened.
   */
  const prefs = useRef(settings)
  prefs.current = settings
  const actionsRef = useRef({ save, build, sync })
  actionsRef.current = { save, build, sync }

  // Keyed on `dirty` rather than on `unsaved`: a CSV is saved when somebody
  // decides to save it. Its bytes carry a registered sha256, so writing them on
  // a timer would move a checksum — and fire E011 — as a side effect of having
  // clicked into a cell, which is the class of surprise this whole data layer
  // exists to prevent. The grid has its own ⌘S and its own unsaved marker.
  useEffect(() => {
    const ms = settings.build.autoSaveMs
    if (ms === null || !dirty) return
    const timer = setTimeout(() => void actionsRef.current.save(), Math.max(200, ms))
    return () => clearTimeout(timer)
  }, [text, dirty, settings.build.autoSaveMs])

  useEffect(() => {
    if (savedAt === 0 || !prefs.current.build.buildOnSave) return
    void actionsRef.current.build()
  }, [savedAt])

  /**
   * The auto-commit loop.
   *
   * It runs only when `git.autoCommit` is on — checked before the timer is even
   * armed, and checked again when it fires. `git.autoPush` decides whether the
   * same run pushes; the engine refuses a push with no upstream, from a detached
   * HEAD, or when the branch is behind, and whatever it says lands in the status
   * bar verbatim. Nothing is swallowed: a failed sync colours the note, and the
   * whole output is one click away in the Build tab.
   */
  useEffect(() => {
    const at = Math.max(savedAt, builtAt)
    if (at === 0 || !prefs.current.git.autoCommit) return
    const timer = setTimeout(() => {
      const git = prefs.current.git
      if (!git.autoCommit) return
      void actionsRef.current.sync(git.autoPush)
    }, Math.max(0, prefs.current.git.debounceMs))
    return () => clearTimeout(timer)
  }, [savedAt, builtAt])

  // ── watch ──────────────────────────────────────────────────────────────────

  useEffect(() => {
    return window.api.watch.on((event) => {
      if (event.kind === 'start') {
        setWatching(true)
        setStatus('watching for changes…')
        return
      }
      if (event.kind === 'exit') {
        setWatching(false)
        setStatus(event.text ?? `watch stopped (exit ${event.code ?? 0})`)
        return
      }
      const line = (event.text ?? '')
        .split('\n')
        .map((part) => part.trim())
        .filter(Boolean)
        .pop()
      if (!line) return
      setStatus(line)
      // `typst watch` says so itself when a rebuild lands; that sentence is the
      // only signal there is, and reloading on it beats polling the PDF's mtime.
      if (/compiled successfully/i.test(line)) setRevision((n) => n + 1)
    })
  }, [])

  // One watcher, on the open report, for as long as the setting says so. A
  // different report means a different watcher, which is why the effect is keyed
  // on the id and stops the old one on the way out.
  useEffect(() => {
    if (!vault || !settings.build.watch || !reportId) return
    void window.api.watch.start(vault, reportId).catch((err) => setStatus(describeError(err)))
    return () => {
      void window.api.watch.stop()
    }
  }, [vault, reportId, settings.build.watch])

  // ── sources ────────────────────────────────────────────────────────────────

  const { sources, loading: sourcesLoading, error: sourcesError, reload: reloadSources } =
    useSources(vault, reportId, revision)

  // ── the pad ────────────────────────────────────────────────────────────────
  //
  // Owned here rather than inside NotesPanel: the panel is a sidebar tab and does
  // not exist while another tab is selected, so a hook living in it could not
  // feed the count in the header. One subprocess answers both.
  const pad = useNotes(vault, reportId, revision)

  // The editor's extensions are built once per document; a ref lets them read
  // fresh rows without the view being rebuilt under the cursor.
  const sourcesRef = useRef<SourceRow[]>([])
  sourcesRef.current = sources
  const snapshotRef = useRef(openSnapshot)
  snapshotRef.current = openSnapshot
  const reportRef = useRef<string | null>(reportId)
  reportRef.current = reportId

  const citationExtras = useMemo(
    () => [
      citationCompletionExtension(() => sourcesRef.current),
      citationHover(
        () => sourcesRef.current,
        (key) => {
          const report = reportRef.current
          if (report) snapshotRef.current(report, key)
        }
      )
    ],
    []
  )
  // Citations are a Typst thing; sources.yml gets the plain editor.
  const editorExtras = openPath?.endsWith('.typ') ? citationExtras : undefined

  // A URL dropped anywhere on the window is a source for the open report.
  const dragging = useUrlDrop(setAddUrl, Boolean(vault && reportId))

  // ── commands and keys ──────────────────────────────────────────────────────

  const togglePane = useCallback(
    (pane: Pane) => setPanes((current) => ({ ...current, [pane]: !current[pane] })),
    []
  )

  /** Every sidebar tab is reached the same way — show the sidebar, then select —
   *  so a shortcut on a hidden sidebar opens it rather than doing nothing. */
  const openSideTab = useCallback((tab: SideTab) => {
    setPanes((current) => ({ ...current, sidebar: true }))
    setSideTab(tab)
  }, [])

  /** Raise the new-report dialog, optionally on a chosen design. */
  const newReport = useCallback((template?: string) => {
    setNewTemplate(template)
    setNewOpen(true)
  }, [])

  const actions = useMemo<CommandActions>(
    () => ({
      save,
      build,
      check: () => {
        setProblemsTab('findings')
        setPanes((current) => ({ ...current, problems: true }))
        return runCheck()
      },
      score: () => engine(['score', ...(reportId ? [reportId] : [])], 'scored'),
      verify,
      html: () => engine(['html', ...(reportId ? [reportId] : [])], 'exported'),
      sync,
      newReport: () => newReport(),
      addSource: () => setAddUrl(''),
      openVault,
      createVault,
      selectVault: switchVault,
      openSettings: () => setSettingsOpen(true),
      openBrandStudio: () => setView('brand'),
      openDashboard: () => setView('dashboard'),
      openTimeline: () => setView('timeline'),
      openDesigns: () => setView('designs'),
      openSideTab,
      togglePane,
      toggleWatch: () => updateSettings({ build: { watch: !settings.build.watch } }),
      engine
    }),
    [
      save,
      build,
      runCheck,
      verify,
      sync,
      engine,
      reportId,
      newReport,
      openVault,
      createVault,
      switchVault,
      openSideTab,
      togglePane,
      updateSettings,
      settings.build.watch
    ]
  )

  const commands = useMemo(
    () =>
      buildCommands({
        vault,
        reportId,
        openPath,
        vaults: list.entries,
        api: window.api,
        actions
      }),
    [vault, reportId, openPath, list.entries, actions]
  )

  // One keymap, driven by the same table the palette prints — a shortcut shown
  // beside a command that does not fire is worse than no shortcut at all. The
  // palette owns ⌘K / ⌘⇧P, and Settings owns ⌘, . The tab and studio shortcuts
  // used to live here; they are rows now, which is what lets the palette show
  // them. ⌘3 stays because it is a second key for a row that already has one.
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      const hit = commandForEvent(event, commands)
      if (hit) {
        event.preventDefault()
        void hit.run()
        return
      }
      if (!(event.metaKey || event.ctrlKey)) return
      // ⌘3 without shift: ⌘⇧3 is macOS's own screenshot.
      if (event.key === '3' && !event.shiftKey) {
        event.preventDefault()
        togglePane('problems')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [commands, togglePane])

  /**
   * The application menu.
   *
   * Main dispatches and the renderer acts, because the state these items change
   * lives here. That way File ▸ New Report and ⌘N are one code path rather than
   * two implementations of the same intent — which is the same reason the
   * shortcuts come out of `commands.ts`.
   */
  useEffect(
    () =>
      window.api.menu.on((command) => {
        switch (command.kind) {
          case 'select-vault':
            void switchVault(command.path)
            break
          case 'open-vault':
            void actions.openVault()
            break
          case 'create-vault':
            void actions.createVault()
            break
          case 'new-report':
            void actions.newReport()
            break
          case 'save':
            void actions.save()
            break
          case 'build':
            void actions.build()
            break
        }
      }),
    [actions, switchVault]
  )

  // ── layout ─────────────────────────────────────────────────────────────────

  if (!vault) {
    return (
      <>
        <Welcome
          onOpen={openVault}
          onCreate={createVault}
          entries={list.entries}
          onSelect={(path) => void switchVault(path)}
          onForget={(path) => void forgetVault(path)}
          onPin={(path, pinned) => void pinVault(path, pinned)}
          demo={demoVault}
          onDemo={demoVault ? () => void switchVault(demoVault) : undefined}
          engine={engineAt}
          version={engineVersion}
          error={status || undefined}
        />
        {/* Preferences exist before a vault does — the theme is the app's, not
            the vault's. */}
        <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} vault={null} />
      </>
    )
  }

  // The dashboard is also the empty state: a vault with nothing open should show
  // what it holds, not an empty editor.
  const route: View = view === 'editor' && openPath === null ? 'dashboard' : view
  const stale = reports.find((row) => row.id === reportId)?.stale

  const sidebar = (
    <aside className="flex h-full flex-col border-r border-border">
      <Tabs
        value={sideTab}
        onValueChange={(value) => setSideTab(value as SideTab)}
        className="flex min-h-0 flex-1 flex-col gap-0"
      >
        {/* A container query rather than a viewport one: four tabs and a count
            do not fit a narrow sidebar, and how narrow the sidebar is has
            nothing to do with how wide the window is. */}
        <div className="@container flex h-8 shrink-0 items-center px-2">
          <TabsList className="h-6 shrink-0 gap-0.5 p-0.5">
            <TabsTrigger value="files" className="h-5 gap-1 px-1.5 text-[11px]" title="Files ⌘1">
              <FileText className="size-3" />
              Files
            </TabsTrigger>
            <TabsTrigger
              value="sources"
              className="h-5 gap-1 px-1.5 text-[11px]"
              title="Sources ⌘⇧E"
            >
              <Link2 className="size-3" />
              Sources
            </TabsTrigger>
            <TabsTrigger value="search" className="h-5 gap-1 px-1.5 text-[11px]" title="Search ⌘⇧F">
              <Search className="size-3" />
              Find
            </TabsTrigger>
            <TabsTrigger value="notes" className="h-5 gap-1 px-1.5 text-[11px]" title="Notes ⌘⇧T">
              <ListTodo className="size-3" />
              Notes
            </TabsTrigger>
          </TabsList>
          <span className="@max-[19rem]:hidden ml-auto pr-1 text-[10px] whitespace-nowrap text-muted-foreground">
            {reports.length} reports
          </span>
        </div>
        <Separator />
        <div className="min-h-0 flex-1">
          {sideTab === 'files' && (
            <FileTree nodes={nodes} openPath={openPath} dirty={dirty} onOpen={openFile} />
          )}
          {sideTab === 'sources' && (
            <SourcesPanel
              vault={vault}
              reportId={reportId}
              sources={sources}
              loading={sourcesLoading}
              error={sourcesError}
              onReload={() => {
                reloadSources()
                void refresh(vault)
              }}
              onReveal={reveal}
              onOpenSnapshot={(key) => reportId && openSnapshot(reportId, key)}
              className="h-full"
            />
          )}
          {sideTab === 'search' && (
            <SearchPanel
              vault={vault}
              onReveal={reveal}
              onOpenSnapshot={openSnapshot}
              className="h-full"
            />
          )}
          {sideTab === 'notes' && (
            <NotesPanel
              vault={vault}
              reportId={reportId}
              pad={pad}
              onReveal={reveal}
              className="h-full"
            />
          )}
        </div>
      </Tabs>
    </aside>
  )

  const centre = (
    <main className="flex h-full min-w-0 flex-col">
      <div className="flex h-8 shrink-0 items-center gap-2 px-3 text-[11px] text-muted-foreground">
        <span className="truncate font-mono">
          {openPath ? relative(vault, openPath) : 'no file open'}
        </span>
        {unsaved && <span className="text-[10px]">● unsaved</span>}
      </div>
      <Separator />
      {/* `data-editor` says which of the three surfaces is mounted, for the same
          reason `data-surface` says which route is: it is what a smoke run can
          ask the live DOM without probing for a placeholder a redesign is free
          to change. */}
      <div className="min-h-0 flex-1" data-editor={isCsv ? 'csv' : isMermaid ? 'mermaid' : 'text'}>
        {isCsv && openPath ? (
          <CsvEditor
            ref={csvEditor}
            vault={vault}
            path={openPath}
            reportId={reportId}
            onDirtyChange={setCsvDirty}
            onChanged={() => {
              // A revision rewrites sources.yml and moves a checksum, so the
              // bibliography, the findings and the tree are all stale at once.
              setRevision((n) => n + 1)
              void runCheck()
            }}
            className="h-full"
          />
        ) : isMermaid ? (
          <MermaidEditor
            vault={vault}
            path={openPath}
            reportId={reportId}
            text={text}
            settings={settings}
            dirty={dirty}
            findings={findings}
            lineClasses={lineClasses}
            handleRef={editor}
            onChange={(next) => {
              setText(next)
              setDirty(true)
            }}
            onSave={save}
            onBuild={build}
            onCreated={async (created) => {
              const contents = await window.api.files.read(vault, created)
              setOpenPath(created)
              setText(contents)
              setDirty(false)
              setStatus(relative(vault, created))
              setRevision((n) => n + 1)
            }}
            className="h-full"
          />
        ) : (
          <Editor
            ref={editor}
            path={openPath}
            rel={openPath ? relative(vault, openPath) : null}
            text={text}
            settings={settings}
            findings={findings}
            lineClasses={lineClasses}
            stale={dirty}
            atLine={atLine}
            extra={editorExtras}
            onChange={(next) => {
              setText(next)
              setDirty(true)
            }}
            onSave={save}
            onBuild={build}
            onCursorLine={setCursorLine}
            onFindingSelect={(finding) => {
              setProblemsTab('findings')
              setPanes((current) => ({ ...current, problems: true }))
              setProblemsFocus({ path: finding.path, line: finding.line })
            }}
          />
        )}
      </div>
    </main>
  )

  const viewer = (
    <section className="flex h-full flex-col">
      <div className="flex h-8 shrink-0 items-center justify-between px-3 text-[11px] text-muted-foreground">
        <span className="truncate font-mono">{located ? located.pdf : 'no report'}</span>
        {located && (
          <button
            className="text-[11px] hover:text-foreground"
            onClick={() => window.api.files.reveal(vault, `${vault}/${located.pdf}`)}
          >
            reveal
          </button>
        )}
      </div>
      <Separator />
      <div className="min-h-0 flex-1">
        {/* The same estimate the status bar prints, used to open the viewer near
            where the cursor is. It is proportion rather than a source map, so it
            positions and never claims more than that. */}
        <Viewer
          vault={vault}
          pdf={located?.pdf ?? null}
          revision={revision}
          building={busy}
          page={cursor?.page ?? null}
        />
      </div>
    </section>
  )

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-3 pl-20">
        <VaultSwitcher
          list={list}
          onSelect={switchVault}
          onOpen={openVault}
          onCreate={createVault}
        />
        <Separator orientation="vertical" className="mx-1 h-5" />
        <Button
          variant="secondary"
          size="sm"
          className="h-7 gap-1.5 text-xs"
          disabled={busy}
          onClick={build}
        >
          <Hammer className="size-3.5" />
          Build
          <kbd className="ml-1 text-[10px] text-muted-foreground">⌘B</kbd>
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 gap-1.5 text-xs"
          disabled={busy}
          onClick={() => void actions.check()}
        >
          <ShieldCheck className="size-3.5" />
          Check
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 gap-1.5 text-xs"
          disabled={!unsaved}
          onClick={save}
        >
          <Save className="size-3.5" />
          Save
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          title="Reload the tree"
          disabled={busy}
          onClick={() => refresh(vault)}
        >
          <RefreshCw className="size-3.5" />
        </Button>

        <div className="ml-auto flex items-center gap-1">
          {/* The pad's count used to sit here, for want of somewhere better; it
              is in the bottom rail now, beside the problems chip, which is where
              a count of things still waiting belongs. */}
          {located && (
            <Badge variant="outline" className="mr-1 font-mono text-[10px] font-normal">
              {located.id}
            </Badge>
          )}
          <RouteButton
            active={route === 'dashboard'}
            title="Vault dashboard ⌘⇧D"
            onClick={() => setView(route === 'dashboard' && openPath ? 'editor' : 'dashboard')}
          >
            <LayoutGrid className="size-3.5" />
          </RouteButton>
          <RouteButton
            active={route === 'designs'}
            title="Designs"
            onClick={() => setView(route === 'designs' ? 'editor' : 'designs')}
          >
            <LayoutTemplate className="size-3.5" />
          </RouteButton>
          <RouteButton
            active={route === 'brand'}
            title="Brand studio ⌘⇧B"
            onClick={() => setView(route === 'brand' ? 'editor' : 'brand')}
          >
            <PaletteIcon className="size-3.5" />
          </RouteButton>
          <RouteButton
            active={route === 'timeline'}
            title="Version timeline"
            onClick={() => setView(route === 'timeline' ? 'editor' : 'timeline')}
          >
            <History className="size-3.5" />
          </RouteButton>
          <RouteButton active={false} title="Settings ⌘," onClick={() => setSettingsOpen(true)}>
            <SlidersHorizontal className="size-3.5" />
          </RouteButton>
        </div>
      </header>

      {route === 'editor' ? (
        <ResizablePanelGroup
          direction="horizontal"
          className="min-h-0 flex-1"
          data-surface="editor"
          // Keyed on whether the preferences have arrived: `defaultLayout` is
          // read once, at mount, and a group mounted a tick before the IPC read
          // returned would keep the default sizes for the session.
          key={settingsLoaded ? 'panes' : 'panes-loading'}
          defaultLayout={
            Object.keys(settings.layout.panes).length > 0 ? settings.layout.panes : undefined
          }
          onLayoutChanged={(layout, meta) => {
            if (!meta.isUserInteraction) return
            updateSettings({ layout: { panes: layout } })
          }}
        >
          {panes.sidebar && (
            <>
              <ResizablePanel id="sidebar" defaultSize={19} minSize={12} maxSize={40}>
                {sidebar}
              </ResizablePanel>
              <ResizableHandle />
            </>
          )}
          <ResizablePanel id="editor" defaultSize={45} minSize={22}>
            {centre}
          </ResizablePanel>
          {panes.viewer && (
            <>
              <ResizableHandle withHandle />
              <ResizablePanel id="viewer" defaultSize={36} minSize={18}>
                {viewer}
              </ResizablePanel>
            </>
          )}
        </ResizablePanelGroup>
      ) : (
        // `data-surface` is what scripts/smoke.mjs asks the DOM to confirm a
        // screen actually appeared — cheaper and less brittle than probing for a
        // placeholder that a redesign is free to change.
        <div className="min-h-0 flex-1" data-surface={route}>
          {route === 'dashboard' && (
            <Dashboard
              vault={vault}
              reports={reports}
              revision={revision}
              onOpen={openReport}
              onNew={() => newReport()}
              className="h-full"
            />
          )}
          {route === 'designs' && (
            <Designs
              vault={vault}
              revision={revision}
              // The card that was clicked names the design, and the dialog opens
              // on it. Dropping the id here is what used to send somebody who had
              // just picked a design to a form that said `base`.
              onNewReport={(templateId) => newReport(templateId)}
              onChanged={() => void refresh(vault)}
              className="h-full"
            />
          )}
          {route === 'brand' && (
            <BrandStudio
              vault={vault}
              onClose={() => {
                setView('editor')
                // A brand change restyles every report, so the PDF and the tree
                // are both stale the moment the studio closes.
                setRevision((n) => n + 1)
                void refresh(vault)
              }}
              className="h-full"
            />
          )}
          {route === 'timeline' && (
            <Timeline
              vault={vault}
              reportId={reportId}
              revision={revision}
              stale={stale}
              onReveal={reveal}
              onChanged={() => void refresh(vault)}
              className="h-full"
            />
          )}
        </div>
      )}

      <Problems
        findings={findings}
        run={lastRun}
        drifts={drifts}
        open={panes.problems}
        onOpenChange={(open) => setPanes((current) => ({ ...current, problems: open }))}
        onReveal={reveal}
        onVerify={verify}
        busy={busy}
        tab={problemsTab}
        onTabChange={setProblemsTab}
        focus={problemsFocus}
      />

      <StatusBar
        engine={engineAt}
        status={status}
        busy={busy}
        git={git}
        gitNote={gitNote}
        syncing={syncing}
        onGit={() => setView('timeline')}
        onGitNote={() => {
          setProblemsTab('build')
          setPanes((current) => ({ ...current, problems: true }))
        }}
        findings={findings}
        problemsOpen={panes.problems}
        onToggleProblems={() => togglePane('problems')}
        todos={pad.todos}
        notesOpen={panes.sidebar && sideTab === 'notes'}
        onTodos={() => openSideTab('notes')}
        score={score}
        scoreStale={dirty}
        onScore={() => setView('dashboard')}
        cursor={cursor}
        watching={watching}
        onWatch={() => void actions.toggleWatch()}
        onEngine={() => setSettingsOpen(true)}
      />

      <Palette nodes={nodes} commands={commands} onOpen={openFile} />
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} vault={vault} />
      <NewReport
        vault={vault}
        open={newOpen}
        onOpenChange={setNewOpen}
        reports={reports}
        defaultTemplate={newTemplate}
        revision={revision}
        onCreated={(id) => void openReport(id)}
      />
      {reportId && (
        <AddSourceDialog
          vault={vault}
          reportId={reportId}
          open={addUrl !== null}
          onOpenChange={(open) => {
            if (!open) setAddUrl(null)
          }}
          defaultUrl={addUrl ?? ''}
          onAdded={() => {
            reloadSources()
            void refresh(vault)
          }}
        />
      )}
      {dragging && <div className="pointer-events-none fixed inset-0 z-50 ring-2 ring-ring ring-inset" />}
    </div>
  )
}

function RouteButton({
  active,
  title,
  onClick,
  children
}: {
  active: boolean
  title: string
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <Button
      variant={active ? 'secondary' : 'ghost'}
      size="icon"
      className="size-7"
      title={title}
      aria-pressed={active}
      onClick={onClick}
    >
      {children}
    </Button>
  )
}
