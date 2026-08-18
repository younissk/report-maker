/**
 * Everything the app can do, as one table.
 *
 * The palette renders this list and nothing else decides what the app offers, so
 * teaching the app a new CLI verb is one row here rather than a button, a
 * handler, a shortcut and a menu entry in four files. That is the only reason
 * this module exists.
 *
 * No command knows anything about a vault. A row either calls a callback the
 * shell supplied or hands an argv to the engine — the same rule that keeps the
 * rest of the renderer honest applies hardest here, because a table of verbs is
 * exactly where vault logic would like to accumulate.
 */

import {
  Download,
  Eye,
  FileCode2,
  FileJson,
  FilePlus2,
  FileText,
  FolderOpen,
  FolderPlus,
  FolderSearch,
  FolderX,
  Gauge,
  GitCommitHorizontal,
  Hammer,
  History,
  Images,
  Layers,
  LayoutGrid,
  LayoutTemplate,
  Link2,
  ListTodo,
  Palette,
  PanelLeft,
  PanelRight,
  RefreshCw,
  Save,
  Search,
  Settings,
  ShieldCheck,
  Stethoscope,
  Trash2,
  TriangleAlert,
  Upload,
  Workflow,
  type LucideIcon
} from 'lucide-react'
import type { Api } from '../../../shared/api'
import type { VaultEntry } from '../../../shared/types'

/** The three panes the shell can show or hide. */
export type Pane = 'sidebar' | 'viewer' | 'problems'

/**
 * The sidebar's tabs. Declared here rather than in `App.tsx` because moving to
 * one is a command like any other, and the table has to be able to name them.
 */
export type SideTab = 'files' | 'sources' | 'search' | 'notes'

/**
 * How many remembered vaults the palette lists. The whole list is on the
 * Welcome screen and in the switcher; this is the shortcut, and a palette whose
 * first page is thirty folders is a palette you stop opening.
 */
const RECENT_LIMIT = 8

/** The headings the palette groups commands under, in the order it shows them. */
export type CommandGroup = 'File' | 'Build' | 'Evidence' | 'Git' | 'View' | 'Vault'

export const GROUPS: CommandGroup[] = ['File', 'Build', 'Evidence', 'Git', 'View', 'Vault']

/**
 * What the shell can do on a command's behalf.
 *
 * Each of these is a callback `App.tsx` supplies: the table knows what the app
 * can do, never how it does it. `engine` is the escape hatch that keeps this
 * file's promise — a CLI verb the shell has no special handling for is one more
 * row below, not another prop threaded through three components.
 */
export type CommandActions = {
  /** Write the open buffer to disk. */
  save(): void | Promise<void>
  /** Save, build the open report, reload the PDF. */
  build(): void | Promise<void>
  /** Run the citation rule and refresh the Problems panel. */
  check(): void | Promise<void>
  /** Recompute evidence density, which is also what the editor's rail draws. */
  score(): void | Promise<void>
  /** Re-fetch archived sources and report drift. */
  verify(): void | Promise<void>
  /** Export the open report as a single self-contained HTML file. */
  html(): void | Promise<void>
  /** Commit the vault, and push it when `push` is true. */
  sync(push: boolean): void | Promise<void>
  /** The new-report dialog. */
  newReport(): void | Promise<void>
  /** The add-source dialog: a URL in, `report-maker cite` out. */
  addSource(): void | Promise<void>
  openVault(): void | Promise<void>
  createVault(): void | Promise<void>
  /** Make a remembered vault current. The palette lists the recent ones. */
  selectVault(path: string): void | Promise<void>
  openSettings(): void | Promise<void>
  openBrandStudio(): void | Promise<void>
  openDashboard(): void | Promise<void>
  openTimeline(): void | Promise<void>
  /** The designs screen — what is installed, and where each came from. */
  openDesigns(): void | Promise<void>
  /** Show the sidebar and put it on one tab. */
  openSideTab(tab: SideTab): void
  togglePane(pane: Pane): void
  /** Start or stop the live `report-maker watch` run. */
  toggleWatch(): void | Promise<void>
  /**
   * Run the CLI and report the outcome in the status bar. `done` is the past
   * tense the status line uses on success — "staged", "cleaned".
   */
  engine(args: string[], done: string): void | Promise<void>
}

