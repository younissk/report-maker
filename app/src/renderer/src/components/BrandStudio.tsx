import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  Building2,
  Check,
  ChevronsUpDown,
  Copy,
  Download,
  Image as ImageIcon,
  Loader2,
  Palette,
  Plus,
  RefreshCw,
  RotateCcw,
  Ruler,
  Sliders,
  Type as TypeIcon,
  Upload,
  X
} from 'lucide-react'
import type { Node } from '../../../shared/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList
} from '@/components/ui/command'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup
} from '@/components/ui/resizable'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Slider } from '@/components/ui/slider'
import { ColorField } from '@/components/ui/color-field'
import {
  flatten,
  getAt,
  packFile,
  section,
  useBrand,
  useFonts,
  usePacks,
  usePreview,
  type BrandPatch,
  type Tree
} from '@/lib/brand'
import { cn } from '@/lib/utils'

/**
 * The brand studio: a form on the left, the document it produces on the right.
 *
 * Everything here rests on one loop — edit, wait 400 ms, patch `brand.json`, run
 * `brand preview`, cross-fade the new pages in. The waiting is the interesting
 * part. A Typst build takes seconds and a drag on a slider takes none, so an edit
 * that lands mid-build queues exactly one follow-up rather than one per frame,
 * and the pane keeps showing the last good render throughout. A studio that
 * blanked while rebuilding would be a studio nobody could use to judge a colour,
 * which is the only reason it exists.
 *
 * The form is generated from the pack, not from a list of fields typed out here:
 * every key in `colors`, every key in `sizes`, whatever `defaults` holds. A pack
 * with a colour this build has never heard of still gets a control, and a key
 * this build has no control for still survives an edit — see `lib/brand.ts`,
 * which patches rather than rewrites.
 */

type Props = {
  vault: string | null
  /** Which pack to open on. */
  pack?: string
  onClose?: () => void
  className?: string
}

/** Long enough that a slider drag is one build, short enough that a colour
 *  change feels like it took effect immediately. */
const DEBOUNCE_MS = 400

