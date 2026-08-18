/**
 * Preferences in the renderer: one store, shared by every panel.
 *
 * A hook with its own `useState` would give each caller a private copy, and the
 * editor would keep the font size it was mounted with while the settings screen
 * insisted it had changed. So the state lives at module scope and every
 * `useSettings()` reads the same snapshot through `useSyncExternalStore`.
 *
 * Writes are optimistic and coalesced. Dragging the font-size slider fires a
 * change per pixel; each one has to reach the editor on the next frame, and none
 * of them has to reach the disk. The local value moves immediately, the patches
 * pile up, and one IPC write goes out once the hand stops.
 *
 * The main process is the authority on what is stored — this file never writes
 * `settings.json`, it only asks `window.api.settings` to.
 */

import { useEffect, useState, useSyncExternalStore } from 'react'
import type { DeepPartial, Settings } from '../../../shared/types'
import { normaliseHex } from '@/components/ui/color-field'

/** How long the store sits on a patch before persisting it. Long enough that a
 *  slider drag is one write, short enough that a crash cannot lose a choice. */
const PERSIST_MS = 200

/**
 * What the app renders before the first IPC round trip returns — and only then.
 * `main/settings.ts` owns the real defaults; this copy exists because the editor
 * has to draw itself with *some* font size on the first frame, and reading
 * `undefined` would be worse than reading a value that is replaced a tick later.
 * Keep the two in step; a drift here is visible for one frame and nowhere else.
 */
export const DEFAULT_SETTINGS: Settings = {
  appearance: { theme: 'system', accent: '#2E5A88', density: 'comfortable' },
  editor: {
    fontFamily: '',
    fontSize: 13,
    lineHeight: 1.6,
    lineNumbers: true,
    wordWrap: true,
    tabSize: 2,
    highlightActiveLine: true,
    bracketMatching: true,
    evidenceRail: true,
    lintGutter: true,
    syntaxTheme: 'auto'
  },
  build: { autoSaveMs: null, buildOnSave: false, watch: false, checkOnIdleMs: 600 },
  git: {
    autoCommit: false,
    autoPush: false,
    debounceMs: 4000,
    messageTemplate: 'report-maker: {n} file(s) — {date}'
  },
  vaults: { lastTarget: {} },
  startup: { reopenLast: true, lastVault: null },
  layout: { panes: {}, sidebar: true, viewer: true, problems: false }
}

// ── merging ──────────────────────────────────────────────────────────────────

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** `patch` over `base`, recursing into objects and replacing everything else —
 *  the same rule `main/settings.ts` applies, so the optimistic result and the
 *  stored one cannot disagree about what a patch meant. */
function merge<T>(base: T, patch: unknown): T {
  if (patch === undefined) return base
  if (!isRecord(base) || !isRecord(patch)) return patch as T

  const merged: Record<string, unknown> = { ...base }
  for (const [key, value] of Object.entries(patch)) {
    merged[key] = merge(merged[key], value)
  }
  return merged as T
}

// ── the store ────────────────────────────────────────────────────────────────

type Snapshot = { settings: Settings; loaded: boolean }

let current: Settings = DEFAULT_SETTINGS
let loaded = false
let snapshot: Snapshot = { settings: current, loaded }
const listeners = new Set<() => void>()

/** Patches written locally but not yet persisted, folded into one. */
let queued: DeepPartial<Settings> | null = null
let timer: ReturnType<typeof setTimeout> | null = null
let writing = false
let started = false
/** Everything changed before the first read came back, kept separately from the
 *  persist queue: a change made in that window has to win over what was on disk
 *  when the app started, and `queued` may already have been flushed. */
let beforeLoad: DeepPartial<Settings> | null = null

function publish(next: Settings, isLoaded: boolean = loaded): void {
  current = next
  loaded = isLoaded
  snapshot = { settings: current, loaded }
  // Appearance is applied here rather than by a caller: the theme is a property
  // of the settings, not of whoever happened to change them, and a panel that
  // forgot to call it would leave the window half-dressed.
  applyAppearance(current)
  for (const listener of listeners) listener()
}

function flush(): void {
  if (timer !== null) {
    clearTimeout(timer)
    timer = null
  }
  if (queued === null || writing) return

  const patch = queued
  queued = null
  writing = true
  Promise.resolve()
    .then(() => window.api.settings.set(patch))
    // Adopt what was stored only when nothing newer is waiting — otherwise the
    // response is already stale and would undo a keystroke.
    .then((stored) => {
      if (queued === null) publish(stored, true)
    })
    .catch(() => undefined)
    .finally(() => {
      writing = false
      if (queued !== null) flush()
    })
}

