/**
 * The brand pack, as the engine resolves it — and the specimen it renders.
 *
 * Four jobs, one for each thing the brand studio has to do:
 *
 * — **Read.** `brand show <pack> --json` is the only source of a resolved value.
 *   The engine layers the vault's pack over its own built-in default, and it is
 *   the engine that decides what "resolved" means; re-implementing that merge
 *   here is how the studio would end up showing a colour the report does not use.
 * — **Write.** A pack file is *patched*, never rewritten from the form. A pack
 *   may carry `$comment`, a key added by a newer engine, or a section this build
 *   has no control for, and all of it has to survive an edit. So a change is a
 *   set of dotted keys applied to the parsed file, and everything else is left
 *   byte-for-byte alone.
 * — **Distinguish.** A field is *inherited* when the vault's own pack file does
 *   not mention it. That is read off the file we already have open for patching,
 *   and corroborated by whatever provenance `brand show --json` reports, so the
 *   studio does not need a second opinion about the engine's defaults.
 * — **Render.** `brand preview --pack <pack>` builds a specimen document and
 *   prints the page PNGs it produced; those bytes come back over IPC as blob
 *   URLs, exactly as the report thumbnails do.
 *
 * Nothing here computes a brand value, a default, or a build. Every answer is a
 * CLI answer; this module is the plumbing between it and a form.
 *
 * The resolved pack is typed as an open record rather than as `BrandPack` from
 * the IPC vocabulary. `BrandPack` describes a *complete* pack — the studio has to
 * render whatever it was actually handed, including a pack that predates a
 * section or an engine too old to answer at all, and a cast would be a claim
 * about the file that nobody checked.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

// ── dotted keys ──────────────────────────────────────────────────────────────

export type Tree = Record<string, unknown>

/** A change to a pack: dotted key → value, or `undefined` to inherit again. */
export type BrandPatch = Record<string, unknown>

function isRecord(value: unknown): value is Tree {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function clone<T>(value: T): T {
  return structuredClone(value)
}

/** The value at `colors.accent`, or undefined. Arrays are leaves — a font stack
 *  is one value, not three keys. */
export function getAt(root: unknown, key: string): unknown {
  let node: unknown = root
  for (const part of key.split('.')) {
    if (!isRecord(node)) return undefined
    node = node[part]
  }
  return node
}

/** Set `colors.accent`, creating the objects on the way down. Mutates `root`. */
export function setAt(root: Tree, key: string, value: unknown): void {
  const parts = key.split('.')
  const last = parts.pop()
  if (!last) return
  let node = root
  for (const part of parts) {
    const next = node[part]
    if (!isRecord(next)) node[part] = {}
    node = node[part] as Tree
  }
  node[last] = value
}

/**
 * Remove `colors.accent`, and any object the removal left empty.
 *
 * Pruning matters: a pack whose `colors` is `{}` reads, to a human opening the
 * file, as "this pack deliberately overrides no colours", which is a different
 * statement from not having the section at all.
 */
export function deleteAt(root: Tree, key: string): void {
  const parts = key.split('.')
  const chain: Tree[] = [root]
  for (const part of parts.slice(0, -1)) {
    const next = chain[chain.length - 1][part]
    if (!isRecord(next)) return
    chain.push(next)
  }
  delete chain[chain.length - 1][parts[parts.length - 1]]
  for (let depth = chain.length - 1; depth > 0; depth -= 1) {
    if (Object.keys(chain[depth]).length > 0) break
    delete chain[depth - 1][parts[depth - 1]]
  }
}

/**
 * Every leaf of a pack as a dotted key. `$`-prefixed keys are skipped: the
 * default pack carries a `$comment` array that is documentation, not a token.
 */
export function flatten(node: unknown, prefix = ''): Record<string, unknown> {
  if (!isRecord(node)) return prefix ? { [prefix]: node } : {}
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(node)) {
    if (key.startsWith('$')) continue
    const dotted = prefix ? `${prefix}.${key}` : key
    if (isRecord(value)) Object.assign(out, flatten(value, dotted))
    else out[dotted] = value
  }
  return out
}