export function BrandStudio({ vault, pack: initial, onClose, className }: Props): React.JSX.Element {
  const [pack, setPack] = useState(initial ?? 'default')
  const { packs, reload: reloadPacks } = usePacks(vault)
  const { resolved, loading, error, stage, commit, reload } = useBrand(vault, pack)
  const [nonce, setNonce] = useState(0)
  const preview = usePreview(vault, pack, nonce)
  const { fonts } = useFonts()

  const [saving, setSaving] = useState(false)
  const [writeError, setWriteError] = useState<string | null>(null)
  const [dialog, setDialog] = useState<'new' | 'duplicate' | null>(null)
  const [pending, setPending] = useState<{ name: string; patch: BrandPatch } | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const commitRef = useRef(commit)
  commitRef.current = commit

  // ── the loop ───────────────────────────────────────────────────────────────

  const write = useCallback(async () => {
    setSaving(true)
    try {
      const keys = await commitRef.current()
      setWriteError(null)
      // Nothing changed on disk means nothing to re-render — a rebuild here would
      // burn seconds to produce the identical page.
      if (keys.length > 0) setNonce((current) => current + 1)
    } catch (err) {
      setWriteError(String(err instanceof Error ? err.message : err))
    } finally {
      setSaving(false)
    }
  }, [])

  const flush = useCallback(async () => {
    if (timer.current !== null) {
      clearTimeout(timer.current)
      timer.current = null
    }
    await write()
  }, [write])

  /** Every control calls this. It moves the form now and the disk shortly after. */
  const edit = useCallback(
    (patch: BrandPatch) => {
      stage(patch)
      if (timer.current !== null) clearTimeout(timer.current)
      timer.current = setTimeout(() => {
        timer.current = null
        void write()
      }, DEBOUNCE_MS)
    },
    [stage, write]
  )

  // A pack edited and immediately closed is still an edit. The write is fired
  // rather than awaited — unmount cannot wait — and `writeBrand` serialises it
  // behind anything already in flight.
  useEffect(
    () => () => {
      if (timer.current === null) return
      clearTimeout(timer.current)
      void commitRef.current().catch(() => undefined)
    },
    []
  )

  const switchPack = useCallback(
    async (next: string) => {
      await flush()
      setPack(next)
    },
    [flush]
  )

  // Re-read the resolved pack after each build. A build is the moment the engine
  // last looked at the file the studio wrote, so it is the cheapest honest point
  // to ask what it resolved to — an import that set twenty keys leaves twenty
  // "default" chips to update, and deciding those in the renderer is the drift
  // this app exists to avoid. Keyed on the build counter alone: switching packs
  // already reloads, and doing it twice would flash the form.
  const built = useRef(nonce)
  useEffect(() => {
    if (built.current === nonce) return
    built.current = nonce
    void reload()
  }, [nonce, reload])

  // ── pack management ────────────────────────────────────────────────────────

  const createPack = useCallback(
    async (name: string, from: string | null): Promise<string | null> => {
      if (!vault) return 'no vault'
      const args = ['brand', 'new', name, ...(from ? ['--from', from] : [])]
      const run = await window.api.engine.run(vault, args)
      if (run.code !== 0) return (run.stderr || run.stdout).trim() || `exit ${run.code}`
      await reloadPacks()
      await switchPack(name)
      return null
    },
    [vault, reloadPacks, switchPack]
  )

  const exportPack = useCallback(() => {
    if (!resolved) return
    // The pack file, not the resolved values: a pack is a set of overrides, and
    // freezing today's engine defaults into the export would turn every inherited
    // key into an override the moment it was imported somewhere else.
    const text = JSON.stringify(resolved.file, null, 2) + '\n'
    const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `${pack === 'default' ? 'brand' : pack}.brand.json`
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 10_000)
  }, [resolved, pack])

  const importPack = useCallback(async (file: File) => {
    try {
      const parsed: unknown = JSON.parse(await file.text())
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        throw new Error('not a JSON object')
      }
      setPending({ name: file.name, patch: flatten(parsed) })
    } catch (err) {
      setWriteError(`${file.name}: ${String(err instanceof Error ? err.message : err)}`)
    }
  }, [])

  // ── rendering ──────────────────────────────────────────────────────────────

  if (!vault) {
    return (
      <div className={cn('flex h-full items-center justify-center p-8', className)}>
        <p className="text-xs text-muted-foreground">Open a vault to edit its brand.</p>
      </div>
    )
  }

  return (
    <div className={cn('flex h-full min-h-0 flex-col', className)}>
      <Toolbar
        pack={pack}
        packs={packs.map((row) => row.name)}
        saving={saving}
        onSwitch={switchPack}
        onNew={() => setDialog('new')}
        onDuplicate={() => setDialog('duplicate')}
        onExport={exportPack}
        onImport={importPack}
        onRebuild={() => setNonce((current) => current + 1)}
        onClose={onClose}
      />
      <Separator />

      <ResizablePanelGroup direction="horizontal" className="min-h-0 flex-1">
        <ResizablePanel id="brand-form" defaultSize={46} minSize={30} className="min-w-0">
          <ScrollArea className="h-full">
            <div className="space-y-6 p-4 pb-16">
              {error && (
                <Notice tone="error" title={`Could not read ${packFile(pack)}`}>
                  {error}
                </Notice>
              )}
              {writeError && (
                <Notice tone="error" title="The pack was not written">
                  {writeError}
                </Notice>
              )}
              {resolved?.degraded && (
                <Notice tone="warn" title="report-maker brand show did not answer">
                  {resolved.degraded}
                  {'\n\n'}
                  Showing the keys this pack sets. Inherited values are missing until the
                  engine can resolve them.
                </Notice>
              )}
              {loading && !resolved && <FormSkeleton />}
              {resolved && (
                <Form tree={resolved.tree} overrides={resolved.overrides} vault={vault} fonts={fonts} onEdit={edit} />
              )}
            </div>
          </ScrollArea>
        </ResizablePanel>

        <ResizableHandle withHandle />

        <ResizablePanel id="brand-specimen" defaultSize={54} minSize={25} className="min-w-0">
          <Specimen pack={pack} state={preview} onRebuild={() => setNonce((current) => current + 1)} />
        </ResizablePanel>
      </ResizablePanelGroup>

      <PackDialog
        mode={dialog}
        from={pack}
        onClose={() => setDialog(null)}
        onCreate={createPack}
      />

      <Dialog open={pending !== null} onOpenChange={(open) => !open && setPending(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="text-sm">Import into {pack}</DialogTitle>
            <DialogDescription className="text-xs">
              {pending?.name} sets {Object.keys(pending?.patch ?? {}).length} values. They are
              merged into <span className="font-mono">{packFile(pack)}</span>; keys the file
              already has that the import does not mention are left alone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" size="sm" onClick={() => setPending(null)}>
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={() => {
                if (pending) {
                  stage(pending.patch)
                  void flush()
                }
                setPending(null)
              }}
            >
              Merge
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

// ── toolbar ──────────────────────────────────────────────────────────────────

function Toolbar({
  pack,
  packs,
  saving,
  onSwitch,
  onNew,
  onDuplicate,
  onExport,
  onImport,
  onRebuild,
  onClose
}: {
  pack: string
  packs: string[]
  saving: boolean
  onSwitch: (pack: string) => void
  onNew: () => void
  onDuplicate: () => void
  onExport: () => void
  onImport: (file: File) => void
  onRebuild: () => void
  onClose?: () => void
}): React.JSX.Element {
  const picker = useRef<HTMLInputElement>(null)

  return (
    <div className="flex h-11 shrink-0 items-center gap-2 px-3">
      <Palette className="size-3.5 text-muted-foreground" />
      <span className="text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
        Brand
      </span>

      <Select value={pack} onValueChange={onSwitch}>
        <SelectTrigger size="sm" className="w-[180px] text-xs">
          <SelectValue placeholder="pack" />
        </SelectTrigger>
        <SelectContent>
          {packs.map((name) => (
            <SelectItem key={name} value={name} className="text-xs">
              {name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Button variant="ghost" size="sm" className="h-7 gap-1.5 text-xs" onClick={onNew}>
        <Plus className="size-3.5" />
        New pack…
      </Button>
      <Button variant="ghost" size="sm" className="h-7 gap-1.5 text-xs" onClick={onDuplicate}>
        <Copy className="size-3.5" />
        Duplicate
      </Button>
      <Button variant="ghost" size="sm" className="h-7 gap-1.5 text-xs" onClick={onExport}>
        <Download className="size-3.5" />
        Export
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 gap-1.5 text-xs"
        onClick={() => picker.current?.click()}
      >
        <Upload className="size-3.5" />
        Import
      </Button>
      <input
        ref={picker}
        type="file"
        accept="application/json,.json"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0]
          if (file) onImport(file)
          // Reset, so importing the same file twice fires twice.
          event.target.value = ''
        }}
      />

      <div className="ml-auto flex items-center gap-2">
        {saving && <span className="text-[11px] text-muted-foreground">saving…</span>}
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          title="Rebuild the specimen"
          onClick={onRebuild}
        >
          <RefreshCw className="size-3.5" />
        </Button>
        {onClose && (
          <Button variant="ghost" size="icon" className="size-7" title="Close" onClick={onClose}>
            <X className="size-3.5" />
          </Button>
        )}
      </div>
    </div>
  )
}

function PackDialog({
  mode,
  from,
  onClose,
  onCreate
}: {
  mode: 'new' | 'duplicate' | null
  from: string
  onClose: () => void
  onCreate: (name: string, from: string | null) => Promise<string | null>
}): React.JSX.Element {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)

  useEffect(() => {
    if (mode === null) return
    setName(mode === 'duplicate' ? `${from === 'default' ? 'brand' : from}-copy` : '')
    setFailed(null)
  }, [mode, from])

  const submit = async (): Promise<void> => {
    const clean = name.trim()
    if (!clean || clean.includes('/')) {
      setFailed('A pack name is a folder name — no slashes.')
      return
    }
    setBusy(true)
    const problem = await onCreate(clean, mode === 'duplicate' ? from : null)
    setBusy(false)
    if (problem) setFailed(problem)
    else onClose()
  }

  return (
    <Dialog open={mode !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="text-sm">
            {mode === 'duplicate' ? `Duplicate ${from}` : 'New brand pack'}
          </DialogTitle>
          <DialogDescription className="text-xs">
            {mode === 'duplicate'
              ? `Runs report-maker brand new <name> --from ${from}.`
              : 'Runs report-maker brand new <name>. It starts as a delta over the engine default.'}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="brand-pack-name" className="text-xs">
            Name
          </Label>
          <Input
            id="brand-pack-name"
            value={name}
            autoFocus
            spellCheck={false}
            placeholder="acme"
            className="h-8 font-mono text-xs"
            aria-invalid={failed !== null}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void submit()
            }}
          />
          <p className="font-mono text-[10px] text-muted-foreground">
            brand/{name.trim() || '<name>'}/brand.json
          </p>
          {failed && <p className="text-[11px] whitespace-pre-wrap text-destructive">{failed}</p>}
        </div>
        <DialogFooter>
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" disabled={busy} onClick={() => void submit()}>
            {busy && <Loader2 className="size-3 animate-spin" />}
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── the form ─────────────────────────────────────────────────────────────────

/** Colour keys, grouped the way a designer reads them. Presentation only: the
 *  groups are matched against whatever keys the pack has, and anything unmatched
 *  still gets a control under "Other". */
const COLOR_GROUPS: { title: string; match: (key: string) => boolean }[] = [
  { title: 'Accent', match: (key) => key.startsWith('accent') },
  { title: 'Inks', match: (key) => key.startsWith('ink') || key === 'text' },
  { title: 'Rules', match: (key) => key.startsWith('rule') || key.startsWith('border') },
  { title: 'Surfaces', match: (key) => key.startsWith('surface') || key.startsWith('background') },
  {
    title: 'Status',
    match: (key) =>
      /^(positive|negative|warning|caution|critical|danger|info|neutral|good|bad|error)/.test(key)
  }
]

function label(key: string): string {
  const words = key.replace(/[-_]/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : value === null || value === undefined ? fallback : String(value)
}

function stack(value: unknown): string[] {
  if (Array.isArray(value)) return value.filter((item): item is string => typeof item === 'string')
  return typeof value === 'string' ? [value] : []
}

function Form({
  tree,
  overrides,
  vault,
  fonts,
  onEdit
}: {
  tree: Tree
  overrides: ReadonlySet<string>
  vault: string
  fonts: string[]
  onEdit: (patch: BrandPatch) => void
}): React.JSX.Element {
  const colors = section(tree, 'colors')
  const sizes = section(tree, 'sizes')
  const space = section(tree, 'space')
  const margin = section(tree, 'page-margin')
  const defaults = section(tree, 'defaults')

  /** Put every overridden key under a prefix back to its inherited value. */
  const resetPrefix = (prefix: string) => (): void => {
    const patch: BrandPatch = {}
    for (const key of overrides) {
      if (key === prefix || key.startsWith(`${prefix}.`)) patch[key] = undefined
    }
    if (Object.keys(patch).length > 0) onEdit(patch)
  }

  const has = (prefix: string): boolean =>
    [...overrides].some((key) => key === prefix || key.startsWith(`${prefix}.`))

  const grouped = useMemo(() => {
    const keys = Object.keys(colors)
    const taken = new Set<string>()
    const groups = COLOR_GROUPS.map((group) => {
      const members = keys.filter((key) => !taken.has(key) && group.match(key))
      members.forEach((key) => taken.add(key))
      return { title: group.title, keys: members }
    }).filter((group) => group.keys.length > 0)
    const rest = keys.filter((key) => !taken.has(key))
    return rest.length > 0 ? [...groups, { title: 'Other', keys: rest }] : groups
  }, [colors])

  return (
    <>
      <Section
        icon={Building2}
        title="Organisation"
        note="Whose report this is. The logo is page furniture, not evidence — it is the one thing in a report that carries no citation."
        overridden={has('org')}
        onReset={resetPrefix('org')}
      >
        <TextField
          label="Name"
          hint="org.name"
          value={str(getAt(tree, 'org.name'))}
          inherited={!overrides.has('org.name')}
          onChange={(value) => onEdit({ 'org.name': value })}
          onReset={() => onEdit({ 'org.name': undefined })}
        />
        <TextField
          label="URL"
          hint="org.url"
          value={str(getAt(tree, 'org.url'))}
          placeholder="example.com"
          inherited={!overrides.has('org.url')}
          onChange={(value) => onEdit({ 'org.url': value || null })}
          onReset={() => onEdit({ 'org.url': undefined })}
        />
        <LogoField
          label="Logo"
          hint="org.logo"
          vault={vault}
          value={str(getAt(tree, 'org.logo'), '')}
          inherited={!overrides.has('org.logo')}
          onChange={(value) => onEdit({ 'org.logo': value })}
          onReset={() => onEdit({ 'org.logo': undefined })}
        />
        <LogoField
          label="Logo, inverse"
          hint="org.logo-inverse"
          vault={vault}
          value={str(getAt(tree, 'org.logo-inverse'), '')}
          inherited={!overrides.has('org.logo-inverse')}
          onChange={(value) => onEdit({ 'org.logo-inverse': value })}
          onReset={() => onEdit({ 'org.logo-inverse': undefined })}
        />
        <LengthField
          label="Logo width, cover"
          hint="org.logo-width"
          value={str(getAt(tree, 'org.logo-width'))}
          inherited={!overrides.has('org.logo-width')}
          onChange={(value) => onEdit({ 'org.logo-width': value })}
          onReset={() => onEdit({ 'org.logo-width': undefined })}
        />
        <LengthField
          label="Logo width, header"
          hint="org.logo-width-header"
          value={str(getAt(tree, 'org.logo-width-header'))}
          inherited={!overrides.has('org.logo-width-header')}
          onChange={(value) => onEdit({ 'org.logo-width-header': value })}
          onReset={() => onEdit({ 'org.logo-width-header': undefined })}
        />
      </Section>

      <Section
        icon={Palette}
        title="Colours"
        note="Every colour the design can use. Diagrams and figures read the same values, so a change here moves the whole document at once."
        overridden={has('colors')}
        onReset={resetPrefix('colors')}
      >
        {grouped.map((group) => (
          <div key={group.title} className="space-y-1.5">
            <p className="text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
              {group.title}
            </p>
            {group.keys.map((key) => (
              <ColorField
                key={key}
                label={label(key)}
                hint={`colors.${key}`}
                value={str(colors[key], '#000000')}
                inherited={!overrides.has(`colors.${key}`)}
                onChange={(hex) => onEdit({ [`colors.${key}`]: hex })}
                onReset={() => onEdit({ [`colors.${key}`]: undefined })}
              />
            ))}
          </div>
        ))}
        {grouped.length === 0 && <Empty>This pack defines no colours.</Empty>}
      </Section>

      <Section
        icon={TypeIcon}
        title="Type"
        note="Families are resolved by name at build time, on whichever machine builds the report — a family missing from this list can still be named."
        overridden={has('fonts') || has('sizes')}
        onReset={() => {
          resetPrefix('fonts')()
          resetPrefix('sizes')()
        }}
      >
        {Object.keys(section(tree, 'fonts')).map((key) => (
          <FontField
            key={key}
            label={label(key)}
            hint={`fonts.${key}`}
            families={fonts}
            value={stack(getAt(tree, `fonts.${key}`))}
            inherited={!overrides.has(`fonts.${key}`)}
            onChange={(next) => onEdit({ [`fonts.${key}`]: next })}
            onReset={() => onEdit({ [`fonts.${key}`]: undefined })}
          />
        ))}

        {Object.keys(sizes).length > 0 && (
          <div className="space-y-1.5 pt-1">
            <p className="text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
              Sizes
            </p>
            {Object.keys(sizes).map((key) => (
              <LengthField
                key={key}
                label={label(key)}
                hint={`sizes.${key}`}
                value={str(sizes[key])}
                inherited={!overrides.has(`sizes.${key}`)}
                onChange={(value) => onEdit({ [`sizes.${key}`]: value })}
                onReset={() => onEdit({ [`sizes.${key}`]: undefined })}
              />
            ))}
          </div>
        )}
      </Section>

      <Section
        icon={Ruler}
        title="Rhythm"
        note="The vertical scale and the page frame. These are the numbers that decide whether a page reads as dense or as generous."
        overridden={has('space') || has('page-margin')}
        onReset={() => {
          resetPrefix('space')()
          resetPrefix('page-margin')()
        }}
      >
        {Object.keys(space).length > 0 && (
          <div className="space-y-1.5">
            <p className="text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
              Space
            </p>
            {Object.keys(space).map((key) => (
              <LengthField
                key={key}
                label={label(key)}
                hint={`space.${key}`}
                value={str(space[key])}
                inherited={!overrides.has(`space.${key}`)}
                onChange={(value) => onEdit({ [`space.${key}`]: value })}
                onReset={() => onEdit({ [`space.${key}`]: undefined })}
              />
            ))}
          </div>
        )}
        {Object.keys(margin).length > 0 && (
          <div className="space-y-1.5 pt-1">
            <p className="text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
              Page margin
            </p>
            {Object.keys(margin).map((key) => (
              <LengthField
                key={key}
                label={label(key)}
                hint={`page-margin.${key}`}
                value={str(margin[key])}
                inherited={!overrides.has(`page-margin.${key}`)}
                onChange={(value) => onEdit({ [`page-margin.${key}`]: value })}
                onReset={() => onEdit({ [`page-margin.${key}`]: undefined })}
              />
            ))}
          </div>
        )}
      </Section>

      <Section
        icon={Sliders}
        title="Defaults"
        note="What a new report inherits when its own front matter says nothing."
        overridden={has('defaults')}
        onReset={resetPrefix('defaults')}
      >
        {Object.keys(defaults).map((key) => (
          <TextField
            key={key}
            label={label(key)}
            hint={`defaults.${key}`}
            value={str(defaults[key])}
            inherited={!overrides.has(`defaults.${key}`)}
            onChange={(value) => onEdit({ [`defaults.${key}`]: value })}
            onReset={() => onEdit({ [`defaults.${key}`]: undefined })}
          />
        ))}
        {Object.keys(defaults).length === 0 && <Empty>This pack sets no defaults.</Empty>}
      </Section>
    </>
  )
}

function Section({
  icon: Icon,
  title,
  note,
  overridden,
  onReset,
  children
}: {
  icon: typeof Palette
  title: string
  note: string
  overridden: boolean
  onReset: () => void
  children: React.ReactNode
}): React.JSX.Element {
  return (
    <section className="space-y-2">
      <div className="flex items-center gap-2">
        <Icon className="size-3.5 text-muted-foreground" />
        <h2 className="text-xs font-medium">{title}</h2>
        <Button
          variant="ghost"
          size="xs"
          className="ml-auto gap-1 text-[11px] text-muted-foreground"
          disabled={!overridden}
          onClick={onReset}
          title={`Reset every ${title.toLowerCase()} field to the default pack`}
        >
          <RotateCcw />
          Reset section
        </Button>
      </div>
      <p className="text-[11px] text-muted-foreground">{note}</p>
      <div className="space-y-1.5 rounded-md border border-border p-3">{children}</div>
    </section>
  )
}

/** Label, "default" chip and revert button — the frame `ColorField` draws for
 *  itself, shared by every other kind of control so the column reads as one. */
function FieldShell({
  label: text,
  hint,
  inherited,
  onReset,
  htmlFor,
  children
}: {
  label: string
  hint?: string
  inherited?: boolean
  onReset?: () => void
  htmlFor?: string
  children: React.ReactNode
}): React.JSX.Element {
  return (
    <div className="flex items-start gap-2 py-0.5">
      <div className="min-w-0 flex-1 pt-1">
        <Label htmlFor={htmlFor} className={cn('text-xs', inherited && 'text-muted-foreground')}>
          <span className="truncate">{text}</span>
          {inherited && (
            <Badge variant="outline" className="px-1 py-0 text-[10px]">
              default
            </Badge>
          )}
        </Label>
        {hint && <p className="truncate font-mono text-[10px] text-muted-foreground">{hint}</p>}
      </div>
      <div className="flex w-[58%] shrink-0 flex-col gap-1">{children}</div>
      {onReset && (
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          className="mt-1 shrink-0"
          disabled={inherited}
          onClick={onReset}
          title="Reset to the default pack's value"
        >
          <RotateCcw />
          <span className="sr-only">Reset {text}</span>
        </Button>
      )}
    </div>
  )
}

function TextField({
  label: text,
  hint,
  value,
  placeholder,
  inherited,
  onChange,
  onReset
}: {
  label: string
  hint: string
  value: string
  placeholder?: string
  inherited: boolean
  onChange: (value: string) => void
  onReset: () => void
}): React.JSX.Element {
  const id = `brand-${hint.replace(/\./g, '-')}`
  return (
    <FieldShell label={text} hint={hint} inherited={inherited} onReset={onReset} htmlFor={id}>
      <Input
        id={id}
        value={value}
        placeholder={placeholder}
        spellCheck={false}
        className="h-7 px-2 text-xs"
        onChange={(event) => onChange(event.target.value)}
      />
    </FieldShell>
  )
}

// ── lengths ──────────────────────────────────────────────────────────────────

/** Slider bounds per unit. Fixed rather than derived from the current value,
 *  because a range that moves as you drag makes the handle feel weightless. */
const RANGES: Record<string, { max: number; step: number }> = {
  pt: { max: 72, step: 0.2 },
  mm: { max: 80, step: 0.5 },
  cm: { max: 8, step: 0.05 },
  in: { max: 4, step: 0.05 },
  em: { max: 8, step: 0.05 },
  px: { max: 96, step: 1 }
}

function parseLength(value: string): { n: number; unit: string } | null {
  const match = /^\s*(-?\d+(?:\.\d+)?)\s*(pt|mm|cm|in|em|px)\s*$/i.exec(value)
  if (!match) return null
  return { n: Number(match[1]), unit: match[2].toLowerCase() }
}

/**
 * A length: `"9.8pt"`, `"26mm"`.
 *
 * The slider is the point — a size you can drag is a size you can judge against
 * the page beside it — but the text stays authoritative. A pack may hold a unit
 * this control has no range for, and typing it must keep working, so an
 * unparseable value simply loses its slider rather than its value.
 */
function LengthField({
  label: text,
  hint,
  value,
  inherited,
  onChange,
  onReset
}: {
  label: string
  hint: string
  value: string
  inherited: boolean
  onChange: (value: string) => void
  onReset: () => void
}): React.JSX.Element {
  const id = `brand-${hint.replace(/\./g, '-')}`
  const parsed = parseLength(value)
  const range = parsed ? RANGES[parsed.unit] : undefined

  return (
    <FieldShell label={text} hint={hint} inherited={inherited} onReset={onReset} htmlFor={id}>
      <div className="flex items-center gap-2">
        {parsed && range && (
          <Slider
            aria-label={text}
            value={[parsed.n]}
            min={0}
            max={range.max}
            step={range.step}
            className="flex-1"
            onValueChange={([next]) =>
              onChange(`${Number(next.toFixed(2))}${parsed.unit}`)
            }
          />
        )}
        <Input
          id={id}
          value={value}
          spellCheck={false}
          aria-invalid={value.trim() !== '' && parsed === null}
          className={cn('h-7 shrink-0 px-2 font-mono text-xs', parsed && range ? 'w-20' : 'w-full')}
          onChange={(event) => onChange(event.target.value)}
        />
      </div>
    </FieldShell>
  )
}

// ── fonts ────────────────────────────────────────────────────────────────────

/**
 * A font stack. The first family is the one that will be used; the rest are what
 * a machine without it falls back to, which is why they are editable as plain
 * text rather than hidden behind the picker.
 */
function FontField({
  label: text,
  hint,
  families,
  value,
  inherited,
  onChange,
  onReset
}: {
  label: string
  hint: string
  families: string[]
  value: string[]
  inherited: boolean
  onChange: (value: string[]) => void
  onReset: () => void
}): React.JSX.Element {
  const [open, setOpen] = useState(false)
  const primary = value[0] ?? ''
  const rest = value.slice(1)

  return (
    <FieldShell label={text} hint={hint} inherited={inherited} onReset={onReset}>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-expanded={open}
            className="h-7 w-full justify-between px-2 text-xs font-normal"
          >
            <span className="truncate" style={{ fontFamily: primary ? `"${primary}"` : undefined }}>
              {primary || 'Choose a family'}
            </span>
            <ChevronsUpDown className="size-3 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-72 p-0" align="start">
          <Command>
            <CommandInput placeholder="Search families…" className="h-8 text-xs" />
            <CommandList>
              <CommandEmpty className="py-4 text-center text-xs text-muted-foreground">
                No family by that name. Type it into the fallbacks to use it anyway.
              </CommandEmpty>
              <CommandGroup>
                {families.map((family) => (
                  <CommandItem
                    key={family}
                    value={family}
                    className="text-xs"
                    onSelect={(selected) => {
                      onChange([selected, ...rest])
                      setOpen(false)
                    }}
                  >
                    <Check className={cn('size-3', family === primary ? 'opacity-100' : 'opacity-0')} />
                    <span className="truncate" style={{ fontFamily: `"${family}"` }}>
                      {family}
                    </span>
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
      <Input
        value={rest.join(', ')}
        spellCheck={false}
        placeholder="fallbacks, comma separated"
        className="h-6 px-2 text-[11px]"
        aria-label={`${text} fallbacks`}
        onChange={(event) =>
          onChange([
            primary,
            ...event.target.value
              .split(',')
              .map((item) => item.trim())
              .filter(Boolean)
          ])
        }
      />
    </FieldShell>
  )
}

// ── logo ─────────────────────────────────────────────────────────────────────

const IMAGE = /\.(svg|png|jpe?g|webp)$/i

function images(nodes: Node[], vault: string): string[] {
  const found: string[] = []
  const walk = (list: Node[]): void => {
    for (const node of list) {
      if (node.kind === 'dir') walk(node.children ?? [])
      else if (IMAGE.test(node.name)) {
        // Project-absolute, the way a report names its own files: a brand pack
        // that pointed outside the vault would not survive being shared.
        found.push('/' + node.path.slice(vault.length).replace(/^\//, ''))
      }
    }
  }
  walk(nodes)
  return found
}

/**
 * The logo, picked from the vault.
 *
 * A file dialog would let you pick an image the build cannot reach: `org.logo` is
 * a project-absolute path, resolved inside the vault at build time, so an image
 * anywhere else is a broken cover. The vault's own images are the real set of
 * choices, and the field stays free text for one that is not there yet.
 */
function LogoField({
  label: text,
  hint,
  vault,
  value,
  inherited,
  onChange,
  onReset
}: {
  label: string
  hint: string
  vault: string
  value: string
  inherited: boolean
  onChange: (value: string | null) => void
  onReset: () => void
}): React.JSX.Element {
  const [open, setOpen] = useState(false)
  const [found, setFound] = useState<string[]>([])

  useEffect(() => {
    if (!open) return
    let alive = true
    void window.api.files
      .tree(vault)
      .then((nodes) => alive && setFound(images(nodes, vault)))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [open, vault])

  return (
    <FieldShell label={text} hint={hint} inherited={inherited} onReset={onReset}>
      <div className="flex items-center gap-1">
        <Input
          value={value}
          spellCheck={false}
          placeholder="/brand/assets/logo.svg"
          aria-label={text}
          className="h-7 px-2 font-mono text-[11px]"
          onChange={(event) => onChange(event.target.value || null)}
        />
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <Button variant="outline" size="icon-sm" className="size-7 shrink-0" title="Pick an image from the vault">
              <ImageIcon className="size-3.5" />
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-80 p-0" align="end">
            <Command>
              <CommandInput placeholder="Search images…" className="h-8 text-xs" />
              <CommandList>
                <CommandEmpty className="py-4 text-center text-xs text-muted-foreground">
                  No image files in this vault.
                </CommandEmpty>
                <CommandGroup>
                  <CommandItem
                    value="__none__"
                    className="text-xs"
                    onSelect={() => {
                      onChange(null)
                      setOpen(false)
                    }}
                  >
                    <X className="size-3" />
                    No logo — set the name in display type
                  </CommandItem>
                  {found.map((path) => (
                    <CommandItem
                      key={path}
                      value={path}
                      className="font-mono text-[11px]"
                      onSelect={() => {
                        onChange(path)
                        setOpen(false)
                      }}
                    >
                      <Check className={cn('size-3', path === value ? 'opacity-100' : 'opacity-0')} />
                      <span className="truncate">{path}</span>
                    </CommandItem>
                  ))}
                </CommandGroup>
              </CommandList>
            </Command>
          </PopoverContent>
        </Popover>
      </div>
    </FieldShell>
  )
}

// ── the specimen ─────────────────────────────────────────────────────────────

function duration(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

/** Milliseconds since `active` last became true, ticking. A build that takes
 *  eight seconds should say so while it is happening, not afterwards. */
function useElapsed(active: boolean): number {
  const [ms, setMs] = useState(0)
  useEffect(() => {
    if (!active) return
    const started = performance.now()
    setMs(0)
    const timer = setInterval(() => setMs(performance.now() - started), 100)
    return () => clearInterval(timer)
  }, [active])
  return ms
}

function Specimen({
  pack,
  state,
  onRebuild
}: {
  pack: string
  state: ReturnType<typeof usePreview>
  onRebuild: () => void
}): React.JSX.Element {
  const elapsed = useElapsed(state.building)
  const shown = state.pages.length > 0 ? state.pages : state.previous
  const under = state.pages.length > 0 ? state.previous : []

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-8 shrink-0 items-center gap-2 px-3 text-[11px] text-muted-foreground">
        <span className="font-mono">brand preview --pack {pack}</span>
        {state.building ? (
          <span className="flex items-center gap-1.5">
            <Loader2 className="size-3 animate-spin" />
            {duration(Math.round(elapsed))}
          </span>
        ) : (
          state.ms !== null && <span>rendered in {duration(state.ms)}</span>
        )}
        {state.error && (
          <Badge variant="outline" className="gap-1 border-destructive/40 px-1 py-0 text-[10px] text-destructive">
            <AlertTriangle className="size-2.5" />
            build failed
          </Badge>
        )}
      </div>
      <Separator />

      <div className="relative min-h-0 flex-1 bg-muted/40">
        <ScrollArea className="h-full">
          <div className="flex flex-col items-center gap-4 p-4">
            {state.error && (
              <Notice tone="error" title="brand preview failed" className="w-full">
                {state.command ? `${state.command}\n\n${state.error}` : state.error}
              </Notice>
            )}

            {shown.map((url, index) => (
              <div
                key={`${state.generation}-${index}`}
                className="relative w-full max-w-[620px] overflow-hidden rounded-sm border border-border shadow-sm"
              >
                {under[index] && (
                  <img
                    src={under[index]}
                    alt=""
                    aria-hidden
                    className="absolute inset-0 h-full w-full"
                  />
                )}
                <Page src={url} page={index + 1} pack={pack} />
              </div>
            ))}

            {shown.length === 0 && state.building && (
              <Skeleton className="aspect-[1/1.414] w-full max-w-[620px]" />
            )}

            {shown.length === 0 && !state.building && !state.error && (
              <div className="flex flex-col items-center gap-3 py-16 text-center text-xs text-muted-foreground">
                <p>No specimen yet.</p>
                <Button variant="outline" size="sm" className="gap-1.5 text-xs" onClick={onRebuild}>
                  <RefreshCw className="size-3.5" />
                  Render it
                </Button>
              </div>
            )}
          </div>
        </ScrollArea>

        {/* The overlay dims, it does not cover: the whole point is to watch the
            page you are changing, including while it is being rebuilt. */}
        {state.building && shown.length > 0 && (
          <div className="pointer-events-none absolute inset-0 flex items-start justify-center bg-background/20">
            <div className="mt-4 flex items-center gap-2 rounded-full border border-border bg-background/90 px-3 py-1 text-[11px] shadow-sm">
              <Loader2 className="size-3 animate-spin" />
              rebuilding
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

/** One page, faded in over whatever was underneath it. */
function Page({ src, page, pack }: { src: string; page: number; pack: string }): React.JSX.Element {
  const [loaded, setLoaded] = useState(false)
  return (
    <img
      src={src}
      alt={`${pack} specimen, page ${page}`}
      onLoad={() => setLoaded(true)}
      className={cn(
        'relative block h-auto w-full transition-opacity duration-300',
        loaded ? 'opacity-100' : 'opacity-0'
      )}
    />
  )
}

// ── small parts ──────────────────────────────────────────────────────────────

function Notice({
  tone,
  title,
  children,
  className
}: {
  tone: 'error' | 'warn'
  title: string
  children: React.ReactNode
  className?: string
}): React.JSX.Element {
  return (
    <Card
      className={cn(
        'gap-1 border-l-2 px-3 py-3',
        tone === 'error' ? 'border-l-destructive' : 'border-l-muted-foreground',
        className
      )}
    >
      <div className="flex items-center gap-2">
        <AlertTriangle
          className={cn('size-3.5', tone === 'error' ? 'text-destructive' : 'text-muted-foreground')}
        />
        <p className="text-xs font-medium">{title}</p>
      </div>
      <pre className="overflow-x-auto font-mono text-[11px] whitespace-pre-wrap text-muted-foreground">
        {children}
      </pre>
    </Card>
  )
}

function Empty({ children }: { children: React.ReactNode }): React.JSX.Element {
  return <p className="py-2 text-[11px] text-muted-foreground">{children}</p>
}

function FormSkeleton(): React.JSX.Element {
  return (
    <div className="space-y-4">
      {[0, 1, 2].map((index) => (
        <div key={index} className="space-y-2">
          <Skeleton className="h-3 w-24" />
          <div className="space-y-1.5 rounded-md border border-border p-3">
            <Skeleton className="h-7 w-full" />
            <Skeleton className="h-7 w-full" />
            <Skeleton className="h-7 w-full" />
          </div>
        </div>
      ))}
    </div>
  )
}