/** What a command is handed when it runs. */
export type CommandCtx = {
  vault: string | null
  /** The report the open file belongs to, as the engine names it. */
  reportId: string | null
  openPath: string | null
  /** The remembered vaults, in the order the main process ordered them —
   *  pinned first, then most recent. One row each, up to {@link RECENT_LIMIT}. */
  vaults?: VaultEntry[]
  api: Api
  actions: CommandActions
}

/**
 * What a command needs before it can run at all. Defaulting to `'vault'` states
 * the truth once instead of twenty-eight times: almost nothing here means
 * anything without a vault open.
 */
export type Requirement = 'nothing' | 'vault' | 'file' | 'report'

/** One row of the table. */
export type CommandSpec = {
  id: string
  title: string
  group: CommandGroup
  /** Secondary text — usually the CLI this row is a front end for. */
  hint?: string
  /** Portable form, `'Mod+Shift+P'`; rendered by {@link shortcutLabel}. */
  shortcut?: string
  /** Extra words the palette should match on, for people who call it something else. */
  keywords?: string[]
  icon?: LucideIcon
  /** @default 'vault' */
  needs?: Requirement
  run: (ctx: CommandCtx) => void | Promise<void>
}

/** A row resolved against the current context, which is what the palette shows. */
export type Command = Omit<CommandSpec, 'run'> & {
  disabled: boolean
  /** Why it is disabled, in the words the palette shows on the row. */
  reason?: string
  run: () => Promise<void>
}

// ── The table ────────────────────────────────────────────────────────────────

/** A row that is nothing but a CLI call. */
function cli(args: string[], done: string): (ctx: CommandCtx) => void | Promise<void> {
  return (ctx) => ctx.actions.engine(args, done)
}