/** One section of a pack — `colors`, `sizes` — or an empty map when the pack
 *  this build was handed does not have it. */
export function section(tree: Tree, name: string): Record<string, unknown> {
  const found = tree[name]
  return isRecord(found) ? found : {}
}

// ── where a pack lives ───────────────────────────────────────────────────────

/** `brand/brand.json` is the default pack; `brand/<name>/brand.json` is a named
 *  one. This mirrors `engine/vault.py:brand_packs`, which is the definition. */
export function packFile(pack: string): string {
  return pack === 'default' ? 'brand/brand.json' : `brand/${pack}/brand.json`
}

export function packPath(vault: string, pack: string): string {
  return `${vault}/${packFile(pack)}`
}

// ── reading ──────────────────────────────────────────────────────────────────

export type Resolved = {
  pack: string
  /** The resolved pack, nested as it sits in a brand.json. */
  tree: Tree
  /** Dotted keys this pack sets itself; everything else is inherited. */
  overrides: ReadonlySet<string>
  /** The vault's pack file, parsed — the object `writeBrand` patches. */
  file: Tree
  /** Set when the engine could not answer and the form is showing the pack file
   *  alone: the values are real, but the inherited ones are missing. */
  degraded: string | null
}

type Tagged = { value: unknown; inherited: boolean }

/** A leaf `brand show --json` tagged with where the value came from. The key
 *  names vary by engine build, so recognise the shape rather than one spelling. */
function tagged(node: unknown): Tagged | null {
  if (!isRecord(node) || !('value' in node)) return null
  const source = node.source ?? node.from ?? node.origin ?? node.inherited
  if (source === undefined) return null
  if (typeof source === 'boolean') return { value: node.value, inherited: source }
  const text = String(source).toLowerCase()
  return {
    value: node.value,
    inherited: ['default', 'builtin', 'built-in', 'engine', 'inherited'].includes(text)
  }
}

/**
 * `brand show --json` in whatever envelope it arrives in.
 *
 * A12 fixes the command and what it must say — every resolved key, with the
 * source of each value — but not the JSON shape, and this file cannot be the
 * thing that decides it. So: an envelope is unwrapped if there is one, dotted
 * keys and nested objects both work, and a tagged leaf is recognised by having a
 * `value` beside a source. A shape nobody anticipated still yields the values;
 * only the "default" chips fall back to the pack file, which is where they are
 * read from anyway.
 */
function absorb(raw: unknown): { tree: Tree; inherited: Set<string>; owned: Set<string> } {
  const tree: Tree = {}
  const inherited = new Set<string>()
  const owned = new Set<string>()

  const walk = (node: unknown, prefix: string): void => {
    const leaf = tagged(node)
    if (leaf) {
      setAt(tree, prefix, leaf.value)
      ;(leaf.inherited ? inherited : owned).add(prefix)
      return
    }
    if (isRecord(node)) {
      for (const [key, value] of Object.entries(node)) {
        if (key.startsWith('$')) continue
        walk(value, prefix ? `${prefix}.${key}` : key)
      }
      return
    }
    if (prefix) setAt(tree, prefix, node)
  }

  const envelope = isRecord(raw)
    ? ((raw.values ?? raw.keys ?? raw.resolved ?? raw.fields ?? raw.brand ?? raw) as unknown)
    : raw
  walk(envelope, '')
  return { tree, inherited, owned }
}

async function readPackFile(vault: string, pack: string): Promise<Tree> {
  const path = packPath(vault, pack)
  if (!(await window.api.files.exists(vault, path))) return {}
  const text = await window.api.files.read(vault, path)
  if (!text.trim()) return {}
  const parsed: unknown = JSON.parse(text)
  if (!isRecord(parsed)) throw new Error(`${packFile(pack)} is not a JSON object`)
  return parsed
}