function ensureLoaded(): void {
  if (started) return
  started = true

  // 'system' means the OS decides, so the OS has to be able to change its mind
  // while the app is open.
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  media.addEventListener('change', () => applyAppearance(current))
  // A pending patch must survive a reload that lands after it; the disk is only
  // the authority for keys nobody has touched since.
  window.addEventListener('pagehide', flush)

  try {
    window.api.settings
      .get()
      .then((stored) => {
        publish(beforeLoad ? merge(stored, beforeLoad) : stored, true)
        beforeLoad = null
      })
      .catch(() => publish(current, true))
  } catch {
    // No bridge (a renderer running outside Electron). The defaults are the
    // answer, and nothing here should throw its way into a render.
    publish(current, true)
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  ensureLoaded()
  return () => {
    listeners.delete(listener)
  }
}

function getSnapshot(): Snapshot {
  return snapshot
}

/** Change one or more preferences. Takes effect immediately; reaches the disk
 *  shortly after. There is no save step, by design. */
export function updateSettings(patch: DeepPartial<Settings>): void {
  publish(merge(current, patch))
  queued = queued ? (merge(queued, patch) as DeepPartial<Settings>) : patch
  if (!loaded) beforeLoad = beforeLoad ? (merge(beforeLoad, patch) as DeepPartial<Settings>) : patch
  if (timer !== null) clearTimeout(timer)
  timer = setTimeout(flush, PERSIST_MS)
}

/** Forget every preference. The main process deletes the file rather than
 *  writing today's defaults into it, so a later build's defaults still reach
 *  someone who once pressed Reset. */
export async function resetSettings(): Promise<void> {
  if (timer !== null) clearTimeout(timer)
  timer = null
  queued = null
  beforeLoad = null
  try {
    publish(await window.api.settings.reset(), true)
  } catch {
    publish(DEFAULT_SETTINGS, true)
  }
}

/**
 * The preferences, live. `loaded` is false only for the first tick, while the
 * IPC read is in flight — most callers can ignore it and render the defaults.
 */
export function useSettings(): {
  settings: Settings
  loaded: boolean
  update: (patch: DeepPartial<Settings>) => void
  reset: () => Promise<void>
} {
  const snap = useSyncExternalStore(subscribe, getSnapshot)
  return {
    settings: snap.settings,
    loaded: snap.loaded,
    update: updateSettings,
    reset: resetSettings
  }
}

// ── appearance ───────────────────────────────────────────────────────────────

function systemPrefersDark(): boolean {
  return typeof window !== 'undefined'
    ? window.matchMedia('(prefers-color-scheme: dark)').matches
    : true
}

/** Whether the OS is asking for a dark window right now. Only `theme: 'system'`
 *  cares, but it has to re-render when the OS flips at dusk. */
export function usePrefersDark(): boolean {
  const [dark, setDark] = useState(systemPrefersDark)
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (): void => setDark(media.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])
  return dark
}

export function resolveTheme(
  theme: Settings['appearance']['theme'],
  prefersDark: boolean = systemPrefersDark()
): 'light' | 'dark' {
  return theme === 'system' ? (prefersDark ? 'dark' : 'light') : theme
}

/** Black or white, whichever can be read on `hex`. WCAG relative luminance —
 *  an accent picked from a brand is as likely to be pale as dark. */
function readableOn(hex: string): string {
  const channel = (index: number): number => {
    const value = parseInt(hex.slice(index, index + 2), 16) / 255
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  }
  const luminance = 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5)
  return luminance > 0.45 ? 'oklch(0.141 0.005 285.823)' : 'oklch(0.985 0 0)'
}

/**
 * Put the appearance settings on the document, where the stylesheet can see them.
 *
 * Everything is written as an inline custom property on `:root` rather than as a
 * new rule in `styles.css`: this file does not own the stylesheet, and an inline
 * property beats both the `:root` and `.dark` blocks without either of them
 * knowing it exists. The accent lands on `--primary` and `--ring`, which is what
 * makes buttons, switches, sliders and focus rings follow the setting for free.
 */