const COMMANDS: CommandSpec[] = [
  // File
  {
    id: 'file.save',
    title: 'Save file',
    group: 'File',
    shortcut: 'Mod+S',
    icon: Save,
    needs: 'file',
    run: (ctx) => ctx.actions.save()
  },
  {
    id: 'report.new',
    title: 'New report…',
    group: 'File',
    hint: 'report-maker new',
    shortcut: 'Mod+N',
    icon: FilePlus2,
    keywords: ['create', 'scaffold'],
    run: (ctx) => ctx.actions.newReport()
  },
  {
    id: 'file.reveal',
    title: 'Reveal the open file in Finder',
    group: 'File',
    icon: FolderSearch,
    needs: 'file',
    run: (ctx) => {
      // Guarded rather than asserted: `needs` disables the row, it does not make
      // the paths non-null to the compiler, and a silent no-op beats a crash.
      if (!ctx.vault || !ctx.openPath) return
      return ctx.api.files.reveal(ctx.vault, ctx.openPath)
    }
  },

  // Build
  {
    id: 'build.report',
    title: 'Build the open report',
    group: 'Build',
    hint: 'report-maker all',
    shortcut: 'Mod+B',
    icon: Hammer,
    keywords: ['compile', 'pdf', 'typst'],
    run: (ctx) => ctx.actions.build()
  },
  {
    id: 'build.watch',
    title: 'Toggle live rebuild',
    group: 'Build',
    hint: 'report-maker watch',
    icon: Eye,
    needs: 'report',
    run: (ctx) => ctx.actions.toggleWatch()
  },
  {
    id: 'build.html',
    title: 'Export as HTML',
    group: 'Build',
    hint: 'report-maker html',
    icon: FileCode2,
    needs: 'report',
    keywords: ['share', 'web'],
    run: (ctx) => ctx.actions.html()
  },
  {
    id: 'build.stage',
    title: 'Stage the designs',
    group: 'Build',
    hint: 'report-maker stage',
    icon: Layers,
    keywords: ['template'],
    run: cli(['stage'], 'staged')
  },
  {
    id: 'build.diagrams',
    title: 'Render the diagrams',
    group: 'Build',
    hint: 'report-maker diagrams',
    icon: Workflow,
    keywords: ['mermaid', 'mmd'],
    run: cli(['diagrams'], 'rendered')
  },
  {
    id: 'build.pages',
    title: 'Render the page images',
    group: 'Build',
    hint: 'report-maker pages',
    icon: Images,
    keywords: ['png', 'thumbnail'],
    run: cli(['pages'], 'rendered')
  },
  {
    id: 'build.manifest',
    title: 'Write the manifest',
    group: 'Build',
    hint: 'report-maker manifest',
    icon: FileJson,
    run: cli(['manifest'], 'written')
  },
  {
    id: 'build.clean',
    title: 'Clean the generated output',
    group: 'Build',
    hint: 'removes .build/ and out/',
    icon: Trash2,
    run: cli(['clean'], 'cleaned')
  },

  // There is deliberately no `data revise` row here, and there should not be
  // one. Re-registering a data file moves the sha256 in `sources.yml` onto
  // whatever the bytes say now — it is the one action that can make a stale
  // number look current, and E011 exists to stop exactly that happening
  // quietly. So it must be a decision taken in front of the diff it causes: the
  // banner on the open CSV, which names the old and new checksums, the row and
  // column counts either side, and the dated copy it archives. A palette entry
  // that moved a checksum from a fuzzy search and a keystroke would be precisely
  // the affordance the feature was built to deny. If you are here to "finish"
  // the table, this row is not missing.



  // Evidence
  {
    id: 'check.run',
    title: 'Check the citation rule',
    group: 'Evidence',
    hint: 'report-maker check',
    shortcut: 'Mod+Shift+C',
    icon: ShieldCheck,
    keywords: ['lint', 'findings', 'problems'],
    run: (ctx) => ctx.actions.check()
  },
  {
    id: 'score.run',
    title: 'Measure evidence density',
    group: 'Evidence',
    hint: 'report-maker score',
    icon: Gauge,
    keywords: ['cited', 'assessed', 'unmarked'],
    run: (ctx) => ctx.actions.score()
  },
  {
    id: 'verify.run',
    title: 'Verify sources against the live web',
    group: 'Evidence',
    hint: 'report-maker verify',
    icon: RefreshCw,
    keywords: ['drift', 'snapshot', 'archive'],
    run: (ctx) => ctx.actions.verify()
  },
  {
    id: 'source.add',
    title: 'Add a source from a URL…',
    group: 'Evidence',
    hint: 'report-maker cite',
    icon: Link2,
    needs: 'report',
    keywords: ['cite', 'reference', 'bibliography'],
    run: (ctx) => ctx.actions.addSource()
  },
  {
    id: 'view.sources',
    title: 'Sources for the open report',
    group: 'Evidence',
    hint: 'report-maker sources',
    shortcut: 'Mod+Shift+E',
    icon: Link2,
    keywords: ['bibliography', 'keys', 'snapshots', 'panel'],
    run: (ctx) => ctx.actions.openSideTab('sources')
  },
  {
    id: 'view.search',
    title: 'Find in the vault',
    group: 'Evidence',
    hint: 'report-maker find — prose, sources, archived pages, diagrams',
    shortcut: 'Mod+Shift+F',
    icon: Search,
    keywords: ['search', 'grep', 'snapshot', 'query'],
    run: (ctx) => ctx.actions.openSideTab('search')
  },

  // Git
  {
    id: 'git.commit',
    title: 'Commit the vault',
    group: 'Git',
    hint: 'report-maker sync',
    icon: GitCommitHorizontal,
    run: (ctx) => ctx.actions.sync(false)
  },
  {
    id: 'git.push',
    title: 'Commit and push',
    group: 'Git',
    hint: 'report-maker sync --push',
    icon: Upload,
    run: (ctx) => ctx.actions.sync(true)
  },
  {
    id: 'git.timeline',
    title: 'Version timeline',
    group: 'Git',
    hint: 'what changed, revision by revision',
    icon: History,
    needs: 'report',
    keywords: ['history', 'log', 'diff'],
    run: (ctx) => ctx.actions.openTimeline()
  },

  // View
  {
    id: 'view.dashboard',
    title: 'Vault dashboard',
    group: 'View',
    shortcut: 'Mod+Shift+D',
    icon: LayoutGrid,
    keywords: ['overview', 'reports', 'home'],
    run: (ctx) => ctx.actions.openDashboard()
  },
  {
    id: 'view.notes',
    title: 'Notes and todos',
    group: 'View',
    hint: 'report-maker todos — the pad beside the report, never compiled',
    shortcut: 'Mod+Shift+T',
    icon: ListTodo,
    keywords: ['todo', 'task', 'checklist', 'scratch', 'pad'],
    run: (ctx) => ctx.actions.openSideTab('notes')
  },
  {
    id: 'view.designs',
    title: 'Designs',
    group: 'View',
    hint: 'what is installed, what it inherits, which reports use it',
    icon: LayoutTemplate,
    keywords: ['template', 'theme', 'layout', 'starter'],
    run: (ctx) => ctx.actions.openDesigns()
  },
  {
    id: 'design.install',
    title: 'Install a design from a URL…',
    group: 'View',
    hint: 'report-maker template install — the dialog is on the Designs screen',
    icon: Download,
    keywords: ['template install', 'git', 'download', 'fetch', 'house style'],
    // The Designs screen owns the two-step review this needs, and that review is
    // the point: installing a design puts somebody else's Typst in the vault to
    // run at build time. A palette row that skipped it would be the wrong help.
    run: (ctx) => ctx.actions.openDesigns()
  },
  {
    id: 'view.brand',
    title: 'Brand studio',
    group: 'View',
    hint: 'colours, fonts and spacing, with a live specimen',
    shortcut: 'Mod+Shift+B',
    icon: Palette,
    keywords: ['colour', 'color', 'theme', 'tokens', 'logo'],
    run: (ctx) => ctx.actions.openBrandStudio()
  },
  {
    id: 'view.settings',
    title: 'Settings',
    group: 'View',
    shortcut: 'Mod+,',
    icon: Settings,
    needs: 'nothing',
    keywords: ['preferences', 'options'],
    run: (ctx) => ctx.actions.openSettings()
  },
  {
    id: 'view.sidebar',
    title: 'Toggle the file tree',
    group: 'View',
    shortcut: 'Mod+1',
    icon: PanelLeft,
    run: (ctx) => ctx.actions.togglePane('sidebar')
  },
  {
    id: 'view.viewer',
    title: 'Toggle the PDF pane',
    group: 'View',
    shortcut: 'Mod+2',
    icon: PanelRight,
    run: (ctx) => ctx.actions.togglePane('viewer')
  },
  {
    id: 'view.problems',
    title: 'Toggle the problems panel',
    group: 'View',
    shortcut: 'Mod+Shift+M',
    icon: TriangleAlert,
    keywords: ['findings', 'errors', 'warnings'],
    run: (ctx) => ctx.actions.togglePane('problems')
  },

  // Vault
  {
    id: 'vault.open',
    title: 'Open a vault…',
    group: 'Vault',
    icon: FolderOpen,
    needs: 'nothing',
    run: (ctx) => ctx.actions.openVault()
  },
  {
    id: 'vault.create',
    title: 'Create a vault…',
    group: 'Vault',
    hint: 'report-maker init',
    icon: FolderPlus,
    needs: 'nothing',
    run: (ctx) => ctx.actions.createVault()
  },
  {
    id: 'vault.reveal',
    title: 'Reveal the vault in Finder',
    group: 'Vault',
    icon: FolderSearch,
    run: (ctx) => {
      if (!ctx.vault) return
      return ctx.api.files.reveal(ctx.vault, ctx.vault)
    }
  },
  {
    id: 'vault.templates',
    title: 'List the designs',
    group: 'Vault',
    hint: 'report-maker templates',
    icon: LayoutTemplate,
    keywords: ['template'],
    run: cli(['templates'], 'listed')
  },
  {
    id: 'vault.reports',
    title: 'List the reports',
    group: 'Vault',
    hint: 'report-maker list',
    icon: FileText,
    run: cli(['list'], 'listed')
  },
  {
    id: 'vault.doctor',
    title: 'Doctor — what is installed',
    group: 'Vault',
    hint: 'report-maker doctor',
    icon: Stethoscope,
    keywords: ['typst', 'mermaid', 'diagnose'],
    run: cli(['doctor'], 'checked')
  }
]