/** The resolved pack, plus the file it is layered on. */
export async function loadBrand(vault: string, pack: string): Promise<Resolved> {
  // The file first: it is what a patch is applied to, and what says which keys
  // this pack owns. A failure here is fatal — editing a pack we could not parse
  // would destroy it.
  const file = await readPackFile(vault, pack)

  let shown: unknown
  let degraded: string | null = null
  try {
    shown = await window.api.engine.json<unknown>(vault, ['brand', 'show', pack, '--json'])
  } catch (err) {
    degraded = String(err instanceof Error ? err.message : err).trim()
  }

  const absorbed = degraded === null ? absorb(shown) : { tree: clone(file), inherited: new Set<string>(), owned: new Set<string>() }

  // The pack file is the authority on what this pack sets: it is the thing being
  // edited. The engine's own tags refine it — a build that reports provenance can
  // mark a key inherited that the file mentions only inside a comment block.
  const written = flatten(file)
  const overrides = new Set<string>(Object.keys(written))
  for (const key of absorbed.owned) overrides.add(key)
  for (const key of absorbed.inherited) if (!(key in written)) overrides.delete(key)

  return { pack, tree: absorbed.tree, overrides, file, degraded }
}

// ── writing ──────────────────────────────────────────────────────────────────

/** One in-flight write per pack file. Two overlapping read-modify-writes would
 *  race, and the loser would silently drop a keystroke. */
const writing = new Map<string, Promise<unknown>>()

/**
 * Patch a pack file: set the dotted keys named, delete the ones set to
 * `undefined`, leave everything else exactly as it was found.
 *
 * Returns the file as written, so a caller can keep its own copy in step without
 * a second read.
 */
export async function writeBrand(vault: string, pack: string, patch: BrandPatch): Promise<Tree> {
  const path = packPath(vault, pack)
  const previous = writing.get(path) ?? Promise.resolve()

  const work = previous.catch(() => undefined).then(async () => {
    const file = await readPackFile(vault, pack)
    for (const [key, value] of Object.entries(patch)) {
      if (value === undefined) deleteAt(file, key)
      else setAt(file, key, value)
    }
    const text = JSON.stringify(file, null, 2) + '\n'
    try {
      await window.api.files.write(vault, path, text)
    } catch (err) {
      const message = String(err instanceof Error ? err.message : err)
      throw new Error(
        `could not write ${packFile(pack)}: ${message}` +
          (pack === 'default' ? '\nCreate the pack first: report-maker brand new default' : '')
      )
    }
    return file
  })

  writing.set(path, work)
  try {
    return await work
  } finally {
    if (writing.get(path) === work) writing.delete(path)
  }
}

// ── packs ────────────────────────────────────────────────────────────────────

export type PackRow = {
  name: string
  /** The engine's own description of the pack, when it prints one. */
  detail: string | null
}

/** `brand list --json`, in whatever envelope it arrives in — an array of names,
 *  an array of rows, or a map of name to path. */
function absorbPacks(raw: unknown): PackRow[] {
  const rows: PackRow[] = []
  const push = (name: unknown, detail: unknown): void => {
    const text = typeof name === 'string' ? name : null
    if (!text || rows.some((row) => row.name === text)) return
    rows.push({ name: text, detail: typeof detail === 'string' ? detail : null })
  }

  const list = isRecord(raw) && Array.isArray(raw.packs) ? raw.packs : raw
  if (Array.isArray(list)) {
    for (const entry of list) {
      if (typeof entry === 'string') push(entry, null)
      else if (isRecord(entry)) push(entry.name ?? entry.pack ?? entry.id, entry.path ?? entry.file)
    }
  } else if (isRecord(list)) {
    for (const [name, value] of Object.entries(list)) {
      if (name.startsWith('$')) continue
      push(name, typeof value === 'string' ? value : isRecord(value) ? value.path : null)
    }
  }
  return rows
}

export type UsePacks = {
  packs: PackRow[]
  loading: boolean
  error: string | null
  reload: () => Promise<void>
}