export function applyAppearance(
  settings: Settings,
  prefersDark: boolean = systemPrefersDark()
): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement

  const dark = resolveTheme(settings.appearance.theme, prefersDark) === 'dark'
  root.classList.toggle('dark', dark)
  root.style.colorScheme = dark ? 'dark' : 'light'

  const accent = normaliseHex(settings.appearance.accent) ?? DEFAULT_SETTINGS.appearance.accent
  root.style.setProperty('--app-accent', accent)
  root.style.setProperty('--primary', accent)
  root.style.setProperty('--primary-foreground', readableOn(accent))
  root.style.setProperty('--ring', accent)

  // Density scales the root font size, because every Tailwind spacing and type
  // utility in this app is expressed in rem — so one number moves the padding,
  // the row heights and the text together, the way a zoom control does. The body
  // carries its own px size, so it is set alongside. Comfortable is the browser
  // default: opting out changes nothing.
  const compact = settings.appearance.density === 'compact'
  root.dataset.density = settings.appearance.density
  root.style.fontSize = compact ? '14px' : '16px'
  if (document.body) document.body.style.fontSize = compact ? '12px' : '13px'
}

// ── editor typography ────────────────────────────────────────────────────────

/**
 * A curated monospace shortlist, offered above whatever `fonts:list` found. Most
 * of a system font list is unusable for code, and a writer should not have to
 * know which of six hundred families has a fixed advance width.
 */
export const MONO_FONTS = [
  'SF Mono',
  'Menlo',
  'Monaco',
  'JetBrains Mono',
  'Fira Code',
  'IBM Plex Mono',
  'Source Code Pro',
  'Roboto Mono',
  'Cascadia Code',
  'Consolas',
  'Courier New'
]

/** What CSS `font-family` the editor should use. An empty setting means "the
 *  app's own mono stack", which is a real choice and the default one. */
export function fontStack(fontFamily: string): string {
  const family = fontFamily.trim()
  return family ? `"${family}", var(--font-mono)` : 'var(--font-mono)'
}

// ── syntax themes ────────────────────────────────────────────────────────────

export type SyntaxTheme = Settings['editor']['syntaxTheme']

/** The one place the app names a colour of its own. Everything else in the
 *  renderer uses the semantic tokens; a syntax theme cannot, because being a
 *  palette *is* what it is. Editor and settings preview read this same table so
 *  the sample and the file cannot end up looking different. */
export type SyntaxPalette = {
  label: string
  description: string
  background: string
  foreground: string
  comment: string
  keyword: string
  func: string
  string: string
  /** `@key` citations and `<labels>` — the span this editor exists to show. */
  cite: string
  heading: string
  number: string
}

/**
 * `'auto'` resolved against the chrome — the palette a caller should draw with
 * when it needs to name one of the two concrete report themes.
 *
 * The editor does not need it: `cmtheme.ts:paletteFor` already falls through to
 * the same answer. It exists for anything that indexes a palette table by the
 * setting, where "auto" is a deferral and not a set of colours.
 */
export function resolveSyntaxTheme(
  theme: SyntaxTheme,
  dark: boolean
): Exclude<SyntaxTheme, 'auto'> {
  return theme === 'auto' ? (dark ? 'report-dark' : 'report-light') : theme
}