// ── Resolving against the current context ────────────────────────────────────

const REASONS: Record<Requirement, string> = {
  nothing: '',
  vault: 'no vault open',
  file: 'no file open',
  report: 'not inside a report'
}

/**
 * The table, bound to the app as it stands right now.
 *
 * A command that could not possibly run — anything needing a vault when none is
 * open — is left out rather than shown dead, because without a vault the palette
 * is a way to get one. A command that is merely unavailable *yet* stays visible
 * and disabled with the reason on the row: a palette you cannot browse is a
 * palette nobody learns.
 *
 * The remembered vaults are appended as rows rather than written into the table,
 * because they are the one thing here that is data: which folders exist is a
 * fact about this machine, and the table is a list of capabilities.
 */
export function buildCommands(ctx: CommandCtx): Command[] {
  const resolved: Command[] = []

  for (const spec of COMMANDS) {
    const needs = spec.needs ?? 'vault'
    if (needs !== 'nothing' && !ctx.vault) continue

    const unmet =
      (needs === 'file' && !ctx.openPath) || (needs === 'report' && !ctx.reportId)

    resolved.push({
      ...spec,
      needs,
      disabled: unmet,
      reason: unmet ? REASONS[needs] : undefined,
      run: async () => {
        await spec.run(ctx)
      }
    })
  }

  // Open Recent. The vault already open is left out — it is not somewhere to go
  // — and a missing one stays listed and disabled with the reason on the row,
  // the same way `vaults.ts` keeps it rather than quietly dropping it.
  for (const entry of (ctx.vaults ?? []).filter((e) => e.path !== ctx.vault).slice(0, RECENT_LIMIT)) {
    resolved.push({
      id: `vault.recent:${entry.path}`,
      title: `Open ${entry.name}`,
      group: 'Vault',
      hint: entry.path,
      icon: entry.missing ? FolderX : FolderOpen,
      needs: 'nothing',
      keywords: ['recent', 'switch', 'vault', entry.path],
      disabled: entry.missing,
      reason: entry.missing ? 'the folder is gone' : undefined,
      run: async () => {
        await ctx.actions.selectVault(entry.path)
      }
    })
  }

  return resolved
}