export function usePacks(vault: string | null): UsePacks {
  const [packs, setPacks] = useState<PackRow[]>([])
  const [loading, setLoading] = useState(Boolean(vault))
  const [error, setError] = useState<string | null>(null)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const reload = useCallback(async () => {
    if (!vault) {
      setPacks([])
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      const raw = await window.api.engine.json<unknown>(vault, ['brand', 'list', '--json'])
      if (!alive.current) return
      const rows = absorbPacks(raw)
      // A vault always has a default pack, even before anyone writes one: that is
      // what the engine falls back to, so it must be selectable.
      setPacks(rows.some((row) => row.name === 'default') ? rows : [{ name: 'default', detail: null }, ...rows])
      setError(null)
    } catch (err) {
      if (!alive.current) return
      setPacks([{ name: 'default', detail: null }])
      setError(String(err instanceof Error ? err.message : err).trim())
    } finally {
      if (alive.current) setLoading(false)
    }
  }, [vault])

  useEffect(() => {
    void reload()
  }, [reload])

  return { packs, loading, error, reload }
}

// ── the form's state ─────────────────────────────────────────────────────────

export type UseBrand = {
  resolved: Resolved | null
  loading: boolean
  /** Set only when the pack could not be read at all. */
  error: string | null
  /** Apply changes to the form now, without touching the disk. */
  stage: (patch: BrandPatch) => void
  /** Write everything staged since the last commit. Resolves to the keys written,
   *  so a caller can tell a no-op from a change. */
  commit: () => Promise<string[]>
  reload: () => Promise<void>
}

/**
 * One pack, loaded and edited.
 *
 * Staging is separate from committing because the two have different clocks. A
 * colour has to move under the cursor on the next frame; the file it lives in
 * should be written once the hand stops, and the specimen rebuilt once after
 * that — not once per pixel of a drag. The caller owns the debounce, this owns
 * what is true in the meantime.
 */
export function useBrand(vault: string | null, pack: string): UseBrand {
  const [resolved, setResolved] = useState<Resolved | null>(null)
  const [loading, setLoading] = useState(Boolean(vault))
  const [error, setError] = useState<string | null>(null)
  const staged = useRef<BrandPatch>({})
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const reload = useCallback(async () => {
    if (!vault) {
      setResolved(null)
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      const next = await loadBrand(vault, pack)
      if (!alive.current) return
      // Anything staged while the read was in flight is newer than the disk. Put
      // it back on top, or a fast hand would watch its own edit disappear and
      // reappear one build later.
      const tree = clone(next.tree)
      const overrides = new Set(next.overrides)
      for (const [key, value] of Object.entries(staged.current)) {
        if (value === undefined) overrides.delete(key)
        else {
          setAt(tree, key, value)
          overrides.add(key)
        }
      }
      setResolved({ ...next, tree, overrides })
      setError(null)
    } catch (err) {
      if (!alive.current) return
      setResolved(null)
      setError(String(err instanceof Error ? err.message : err).trim())
    } finally {
      if (alive.current) setLoading(false)
    }
  }, [vault, pack])

  useEffect(() => {
    staged.current = {}
    void reload()
  }, [reload])

  const stage = useCallback((patch: BrandPatch) => {
    staged.current = { ...staged.current, ...patch }
    setResolved((current) => {
      if (!current) return current
      const tree = clone(current.tree)
      const overrides = new Set(current.overrides)
      for (const [key, value] of Object.entries(patch)) {
        if (value === undefined) {
          // Clearing an override does not clear the value — the inherited one
          // takes over, and only a reload knows what it is. Until then the form
          // shows what it had, marked as inherited, rather than an empty control.
          overrides.delete(key)
        } else {
          setAt(tree, key, value)
          overrides.add(key)
        }
      }
      return { ...current, tree, overrides }
    })
  }, [])

  const commit = useCallback(async () => {
    const patch = staged.current
    const keys = Object.keys(patch)
    if (!vault || keys.length === 0) return []
    staged.current = {}
    const file = await writeBrand(vault, pack, patch)
    if (alive.current) setResolved((current) => (current ? { ...current, file } : current))
    return keys
  }, [vault, pack])

  return { resolved, loading, error, stage, commit, reload }
}