export const SYNTAX_THEMES: Record<SyntaxTheme, SyntaxPalette> = {
  /**
   * The default, and the only entry that names no polarity of its own.
   *
   * Every value is `light-dark(report-light, report-dark)`, which the browser
   * resolves against `color-scheme` — the property `applyAppearance` sets on the
   * document from the appearance setting. So this is not an approximation of the
   * two themes below: it *is* them, chosen by the same rule that dressed the
   * window, which is why a preview drawn straight from this table is honest
   * without anybody having to remember to resolve it first.
   */
  auto: {
    label: 'Auto',
    description: 'Follows the app appearance — report light on light, report dark on dark.',
    background: 'light-dark(oklch(1 0 0), oklch(0.141 0.005 285.823))',
    foreground: 'light-dark(oklch(0.141 0.005 285.823), oklch(0.985 0 0))',
    comment: 'light-dark(oklch(0.552 0.016 285.938), oklch(0.705 0.015 286.067))',
    keyword: 'light-dark(oklch(0.45 0.16 300), oklch(0.72 0.13 300))',
    func: 'light-dark(oklch(0.45 0.13 240), oklch(0.78 0.11 230))',
    string: 'light-dark(oklch(0.45 0.12 145), oklch(0.80 0.11 140))',
    cite: 'light-dark(oklch(0.52 0.20 25), oklch(0.75 0.16 25))',
    heading: 'light-dark(oklch(0.141 0.005 285.823), oklch(0.985 0 0))',
    number: 'light-dark(oklch(0.52 0.13 60), oklch(0.82 0.10 60))'
  },
  'report-light': {
    label: 'Report light',
    description: 'The app palette, on paper.',
    background: 'oklch(1 0 0)',
    foreground: 'oklch(0.141 0.005 285.823)',
    comment: 'oklch(0.552 0.016 285.938)',
    keyword: 'oklch(0.45 0.16 300)',
    func: 'oklch(0.45 0.13 240)',
    string: 'oklch(0.45 0.12 145)',
    cite: 'oklch(0.52 0.20 25)',
    heading: 'oklch(0.141 0.005 285.823)',
    number: 'oklch(0.52 0.13 60)'
  },
  'report-dark': {
    label: 'Report dark',
    description: 'The app palette. Citations carry the warm accent.',
    background: 'oklch(0.141 0.005 285.823)',
    foreground: 'oklch(0.985 0 0)',
    comment: 'oklch(0.705 0.015 286.067)',
    keyword: 'oklch(0.72 0.13 300)',
    func: 'oklch(0.78 0.11 230)',
    string: 'oklch(0.80 0.11 140)',
    cite: 'oklch(0.75 0.16 25)',
    heading: 'oklch(0.985 0 0)',
    number: 'oklch(0.82 0.10 60)'
  },
  mono: {
    label: 'Mono',
    description: 'No colour but the citation. Follows the window theme.',
    background: 'var(--background)',
    foreground: 'var(--foreground)',
    comment: 'var(--muted-foreground)',
    keyword: 'var(--foreground)',
    func: 'var(--foreground)',
    string: 'var(--muted-foreground)',
    cite: 'var(--app-accent, var(--foreground))',
    heading: 'var(--foreground)',
    number: 'var(--muted-foreground)'
  },
  solarized: {
    label: 'Solarized',
    description: "Ethan Schoonover's palette, at its published values.",
    background: '#002b36',
    foreground: '#93a1a1',
    comment: '#586e75',
    keyword: '#859900',
    func: '#268bd2',
    string: '#2aa198',
    cite: '#cb4b16',
    heading: '#b58900',
    number: '#d33682'
  },
  'high-contrast': {
    label: 'High contrast',
    description: 'Maximum separation, for glare or for tired eyes.',
    background: 'oklch(0 0 0)',
    foreground: 'oklch(1 0 0)',
    comment: 'oklch(0.75 0 0)',
    keyword: 'oklch(0.85 0.20 300)',
    func: 'oklch(0.85 0.16 230)',
    string: 'oklch(0.90 0.20 140)',
    cite: 'oklch(0.85 0.22 30)',
    heading: 'oklch(1 0 0)',
    number: 'oklch(0.92 0.18 90)'
  }
}

export type SyntaxKind =
  | 'plain'
  | 'comment'
  | 'keyword'
  | 'func'
  | 'string'
  | 'cite'
  | 'heading'
  | 'number'

export function syntaxColor(palette: SyntaxPalette, kind: SyntaxKind): string {
  return kind === 'plain' ? palette.foreground : palette[kind]
}

/**
 * Six lines of real report source, hand-classified.
 *
 * Hand-classified rather than run through the editor's tokeniser on purpose: a
 * preview is a picture of a theme, not a second parser to keep working, and this
 * sample is chosen to show every token a report actually contains — a citation,
 * an assessment, a helper call, a comment.
 */
export const TYPST_SAMPLE: { text: string; kind: SyntaxKind }[][] = [
  [
    { text: '#import', kind: 'keyword' },
    { text: ' ', kind: 'plain' },
    { text: '"/.build/design/base/report.typ"', kind: 'string' },
    { text: ': *', kind: 'plain' }
  ],
  [{ text: '== Pricing', kind: 'heading' }],
  [
    { text: 'The list price rose ', kind: 'plain' },
    { text: '12', kind: 'number' },
    { text: '% in Q3 ', kind: 'plain' },
    { text: '@acme-pricing', kind: 'cite' },
    { text: '.', kind: 'plain' }
  ],
  [
    { text: 'It will not hold through renewal ', kind: 'plain' },
    { text: '#assess', kind: 'func' },
    { text: '.', kind: 'plain' }
  ],
  [
    { text: '#srcfig', kind: 'func' },
    { text: '(chart, source: [', kind: 'plain' },
    { text: '@acme-pricing', kind: 'cite' },
    { text: '])', kind: 'plain' }
  ],
  [{ text: '// cited, or it is an opinion — there is no third category', kind: 'comment' }]
]