// ── Shortcuts ────────────────────────────────────────────────────────────────

const MAC_MODIFIERS: Record<string, string> = {
  mod: '⌘',
  meta: '⌘',
  ctrl: '⌃',
  shift: '⇧',
  alt: '⌥'
}

const OTHER_MODIFIERS: Record<string, string> = {
  mod: 'Ctrl',
  meta: 'Win',
  ctrl: 'Ctrl',
  shift: 'Shift',
  alt: 'Alt'
}

/**
 * `'Mod+Shift+P'` → `⇧⌘P` on a Mac, `Ctrl+Shift+P` anywhere else.
 *
 * Shortcuts are stored portably so one table can describe both platforms, and
 * the Mac order (modifiers ascending, ⌘ last) is the one people read as native.
 */
export function shortcutLabel(shortcut: string, platform: string): string {
  const mac = platform === 'darwin'
  const parts = shortcut.split('+')
  const key = parts[parts.length - 1]
  const modifiers = parts.slice(0, -1).map((part) => part.toLowerCase())

  // Each platform reads its modifiers in its own order — ⌃⌥⇧⌘ on a Mac, the
  // accelerator first everywhere else.
  const order = mac
    ? ['ctrl', 'alt', 'shift', 'mod', 'meta']
    : ['mod', 'meta', 'ctrl', 'alt', 'shift']
  const symbols = modifiers
    .sort((a, b) => order.indexOf(a) - order.indexOf(b))
    .map((name) => (mac ? MAC_MODIFIERS[name] : OTHER_MODIFIERS[name]) ?? name)

  const label = key.length === 1 ? key.toUpperCase() : key
  return mac ? `${symbols.join('')}${label}` : [...symbols, label].join('+')
}

/**
 * The command a keystroke fires, if any.
 *
 * Exported so the shell can drive its global keymap from the same table the
 * palette shows — a shortcut printed next to a command that does not actually
 * fire is worse than no shortcut at all. `Mod` accepts either ⌘ or Ctrl, which
 * is how a desktop app behaves on a machine with either keyboard.
 */
export function commandForEvent(event: KeyboardEvent, commands: Command[]): Command | null {
  for (const command of commands) {
    if (!command.shortcut || command.disabled) continue

    const parts = command.shortcut.split('+')
    const key = parts[parts.length - 1].toLowerCase()
    const modifiers = new Set(parts.slice(0, -1).map((part) => part.toLowerCase()))

    if (event.key.toLowerCase() !== key) continue
    if (modifiers.has('mod') !== (event.metaKey || event.ctrlKey)) continue
    if (modifiers.has('shift') !== event.shiftKey) continue
    if (modifiers.has('alt') !== event.altKey) continue

    return command
  }
  return null
}