// ── the specimen ─────────────────────────────────────────────────────────────

export type Preview = {
  ok: boolean
  /** Blob URLs of the specimen's pages, in order. The caller revokes them. */
  pages: string[]
  /** Wall-clock milliseconds the build took. */
  ms: number
  /** The engine's own words when it failed. */
  stderr: string
  command: string
}

function revoke(urls: readonly string[]): void {
  for (const url of urls) URL.revokeObjectURL(url)
}

/** A path the engine printed, made absolute inside the vault. The engine prints
 *  absolute paths, and Typst-style project-absolute ones (`/.build/…`); both
 *  resolve here, and anything genuinely outside the vault is refused by the main
 *  process before it is read. */
function insideVault(vault: string, raw: string): string {
  const text = raw.trim().replace(/^['"(]+|['"),.]+$/g, '')
  if (text.startsWith(vault)) return text
  return text.startsWith('/') ? `${vault}${text}` : `${vault}/${text}`
}

/** Every `.png` the run printed, in the order it printed them. `brand preview`
 *  prints the page images it produced — that list is the engine's answer, not a
 *  convention this file guesses at. */
function printedPages(vault: string, run: { stdout: string; stderr: string }): string[] {
  const seen: string[] = []
  for (const token of `${run.stdout}\n${run.stderr}`.match(/\S+\.png/g) ?? []) {
    const path = insideVault(vault, token)
    if (!seen.includes(path)) seen.push(path)
  }
  return seen
}

/** `pages.json` beside the specimen, the same index `engine/pages.py` writes for
 *  a report. Only consulted when the run printed no paths. */
async function indexedPages(vault: string, pack: string): Promise<string[]> {
  const dir = `${vault}/.build/brand-preview/${pack}`
  const index = `${dir}/pages.json`
  if (!(await window.api.files.exists(vault, index))) return []
  try {
    const parsed = JSON.parse(await window.api.files.read(vault, index)) as { pages?: unknown }
    const names = Array.isArray(parsed.pages) ? parsed.pages : []
    return names.filter((name): name is string => typeof name === 'string').map((name) => `${dir}/${name}`)
  } catch {
    return []
  }
}

async function blobs(vault: string, paths: readonly string[]): Promise<string[]> {
  const urls: string[] = []
  for (const path of paths) {
    try {
      const bytes = await window.api.files.bytes(vault, path)
      // .slice() copies into a plain ArrayBuffer, which is what Blob accepts —
      // the same dance the PDF viewer and the report thumbnails do.
      const buffer = bytes.slice().buffer as ArrayBuffer
      urls.push(URL.createObjectURL(new Blob([buffer], { type: 'image/png' })))
    } catch {
      // A page the engine named but could not write is a build problem, and the
      // build's own output already says so. Show the pages that exist.
      break
    }
  }
  return urls
}

/**
 * Build the specimen for one pack and load its pages.
 *
 * The specimen is `report-maker brand preview`'s business — where it lives, what
 * it exercises, and how many pages it runs to. This waits for it and reads what
 * it names.
 */
export async function buildPreview(vault: string, pack: string): Promise<Preview> {
  const started = performance.now()
  const run = await window.api.engine.run(vault, ['brand', 'preview', '--pack', pack])
  const ms = Math.round(performance.now() - started)

  if (run.code !== 0) {
    return {
      ok: false,
      pages: [],
      ms,
      stderr: (run.stderr || run.stdout).trim() || `exit ${run.code}`,
      command: run.command
    }
  }

  const printed = printedPages(vault, run)
  const paths = printed.length > 0 ? printed : await indexedPages(vault, pack)
  const pages = await blobs(vault, paths)

  if (pages.length === 0) {
    return {
      ok: false,
      pages: [],
      ms,
      stderr:
        (run.stdout || run.stderr).trim() ||
        'brand preview reported no page images. Run it in a terminal to see what it produced.',
      command: run.command
    }
  }
  return { ok: true, pages, ms, stderr: '', command: run.command }
}

export type PreviewState = {
  /** The newest render. */
  pages: string[]
  /** The render before it, still mounted underneath so the swap can cross-fade. */
  previous: string[]
  /** Bumped on every completed render — the key the fading layer is drawn with. */
  generation: number
  building: boolean
  /** Milliseconds of the last completed build. */
  ms: number | null
  /** The engine's stderr when the last build failed. The pages stay on screen. */
  error: string | null
  command: string | null
}

const IDLE: PreviewState = {
  pages: [],
  previous: [],
  generation: 0,
  building: false,
  ms: null,
  error: null,
  command: null
}

/**
 * The specimen, rebuilt whenever `nonce` changes.
 *
 * Two rules, both of which exist because the point of this pane is watching a
 * document change rather than watching a spinner:
 *
 * — **Edits coalesce.** A build takes seconds; keystrokes do not wait for it. An
 *   edit landing mid-build queues exactly one follow-up, so ten changes during
 *   one render cost one more render, not ten.
 * — **The pane never blanks.** A failed or in-flight build leaves the last good
 *   pages where they were; only a change of pack clears them, because those
 *   pages are a different document and not a stale version of this one.
 */
export function usePreview(vault: string | null, pack: string, nonce: number): PreviewState {
  const [state, setState] = useState<PreviewState>(IDLE)
  const alive = useRef(true)
  const running = useRef(false)
  const queued = useRef(false)
  const wanted = useRef({ vault, pack })
  /** The generations still referenced by `pages` and `previous`. */
  const generations = useRef<string[][]>([])

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      generations.current.forEach(revoke)
      generations.current = []
    }
  }, [])

  // Declared before the build effect so it runs first: a build started for the
  // pack we are leaving must know that its result is no longer wanted.
  useEffect(() => {
    wanted.current = { vault, pack }
    generations.current.forEach(revoke)
    generations.current = []
    setState(IDLE)
  }, [vault, pack])

  const drain = useCallback(async (target: string, name: string) => {
    if (running.current) {
      queued.current = true
      return
    }
    running.current = true
    setState((current) => ({ ...current, building: true }))
    try {
      for (;;) {
        queued.current = false
        const result = await buildPreview(target, name)
        const stale =
          !alive.current || wanted.current.vault !== target || wanted.current.pack !== name
        if (stale) {
          revoke(result.pages)
          return
        }
        if (result.ok) {
          generations.current.push(result.pages)
          while (generations.current.length > 2) revoke(generations.current.shift() ?? [])
          setState((current) => ({
            pages: result.pages,
            previous: current.pages,
            generation: current.generation + 1,
            building: true,
            ms: result.ms,
            error: null,
            command: null
          }))
        } else {
          setState((current) => ({
            ...current,
            building: true,
            ms: result.ms,
            error: result.stderr,
            command: result.command
          }))
        }
        if (!queued.current) break
      }
    } finally {
      running.current = false
      if (alive.current) setState((current) => ({ ...current, building: false }))
    }
  }, [])

  useEffect(() => {
    if (!vault) return
    void drain(vault, pack)
  }, [vault, pack, nonce, drain])

  return state
}

// ── system fonts ─────────────────────────────────────────────────────────────

/**
 * The families installed on this machine, from `fonts:list`.
 *
 * A brand names fonts by family, and Typst resolves them by name at build time —
 * so the list is a picker's vocabulary, not a promise. A family missing here can
 * still be typed in: the machine that renders the report may not be this one.
 */
export function useFonts(): { fonts: string[]; loading: boolean } {
  const [fonts, setFonts] = useState<string[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    window.api.fonts
      .list()
      .then((found) => alive && setFonts(found))
      .catch(() => undefined)
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [])

  return { fonts, loading }
}
