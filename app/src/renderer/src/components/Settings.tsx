import { useEffect, useId, useState } from 'react'
import {
  AlertTriangle,
  GitBranch,
  Hammer,
  Info,
  Monitor,
  Moon,
  Palette,
  RotateCcw,
  Sun,
  Type
} from 'lucide-react'
import type { DeepPartial, GitState, Settings as Prefs } from '../../../shared/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Slider } from '@/components/ui/slider'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ColorField } from '@/components/ui/color-field'
import {
  DEFAULT_SETTINGS,
  MONO_FONTS,
  SYNTAX_THEMES,
  TYPST_SAMPLE,
  fontStack,
  syntaxColor,
  useSettings,
  type SyntaxTheme
} from '@/lib/settings'
import { cn } from '@/lib/utils'

type Props = {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** The active vault. Only the Git section needs it — to say, in words, which
   *  branch and remote an auto-push would publish to. */
  vault: string | null
}

/**
 * Preferences.
 *
 * A dialog rather than a route, deliberately. Preferences are a detour and not a
 * destination: you come here because the font is too small in the file you were
 * reading, and you want that file back the instant you are done. A pane would
 * unmount the editor, lose the scroll position and the selection, and make ⎋ do
 * nothing. It also matches what ⌘, does in every other Mac app — a panel over
 * your work, not instead of it.
 *
 * There is no Save button. Every control writes through `useSettings`, which
 * applies the change on the next frame and persists it once your hand stops.
 */
export function Settings({ open, onOpenChange, vault }: Props): React.JSX.Element {
  const { settings, update, reset } = useSettings()
  const [section, setSection] = useState('appearance')

  // Mounting this component is enough to make ⌘, work. It only ever opens, never
  // toggles, so a duplicate binding in the shell is harmless.
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if ((event.metaKey || event.ctrlKey) && event.key === ',') {
        event.preventDefault()
        onOpenChange(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onOpenChange])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="flex h-[min(680px,88vh)] w-[min(60rem,calc(100%-3rem))] gap-0 overflow-hidden p-0 sm:max-w-4xl"
      >
        <DialogDescription className="sr-only">
          Appearance, editor, build, git and about. Changes take effect immediately.
        </DialogDescription>

        <Tabs
          orientation="vertical"
          value={section}
          onValueChange={setSection}
          className="min-h-0 w-full gap-0"
        >
          <div className="flex w-52 shrink-0 flex-col border-r border-border bg-muted/30">
            <div className="flex h-12 shrink-0 items-center px-4">
              <DialogTitle className="text-sm">Settings</DialogTitle>
            </div>
            <Separator />
            <TabsList className="w-full flex-col items-stretch justify-start gap-0.5 rounded-none bg-transparent p-2">
              <NavItem value="appearance" icon={<Palette />} label="Appearance" />
              <NavItem value="editor" icon={<Type />} label="Editor" />
              <NavItem value="build" icon={<Hammer />} label="Build" />
              <NavItem value="git" icon={<GitBranch />} label="Git" />
              <NavItem value="about" icon={<Info />} label="About" />
            </TabsList>
            <div className="mt-auto p-3">
              <Button
                variant="ghost"
                size="sm"
                className="w-full justify-start text-xs text-muted-foreground"
                onClick={() => onOpenChange(false)}
              >
                Done
                <kbd className="ml-auto text-[10px]">⎋</kbd>
              </Button>
            </div>
          </div>

          <ScrollArea className="min-w-0 flex-1">
            <div className="px-6 py-5">
              <TabsContent value="appearance">
                <Appearance settings={settings} update={update} />
              </TabsContent>
              <TabsContent value="editor">
                <EditorPrefs settings={settings} update={update} open={open} />
              </TabsContent>
              <TabsContent value="build">
                <Build settings={settings} update={update} />
              </TabsContent>
              <TabsContent value="git">
                <Git settings={settings} update={update} vault={vault} open={open} />
              </TabsContent>
              <TabsContent value="about">
                <About open={open} onReset={reset} />
              </TabsContent>
            </div>
          </ScrollArea>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}

type Update = (patch: DeepPartial<Prefs>) => void

// ── sections ─────────────────────────────────────────────────────────────────

function Appearance({ settings, update }: { settings: Prefs; update: Update }): React.JSX.Element {
  const { theme, accent, density } = settings.appearance
  return (
    <Section
      title="Appearance"
      blurb="How the window looks. The report itself is dressed by its brand pack, not by this."
    >
      <Row label="Theme" hint="Follow the system, or pin it.">
        <Segmented
          label="Theme"
          value={theme}
          onChange={(next) => update({ appearance: { theme: next } })}
          options={[
            { value: 'system', label: 'System', icon: <Monitor className="size-3.5" /> },
            { value: 'light', label: 'Light', icon: <Sun className="size-3.5" /> },
            { value: 'dark', label: 'Dark', icon: <Moon className="size-3.5" /> }
          ]}
        />
      </Row>

      <Preview />

      <Separator />

      <div className="max-w-md">
        <ColorField
          label="Accent"
          hint="appearance.accent"
          value={accent}
          onChange={(hex) => update({ appearance: { accent: hex } })}
          inherited={accent === DEFAULT_SETTINGS.appearance.accent}
          onReset={() =>
            update({ appearance: { accent: DEFAULT_SETTINGS.appearance.accent } })
          }
        />
        <p className="mt-1.5 text-[11px] text-muted-foreground">
          Buttons, switches, focus rings and citation markers take this colour.
        </p>
      </div>

      <Separator />

      <Row label="Density" hint="Compact tightens the type and the spacing of the app chrome by about a tenth. It does not touch the report.">
        <Segmented
          label="Density"
          value={density}
          onChange={(next) => update({ appearance: { density: next } })}
          options={[
            { value: 'comfortable', label: 'Comfortable' },
            { value: 'compact', label: 'Compact' }
          ]}
        />
      </Row>
    </Section>
  )
}

function EditorPrefs({
  settings,
  update,
  open
}: {
  settings: Prefs
  update: Update
  open: boolean
}): React.JSX.Element {
  const editor = settings.editor
  const fonts = useSystemFonts(open)

  // Most of a system font list has no fixed advance width, so the shortlist goes
  // first — filtered to what this machine actually has, when we know.
  const installed = new Set(fonts)
  const curated = fonts.length ? MONO_FONTS.filter((name) => installed.has(name)) : MONO_FONTS
  const rest = fonts.filter((name) => !curated.includes(name))
  const chosen = editor.fontFamily.trim()
  // A settings file can name a font this machine does not have; showing it as a
  // blank select would be a lie about what the editor is rendering with.
  const missing = chosen && !curated.includes(chosen) && !rest.includes(chosen) ? chosen : null

  return (
    <Section title="Editor" blurb="Typography and behaviour of the writing surface.">
      <div>
        <div className="mb-1.5 flex items-baseline justify-between">
          <span className="text-xs font-medium">Preview</span>
          <span className="text-[11px] text-muted-foreground">
            {SYNTAX_THEMES[editor.syntaxTheme].description}
          </span>
        </div>
        <SyntaxSample
          theme={editor.syntaxTheme}
          fontFamily={editor.fontFamily}
          fontSize={editor.fontSize}
          lineHeight={editor.lineHeight}
          lineNumbers={editor.lineNumbers}
        />
      </div>

      <Separator />

      <Row label="Font family" hint={chosen || "The app's mono stack"}>
        <Select
          value={chosen === '' ? APP_FONT : chosen}
          onValueChange={(value) =>
            update({ editor: { fontFamily: value === APP_FONT ? '' : value } })
          }
        >
          <SelectTrigger size="sm" className="w-56 text-xs" aria-label="Editor font family">
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="max-h-72">
            <SelectItem value={APP_FONT}>App default (SF Mono, Menlo)</SelectItem>
            {missing && (
              <SelectGroup>
                <SelectLabel>Not installed here</SelectLabel>
                <SelectItem value={missing}>{missing}</SelectItem>
              </SelectGroup>
            )}
            {curated.length > 0 && (
              <SelectGroup>
                <SelectLabel>Monospace</SelectLabel>
                {curated.map((name) => (
                  <SelectItem key={name} value={name}>
                    {name}
                  </SelectItem>
                ))}
              </SelectGroup>
            )}
            {rest.length > 0 && (
              <SelectGroup>
                <SelectLabel>Installed on this machine</SelectLabel>
                {rest.map((name) => (
                  <SelectItem key={name} value={name}>
                    {name}
                  </SelectItem>
                ))}
              </SelectGroup>
            )}
          </SelectContent>
        </Select>
      </Row>

      <SliderRow
        label="Font size"
        value={editor.fontSize}
        display={`${editor.fontSize} px`}
        min={10}
        max={24}
        step={1}
        onChange={(value) => update({ editor: { fontSize: value } })}
      />

      <SliderRow
        label="Line height"
        value={editor.lineHeight}
        display={editor.lineHeight.toFixed(2)}
        min={1.1}
        max={2.4}
        step={0.05}
        onChange={(value) => update({ editor: { lineHeight: Number(value.toFixed(2)) } })}
      />

      <Row label="Tab size" hint="Spaces an indent is worth.">
        <Select
          value={String(editor.tabSize)}
          onValueChange={(value) => update({ editor: { tabSize: Number(value) } })}
        >
          <SelectTrigger size="sm" className="w-24 text-xs" aria-label="Tab size">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {[2, 4, 8].map((size) => (
              <SelectItem key={size} value={String(size)}>
                {size}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Row>

      <Row label="Syntax theme" hint="Colours for citations, assessments and helpers.">
        <Select
          value={editor.syntaxTheme}
          onValueChange={(value) =>
            update({ editor: { syntaxTheme: value as SyntaxTheme } })
          }
        >
          <SelectTrigger size="sm" className="w-48 text-xs" aria-label="Syntax theme">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(Object.keys(SYNTAX_THEMES) as SyntaxTheme[]).map((id) => (
              <SelectItem key={id} value={id}>
                {SYNTAX_THEMES[id].label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Row>

      <Separator />

      <SwitchRow
        label="Line numbers"
        hint="A finding says path:line — the gutter is how you get there."
        checked={editor.lineNumbers}
        onChange={(value) => update({ editor: { lineNumbers: value } })}
      />
      <SwitchRow
        label="Wrap long lines"
        hint="A paragraph of prose is one line to Typst."
        checked={editor.wordWrap}
        onChange={(value) => update({ editor: { wordWrap: value } })}
      />
      <SwitchRow
        label="Highlight the active line"
        checked={editor.highlightActiveLine}
        onChange={(value) => update({ editor: { highlightActiveLine: value } })}
      />
      <SwitchRow
        label="Match brackets"
        hint="Helper calls nest deeply; this shows where one ends."
        checked={editor.bracketMatching}
        onChange={(value) => update({ editor: { bracketMatching: value } })}
      />
      <SwitchRow
        label="Evidence rail"
        hint="A stripe down the right edge: cited, assessed, or unmarked, line by line."
        checked={editor.evidenceRail}
        onChange={(value) => update({ editor: { evidenceRail: value } })}
      />
      <SwitchRow
        label="Lint gutter"
        hint="Markers from the last check run, in the left gutter."
        checked={editor.lintGutter}
        onChange={(value) => update({ editor: { lintGutter: value } })}
      />
    </Section>
  )
}

const AUTOSAVE = [
  { value: 'off', label: 'Off', ms: null },
  { value: '500', label: 'After 500 ms', ms: 500 },
  { value: '1000', label: 'After 1 second', ms: 1000 },
  { value: '2000', label: 'After 2 seconds', ms: 2000 },
  { value: '5000', label: 'After 5 seconds', ms: 5000 }
]

function Build({ settings, update }: { settings: Prefs; update: Update }): React.JSX.Element {
  const build = settings.build
  const autosave = AUTOSAVE.find((option) => option.ms === build.autoSaveMs) ?? AUTOSAVE[0]

  return (
    <Section
      title="Build"
      blurb="When the app saves, rebuilds, and re-runs the citation rule on your behalf."
    >
      <Row label="Autosave" hint="Idle time before an edited file is written.">
        <Select
          value={autosave.value}
          onValueChange={(value) =>
            update({
              build: { autoSaveMs: AUTOSAVE.find((o) => o.value === value)?.ms ?? null }
            })
          }
        >
          <SelectTrigger size="sm" className="w-44 text-xs" aria-label="Autosave">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {AUTOSAVE.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Row>

      <SwitchRow
        label="Build on save"
        hint="Runs report-maker all on the open report after every write."
        checked={build.buildOnSave}
        onChange={(value) => update({ build: { buildOnSave: value } })}
      />

      <SwitchRow
        label="Watch mode"
        hint="Keeps report-maker watch running so the PDF follows the file without a keystroke."
        checked={build.watch}
        onChange={(value) => update({ build: { watch: value } })}
      />

      <SliderRow
        label="Check when idle"
        hint="How long after the last keystroke the findings and the evidence rail refresh."
        value={build.checkOnIdleMs}
        display={`${build.checkOnIdleMs} ms`}
        min={200}
        max={3000}
        step={100}
        onChange={(value) => update({ build: { checkOnIdleMs: value } })}
      />
    </Section>
  )
}

function Git({
  settings,
  update,
  vault,
  open
}: {
  settings: Prefs
  update: Update
  vault: string | null
  open: boolean
}): React.JSX.Element {
  const git = settings.git
  const state = useGitState(vault, open)
  const [confirming, setConfirming] = useState(false)

  // Why auto-push cannot be turned on, in the order the user can fix it.
  const blocked = !git.autoCommit
    ? 'Auto-commit has to be on — there is nothing to push without a commit.'
    : !vault
      ? 'No vault is open.'
      : state === null
        ? 'Reading this vault’s git state…'
        : !state.repo
          ? 'This vault is not a git repository.'
          : state.branch === null
            ? 'HEAD is detached — check out a branch first.'
            : !state.upstream
              ? `This branch has no upstream. Set one with git push -u origin ${state.branch}.`
              : null

  const destination = state?.upstream ?? (state?.remote ? `${state.remote}/…` : 'the remote')

  return (
    <Section
      title="Git"
      blurb="The app never runs git itself — it asks report-maker sync, which is where the safety rules live."
    >
      <div className="rounded-md border border-border px-3 py-2.5 text-xs">
        {!vault ? (
          <span className="text-muted-foreground">No vault open.</span>
        ) : state === null ? (
          <span className="text-muted-foreground">Reading git state…</span>
        ) : !state.repo ? (
          <span className="text-muted-foreground">
            <span className="font-mono">{vault}</span> is not a git repository.
          </span>
        ) : (
          <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1">
            <dt className="text-muted-foreground">Branch</dt>
            <dd className="font-mono">{state.branch ?? 'detached HEAD'}</dd>
            <dt className="text-muted-foreground">Upstream</dt>
            <dd className="font-mono">{state.upstream ?? 'none'}</dd>
            <dt className="text-muted-foreground">Remote</dt>
            <dd className="font-mono">{state.remote ?? 'none'}</dd>
            <dt className="text-muted-foreground">Uncommitted</dt>
            <dd>
              {state.dirty.length} file{state.dirty.length === 1 ? '' : 's'}
              {state.ahead > 0 && ` · ${state.ahead} ahead`}
              {state.behind > 0 && ` · ${state.behind} behind`}
            </dd>
          </dl>
        )}
      </div>

      <SwitchRow
        label="Commit automatically"
        hint="After a successful build, commit what changed inside the vault."
        checked={git.autoCommit}
        onChange={(value) =>
          // Turning commits off takes pushing with it: pushing every save without
          // committing every save is not a state this app should be able to reach.
          update({ git: value ? { autoCommit: true } : { autoCommit: false, autoPush: false } })
        }
      />

      <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-3">
        <SwitchRow
          label="Push automatically"
          badge={
            <Badge variant="outline" className="gap-1 border-destructive/50 text-[10px]">
              <AlertTriangle className="size-3" />
              publishes your work
            </Badge>
          }
          hint={`Every commit this app makes is sent to ${destination} without asking. Anyone with access to that remote sees the draft as you write it, and a push cannot be taken back.`}
          checked={git.autoPush}
          disabled={blocked !== null}
          onChange={(value) => {
            if (value) setConfirming(true)
            else update({ git: { autoPush: false } })
          }}
        />
        {blocked !== null && (
          <p className="mt-2 text-[11px] text-muted-foreground">{blocked}</p>
        )}
        {confirming && blocked === null && (
          <div className="mt-3 rounded-md border border-destructive/50 bg-background p-3">
            <p className="text-xs">
              Publish every save to <span className="font-mono">{destination}</span>?
            </p>
            <p className="mt-1 text-[11px] text-muted-foreground">
              A report is usually half-wrong halfway through. Turn this on only for a vault whose
              remote you are happy to have watch you think.
            </p>
            <div className="mt-2.5 flex gap-2">
              <Button
                size="xs"
                variant="destructive"
                onClick={() => {
                  update({ git: { autoPush: true } })
                  setConfirming(false)
                }}
              >
                Turn on auto-push
              </Button>
              <Button size="xs" variant="ghost" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </div>
          </div>
        )}
      </div>

      <SliderRow
        label="Debounce"
        hint="Quiet time after the last save before a commit is made."
        value={git.debounceMs}
        display={`${(git.debounceMs / 1000).toFixed(1)} s`}
        min={1000}
        max={30000}
        step={500}
        onChange={(value) => update({ git: { debounceMs: value } })}
      />

      <TemplateField
        value={git.messageTemplate}
        onChange={(value) => update({ git: { messageTemplate: value } })}
      />
    </Section>
  )
}

function TemplateField({
  value,
  onChange
}: {
  value: string
  onChange: (value: string) => void
}): React.JSX.Element {
  const id = useId()
  return (
    <div>
      <Label htmlFor={id} className="text-xs">
        Commit message
      </Label>
      <Input
        id={id}
        value={value}
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1.5 h-8 font-mono text-xs"
      />
      {/* The legend names the tokens; it does not render a sample message. The
          engine does the substitution at commit time, and a preview here would be
          a second implementation of it, free to drift. */}
      <dl className="mt-2 grid grid-cols-[4.5rem_1fr] gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
        <dt className="font-mono">{'{n}'}</dt>
        <dd>how many files the commit touches</dd>
        <dt className="font-mono">{'{date}'}</dt>
        <dd>the commit time, as YYYY-MM-DD HH:MM</dd>
        <dt className="font-mono">{'{vault}'}</dt>
        <dd>the vault folder’s name</dd>
      </dl>
    </div>
  )
}

function About({
  open,
  onReset
}: {
  open: boolean
  onReset: () => Promise<void>
}): React.JSX.Element {
  const [engine, setEngine] = useState('locating…')
  const [confirming, setConfirming] = useState(false)
  const versions = readVersions()

  useEffect(() => {
    if (!open) return
    let stale = false
    window.api.engine
      .where()
      .then((path) => !stale && setEngine(path))
      .catch((error) => !stale && setEngine(String(error)))
    return () => {
      stale = true
    }
  }, [open])

  return (
    <Section title="About" blurb="What this window is running, and where it found the engine.">
      <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-2 text-xs">
        <dt className="text-muted-foreground">Engine</dt>
        <dd className="font-mono break-all select-text">{engine}</dd>
        <dt className="text-muted-foreground">App</dt>
        <dd className="font-mono">{versions.app ?? 'unknown'}</dd>
        <dt className="text-muted-foreground">Electron</dt>
        <dd className="font-mono">{versions.electron ?? 'unknown'}</dd>
        <dt className="text-muted-foreground">Chromium</dt>
        <dd className="font-mono">{versions.chromium ?? 'unknown'}</dd>
        <dt className="text-muted-foreground">Platform</dt>
        <dd className="font-mono">{window.api?.platform ?? 'unknown'}</dd>
      </dl>

      <p className="text-[11px] text-muted-foreground">
        Every question about a vault is answered by running that binary. If the path above is wrong,
        set <span className="font-mono">REPORT_MAKER_BIN</span> or{' '}
        <span className="font-mono">REPORT_MAKER_ROOT</span> and reopen the app.
      </p>

      <Separator />

      <div>
        <p className="text-xs font-medium">Reset all settings</p>
        <p className="mt-1 text-[11px] text-muted-foreground">
          Deletes the preferences file. Your vaults, reports and brand packs are untouched — none of
          them live here.
        </p>
        {confirming ? (
          <div className="mt-2.5 flex items-center gap-2">
            <Button
              size="xs"
              variant="destructive"
              onClick={() => {
                void onReset()
                setConfirming(false)
              }}
            >
              <RotateCcw />
              Reset everything
            </Button>
            <Button size="xs" variant="ghost" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        ) : (
          <Button
            size="xs"
            variant="outline"
            className="mt-2.5"
            onClick={() => setConfirming(true)}
          >
            Reset all settings…
          </Button>
        )}
      </div>
    </Section>
  )
}

// ── loaders ──────────────────────────────────────────────────────────────────

/** The system font list, fetched once the dialog is first opened — enumerating
 *  fonts is slow enough that it should not happen at launch. */
function useSystemFonts(open: boolean): string[] {
  const [fonts, setFonts] = useState<string[]>([])
  useEffect(() => {
    if (!open || fonts.length > 0) return
    let stale = false
    window.api.fonts
      .list()
      .then((list) => !stale && setFonts(list))
      .catch(() => undefined)
    return () => {
      stale = true
    }
  }, [open, fonts.length])
  return fonts
}

/** Where a push would go, straight from the engine. Re-read every time the
 *  dialog opens: a branch switch in a terminal must not leave this lying. */
function useGitState(vault: string | null, open: boolean): GitState | null {
  const [state, setState] = useState<GitState | null>(null)
  useEffect(() => {
    if (!open || !vault) {
      setState(null)
      return
    }
    let stale = false
    window.api.git
      .state(vault)
      .then((next) => !stale && setState(next))
      .catch(() => !stale && setState(null))
    return () => {
      stale = true
    }
  }, [vault, open])
  return state
}

const UA_RUNTIMES = new Set(['Mozilla', 'AppleWebKit', 'KHTML', 'Gecko', 'Safari', 'Version'])

/**
 * Versions, read out of the user agent.
 *
 * Electron writes `<appName>/<appVersion>` into the UA alongside Chrome's and its
 * own, so the numbers are already in the renderer and no new IPC channel is
 * needed to show them. It is a parse of somebody else's string, so every field
 * is allowed to be missing.
 */
function readVersions(): { app: string | null; electron: string | null; chromium: string | null } {
  const found: Record<string, string> = {}
  for (const match of navigator.userAgent.matchAll(/([A-Za-z][\w.+-]*)\/([\d][\w.+-]*)/g)) {
    found[match[1]] = match[2]
  }
  const app = Object.entries(found).find(
    ([name]) => !UA_RUNTIMES.has(name) && name !== 'Chrome' && name !== 'Electron'
  )
  return {
    app: app ? `${app[0]} ${app[1]}` : null,
    electron: found.Electron ?? null,
    chromium: found.Chrome ?? null
  }
}

// ── building blocks ──────────────────────────────────────────────────────────

const APP_FONT = '__app__'

function NavItem({
  value,
  icon,
  label
}: {
  value: string
  icon: React.JSX.Element
  label: string
}): React.JSX.Element {
  return (
    <TabsTrigger
      value={value}
      className="justify-start gap-2 px-2 py-1.5 text-xs data-[state=active]:bg-accent data-[state=active]:shadow-none"
    >
      {icon}
      {label}
    </TabsTrigger>
  )
}

function Section({
  title,
  blurb,
  children
}: {
  title: string
  blurb: string
  children: React.ReactNode
}): React.JSX.Element {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-sm font-medium">{title}</h2>
        <p className="mt-0.5 max-w-lg text-[11px] text-muted-foreground">{blurb}</p>
      </div>
      {children}
    </div>
  )
}

/** Label and explanation on the left, the control on the right. */
function Row({
  label,
  hint,
  children
}: {
  label: string
  hint?: string
  children: React.ReactNode
}): React.JSX.Element {
  return (
    <div className="flex items-center justify-between gap-6">
      <div className="min-w-0">
        <p className="text-xs font-medium">{label}</p>
        {hint && <p className="mt-0.5 text-[11px] text-muted-foreground">{hint}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  )
}

function SwitchRow({
  label,
  hint,
  badge,
  checked,
  onChange,
  disabled
}: {
  label: string
  hint?: string
  badge?: React.ReactNode
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
}): React.JSX.Element {
  const id = useId()
  return (
    <div className={cn('flex items-start justify-between gap-6', disabled && 'opacity-60')}>
      <div className="min-w-0">
        <Label htmlFor={id} className="text-xs">
          {label}
          {badge}
        </Label>
        {hint && <p className="mt-1 max-w-md text-[11px] text-muted-foreground">{hint}</p>}
      </div>
      <Switch
        id={id}
        checked={checked}
        disabled={disabled}
        onCheckedChange={onChange}
        className="mt-0.5 shrink-0"
      />
    </div>
  )
}

function SliderRow({
  label,
  hint,
  value,
  display,
  min,
  max,
  step,
  onChange
}: {
  label: string
  hint?: string
  value: number
  /** The current value in words — a slider with no readout is a guess. */
  display: string
  min: number
  max: number
  step: number
  onChange: (value: number) => void
}): React.JSX.Element {
  return (
    // No <Label htmlFor>: what takes focus in a slider is the thumb, not the
    // element carrying the id, so the name goes on the control as aria-label.
    <div>
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-xs font-medium">{label}</span>
        <span className="font-mono text-[11px] text-muted-foreground">{display}</span>
      </div>
      {hint && <p className="mt-0.5 mb-1 text-[11px] text-muted-foreground">{hint}</p>}
      <Slider
        aria-label={`${label} — ${display}`}
        value={[value]}
        min={min}
        max={max}
        step={step}
        onValueChange={(next) => onChange(next[0])}
        className="mt-2 max-w-md"
      />
    </div>
  )
}

/**
 * A segmented control that behaves like a radio group, because it is one: arrow
 * keys move the selection, only the selected button is in the tab order.
 */
function Segmented<T extends string>({
  label,
  value,
  options,
  onChange
}: {
  label: string
  value: T
  options: { value: T; label: string; icon?: React.JSX.Element }[]
  onChange: (value: T) => void
}): React.JSX.Element {
  const move = (event: React.KeyboardEvent<HTMLButtonElement>, delta: number): void => {
    const index = options.findIndex((option) => option.value === value)
    const next = (index + delta + options.length) % options.length
    onChange(options[next].value)
    const sibling = event.currentTarget.parentElement?.children[next]
    if (sibling instanceof HTMLElement) sibling.focus()
  }

  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex items-center gap-0.5 rounded-md border border-border bg-muted/40 p-[2px]"
    >
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
                event.preventDefault()
                move(event, 1)
              }
              if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
                event.preventDefault()
                move(event, -1)
              }
            }}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-[5px] px-2.5 py-1 text-xs transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
              active
                ? 'bg-background text-foreground shadow-xs'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            {option.icon}
            {option.label}
          </button>
        )
      })}
    </div>
  )
}

/** A miniature of the window chrome, so the theme buttons show what they do
 *  without the reader having to look past the dialog to find out. */
function Preview(): React.JSX.Element {
  return (
    <div className="max-w-md overflow-hidden rounded-md border border-border">
      <div className="flex items-center gap-1.5 border-b border-border bg-muted/40 px-2 py-1.5">
        <span className="size-2 rounded-full bg-primary" />
        <span className="text-[10px] text-muted-foreground">reports/acme/2026-08-12-audit</span>
      </div>
      <div className="space-y-1.5 bg-background px-3 py-2.5">
        <p className="text-xs font-medium">Pricing held through the renewal window</p>
        <p className="text-[11px] text-muted-foreground">
          List price rose 12% in Q3{' '}
          <span className="font-mono text-primary">@acme-pricing</span>.
        </p>
        <div className="flex gap-1.5 pt-0.5">
          <Badge className="text-[10px]">cited</Badge>
          <Badge variant="secondary" className="text-[10px]">
            assessed
          </Badge>
          <Badge variant="outline" className="text-[10px]">
            unmarked
          </Badge>
        </div>
      </div>
    </div>
  )
}

/** Six lines of real report source in the chosen theme, at the chosen size. One
 *  sample answers every typography question on this screen at once. */
function SyntaxSample({
  theme,
  fontFamily,
  fontSize,
  lineHeight,
  lineNumbers
}: {
  theme: SyntaxTheme
  fontFamily: string
  fontSize: number
  lineHeight: number
  lineNumbers: boolean
}): React.JSX.Element {
  const palette = SYNTAX_THEMES[theme]
  return (
    <div
      className="overflow-x-auto rounded-md border border-border p-3"
      style={{
        background: palette.background,
        color: palette.foreground,
        fontFamily: fontStack(fontFamily),
        fontSize: `${fontSize}px`,
        lineHeight
      }}
    >
      <pre className="w-max">
        {TYPST_SAMPLE.map((tokens, index) => (
          <div key={index}>
            {lineNumbers && (
              <span
                aria-hidden
                className="inline-block w-6 pr-2 text-right opacity-45 select-none"
              >
                {index + 1}
              </span>
            )}
            {tokens.map((token, position) => (
              <span
                key={position}
                style={{
                  color: syntaxColor(palette, token.kind),
                  fontStyle: token.kind === 'comment' ? 'italic' : undefined,
                  fontWeight:
                    token.kind === 'cite' || token.kind === 'heading' ? 600 : undefined
                }}
              >
                {token.text}
              </span>
            ))}
          </div>
        ))}
      </pre>
    </div>
  )
}
