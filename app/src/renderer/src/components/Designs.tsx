import { useEffect, useMemo, useState } from 'react'
import {
  Download,
  FileText,
  GitBranch,
  Layers,
  Loader2,
  Package,
  Palette,
  RefreshCw,
  Search,
  ShieldAlert,
  Terminal,
  Trash2,
  TriangleAlert
} from 'lucide-react'
import type { Run } from '../../../shared/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
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
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import {
  commandLine,
  day,
  emptyInstall,
  grouped,
  installArgs,
  matches,
  parseGitUrl,
  run as runEngine,
  shortSha,
  showArgs,
  suggestId,
  uninstallArgs,
  updateArgs,
  useDesigns,
  type Design,
  type DesignOrigin,
  type InstallSpec
} from '@/lib/designs'
import { cn } from '@/lib/utils'

type Props = {
  vault: string
  /** Bump when something outside this screen changed the vault. */
  revision?: number
  /** Start a report on this design — the shell opens its new-report dialog. */
  onNewReport: (templateId: string) => void
  /** A design was installed, updated or removed: re-read the tree and the report list. */
  onChanged?: () => void
  className?: string
}

/** A command this screen is running or has run, kept so its output can be shown
 *  verbatim. `run: null` means it is still going. */
type Output = { title: string; note: string; args: string[]; run: Run | null }

/**
 * The vault's designs, and where each came from.
 *
 * Every card is `templates --json` plus the install ledger; every button is one
 * CLI command, run with its output shown as the engine wrote it. That matters
 * more here than anywhere else in the app: installing a design fetches somebody
 * else's Typst and puts it in the vault, where it will run on the next build.
 * An operation like that has to be auditable — what is about to happen, said
 * before it happens, and what did happen, quoted afterwards.
 */
export function Designs({ vault, revision = 0, onNewReport, onChanged, className }: Props) {
  const { designs, loading, error, partial, reload } = useDesigns(vault, revision)
  const [query, setQuery] = useState('')
  const [installing, setInstalling] = useState(false)
  const [removing, setRemoving] = useState<Design | null>(null)
  const [output, setOutput] = useState<Output | null>(null)

  const shown = useMemo(() => designs.filter((design) => matches(design, query)), [designs, query])
  const groups = useMemo(() => grouped(shown), [shown])
  const taken = useMemo(() => new Set(designs.map((design) => design.id)), [designs])
  const installed = designs.filter((design) => design.origin === 'installed').length

  /** Run one command with its output on screen. `mutates` says whether the vault
   *  changed, so a read-only `show` does not trigger a reload of everything. */
  async function invoke(spec: {
    title: string
    note: string
    args: string[]
    mutates?: boolean
  }): Promise<void> {
    setOutput({ title: spec.title, note: spec.note, args: spec.args, run: null })
    const result = await runEngine(vault, spec.args)
    setOutput({ title: spec.title, note: spec.note, args: spec.args, run: result })
    if (spec.mutates && result.code === 0) {
      reload()
      onChanged?.()
    }
  }

  return (
    <TooltipProvider delayDuration={200}>
      <div className={cn('flex h-full min-h-0 flex-col', className)}>
        <div className="flex h-8 shrink-0 items-center justify-between gap-2 px-3 text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
          <span>Designs</span>
          <span>
            {designs.length} {designs.length === 1 ? 'design' : 'designs'}
            {installed > 0 && ` · ${installed} installed`}
          </span>
        </div>
        <Separator />

        <div className="flex shrink-0 items-center gap-1.5 p-2">
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute top-1/2 left-2 size-3 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Filter designs"
              spellCheck={false}
              className="h-7 pl-7 text-xs"
            />
          </div>
          <Button size="xs" variant="ghost" title="Reload" onClick={reload}>
            <RefreshCw className={cn('size-3', loading && 'animate-spin')} />
          </Button>
          <Button size="xs" variant="secondary" onClick={() => setInstalling(true)}>
            <Download className="size-3" />
            Install from a URL…
          </Button>
        </div>
        <Separator />

        {partial && (
          <div className="flex shrink-0 items-start gap-1.5 border-b border-border px-3 py-1.5 text-[10.5px] text-muted-foreground">
            <TriangleAlert className="mt-px size-3 shrink-0" />
            <span className="font-mono whitespace-pre-wrap">{partial}</span>
          </div>
        )}

        <ScrollArea className="min-h-0 flex-1">
          <div className="pb-4">
            {error && (
              <div className="space-y-2 p-3">
                <p className="text-xs text-muted-foreground">
                  <span className="font-mono">templates --json</span> failed.
                </p>
                <pre className="max-h-40 overflow-auto rounded-md border border-destructive/50 p-2 font-mono text-[11px] whitespace-pre-wrap">
                  {error}
                </pre>
                <Button size="xs" variant="secondary" onClick={reload}>
                  Try again
                </Button>
              </div>
            )}

            {!error && loading && designs.length === 0 && (
              <div className="grid gap-3 p-3 [grid-template-columns:repeat(auto-fill,minmax(300px,1fr))]">
                {[0, 1, 2, 3].map((card) => (
                  <Skeleton key={card} className="h-44 w-full" />
                ))}
              </div>
            )}

            {!error && !loading && designs.length > 0 && shown.length === 0 && (
              <p className="px-3 py-4 text-xs text-muted-foreground">Nothing matches “{query}”.</p>
            )}

            {!error &&
              groups.map((group) => (
                <section key={group.group} className="px-3 pt-3">
                  <h2 className="text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
                    {group.group || 'Ungrouped'}
                  </h2>
                  <div className="mt-2 grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(300px,1fr))]">
                    {group.designs.map((design) => (
                      <DesignCard
                        key={design.id}
                        design={design}
                        onUse={() => onNewReport(design.id)}
                        onShow={() =>
                          void invoke({
                            title: `template show ${design.id}`,
                            note: 'What the engine says this design is, and what it inherits.',
                            args: showArgs(design.id)
                          })
                        }
                        onUpdate={() =>
                          void invoke({
                            title: `template update ${design.id}`,
                            note: 'Re-fetches the design from the URL it was installed from.',
                            args: updateArgs(design.id),
                            mutates: true
                          })
                        }
                        onUninstall={() => setRemoving(design)}
                      />
                    ))}
                  </div>
                </section>
              ))}
          </div>
        </ScrollArea>

        <InstallDialog
          vault={vault}
          open={installing}
          onOpenChange={setInstalling}
          taken={taken}
          onInstalled={() => {
            reload()
            onChanged?.()
          }}
        />

        <Dialog open={Boolean(removing)} onOpenChange={(open) => !open && setRemoving(null)}>
          <DialogContent className="sm:max-w-lg">
            {removing && (
              <UninstallBody
                design={removing}
                onCancel={() => setRemoving(null)}
                onConfirm={() => {
                  const design = removing
                  setRemoving(null)
                  void invoke({
                    title: `template uninstall ${design.id}`,
                    note: 'Removing an installed design.',
                    args: uninstallArgs(design.id),
                    mutates: true
                  })
                }}
              />
            )}
          </DialogContent>
        </Dialog>

        <Dialog open={Boolean(output)} onOpenChange={(open) => !open && setOutput(null)}>
          <DialogContent className="sm:max-w-2xl">
            <DialogHeader>
              <DialogTitle className="font-mono text-sm">{output?.title}</DialogTitle>
              <DialogDescription>{output?.note}</DialogDescription>
            </DialogHeader>
            {output && <RunOutput args={output.args} run={output.run} />}
            <DialogFooter>
              <Button size="sm" onClick={() => setOutput(null)}>
                Close
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </TooltipProvider>
  )
}

// ── One design ───────────────────────────────────────────────────────────────

function DesignCard({
  design,
  onUse,
  onShow,
  onUpdate,
  onUninstall
}: {
  design: Design
  onUse: () => void
  onShow: () => void
  onUpdate: () => void
  onUninstall: () => void
}) {
  const record = design.installed

  return (
    <Card className="gap-2.5 py-3">
      <CardHeader className="gap-1 px-3">
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="text-[13px] leading-snug">{design.title || design.id}</CardTitle>
          <OriginChip origin={design.origin} />
        </div>
        <code className="truncate font-mono text-[11px] text-muted-foreground" title={design.folder}>
          {design.id}
        </code>
      </CardHeader>

      <CardContent className="space-y-2 px-3 text-[11.5px]">
        <p className={cn('leading-relaxed', design.description ? 'text-foreground/80' : 'text-muted-foreground')}>
          {design.description || 'No description.'}
        </p>

        <Lineage chain={design.lineage} missing={design.missingParent} />

        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground">
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="flex items-center gap-1" tabIndex={0}>
                <Palette className="size-3" />
                <span className="font-mono">{design.brand}</span>
              </span>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="max-w-[260px]">
              The brand pack this design names in its{' '}
              <span className="font-mono">template.toml</span>. Every colour and font it uses comes
              from there.
            </TooltipContent>
          </Tooltip>

          <span className="flex items-center gap-1" title="reports built with this design">
            <FileText className="size-3" />
            {design.uses === 0 ? 'no reports' : `${design.uses} ${design.uses === 1 ? 'report' : 'reports'}`}
          </span>
        </div>

        {record && <InstalledFacts record={record} />}

        {design.missingParent && (
          <p className="flex items-start gap-1.5 text-[10.5px] text-destructive">
            <TriangleAlert className="mt-px size-3 shrink-0" />
            <span>
              It extends <span className="font-mono">{design.missingParent}</span>, which is not in
              this vault. Reports using it will not build.
            </span>
          </p>
        )}
      </CardContent>

      <CardFooter className="flex-wrap gap-1.5 px-3">
        <Button size="xs" variant="secondary" onClick={onUse}>
          Use for a new report
        </Button>
        <Button size="xs" variant="ghost" onClick={onShow} title="report-maker template show">
          <Terminal className="size-3" />
          Show
        </Button>
        {record && (
          <>
            <Button size="xs" variant="ghost" onClick={onUpdate} title={`Re-fetch from ${record.url}`}>
              <RefreshCw className="size-3" />
              Update
            </Button>
            <Button
              size="xs"
              variant="ghost"
              className="text-destructive hover:text-destructive"
              onClick={onUninstall}
            >
              <Trash2 className="size-3" />
              Uninstall
            </Button>
          </>
        )}
      </CardFooter>
    </Card>
  )
}

function OriginChip({ origin }: { origin: DesignOrigin }) {
  const explain: Record<DesignOrigin, string> = {
    'built-in': 'Ships with report-maker. Copy it into the vault to edit it.',
    vault: 'Written in this vault, under templates/.',
    installed: 'Fetched from a git URL into this vault.'
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          tabIndex={0}
          variant={origin === 'installed' ? 'secondary' : 'outline'}
          className="shrink-0 gap-1 px-1.5 py-0 text-[9.5px] font-normal"
        >
          {origin === 'installed' && <Package className="size-2.5" />}
          {origin}
        </Badge>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-[240px]">
        {explain[origin]}
      </TooltipContent>
    </Tooltip>
  )
}

/** The inheritance chain, oldest first — the order the files merge in. A design
 *  that inherits nothing says so rather than showing a chain of one. */
function Lineage({ chain, missing }: { chain: string[]; missing: string | null }) {
  if (chain.length < 2) {
    return <p className="text-[10.5px] text-muted-foreground">Inherits nothing — it is a root design.</p>
  }
  return (
    <p className="flex flex-wrap items-center gap-1 text-[10.5px] text-muted-foreground">
      <Layers className="size-3 shrink-0" />
      {chain.map((id, index) => (
        <span key={id} className="flex items-center gap-1">
          {index > 0 && <span aria-hidden>→</span>}
          <span
            className={cn(
              'font-mono',
              id === missing && 'text-destructive line-through',
              index === chain.length - 1 && 'text-foreground'
            )}
          >
            {id}
          </span>
        </span>
      ))}
    </p>
  )
}

function InstalledFacts({ record }: { record: NonNullable<Design['installed']> }) {
  const parsed = parseGitUrl(record.url)
  return (
    <div className="space-y-0.5 rounded-md border border-dashed border-border px-2 py-1.5 text-[10.5px] text-muted-foreground">
      <div className="flex items-center gap-1">
        <GitBranch className="size-3 shrink-0" />
        <span className="truncate font-mono" title={record.url}>
          {parsed ? `${parsed.host}/${parsed.path}` : record.url}
        </span>
      </div>
      <div className="font-mono" title={record.sha}>
        {record.ref || 'default branch'} · {shortSha(record.sha)} · installed{' '}
        {day(record.installed_at)}
      </div>
      {record.subdir && (
        <div className="truncate font-mono" title={record.subdir}>
          subdir {record.subdir}
        </div>
      )}
    </div>
  )
}

// ── What a command printed ───────────────────────────────────────────────────

/**
 * A command's output, quoted.
 *
 * Not summarised, not filtered to the last line: this is the record of something
 * that wrote to the vault, and the person who ran it has to be able to read what
 * the engine actually said — including the parts this app has never heard of.
 */
function RunOutput({ args, run }: { args: string[]; run: Run | null }) {
  return (
    <div className="space-y-2">
      <p className="font-mono text-[10.5px] break-all text-muted-foreground">
        {run?.command ?? commandLine(args)}
      </p>

      {!run ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" />
          Running…
        </p>
      ) : (
        <>
          <div className="flex items-center gap-2">
            <Badge
              variant={run.code === 0 ? 'secondary' : 'destructive'}
              className="px-1.5 py-0 font-mono text-[10px] font-normal"
            >
              exit {run.code}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {run.code === 0 ? 'succeeded' : 'failed — nothing else was changed'}
            </span>
          </div>
          <Stream text={run.stdout} />
          <Stream text={run.stderr} tone="error" />
          {!run.stdout.trim() && !run.stderr.trim() && (
            <p className="text-xs text-muted-foreground">The command printed nothing.</p>
          )}
        </>
      )}
    </div>
  )
}

function Stream({ text, tone }: { text: string; tone?: 'error' }) {
  if (!text.trim()) return null
  return (
    <pre
      className={cn(
        'max-h-64 overflow-auto rounded-md border p-2 font-mono text-[11px] whitespace-pre-wrap',
        tone === 'error' ? 'border-destructive/50' : 'border-border'
      )}
    >
      {text.trimEnd()}
    </pre>
  )
}

// ── Installing one ───────────────────────────────────────────────────────────

type InstallProps = {
  vault: string
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Design ids already in the vault, so the review step can warn about a clash. */
  taken: Set<string>
  onInstalled: () => void
}

/**
 * `report-maker template install <url>`: fetch a design from a git repository
 * into this vault.
 *
 * Deliberately two steps. Filling in a URL and pressing Install in one motion is
 * how you end up running code you never looked at — so the form leads to a
 * review that states the URL, the id it will be installed as, and what a design
 * is allowed to do once it is here. Enter never installs; it only moves to that
 * review.
 */
function InstallDialog({ vault, open, onOpenChange, taken, onInstalled }: InstallProps) {
  const [spec, setSpec] = useState<InstallSpec>(emptyInstall)
  const [step, setStep] = useState<'form' | 'review' | 'done'>('form')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<Run | null>(null)
  // Once the id has been typed in, it stops following the URL — otherwise
  // correcting a typo in the URL silently discards the name that was chosen.
  const [named, setNamed] = useState(false)

  useEffect(() => {
    if (!open) return
    setSpec(emptyInstall)
    setStep('form')
    setBusy(false)
    setResult(null)
    setNamed(false)
  }, [open])

  const args = installArgs(spec)
  const parsed = parseGitUrl(spec.url)
  const id = spec.id.trim()
  const clash = id.length > 0 && taken.has(id) && !spec.force
  const ready = spec.url.trim().length > 0

  function edit(patch: Partial<InstallSpec>): void {
    const renaming = patch.id !== undefined
    if (renaming) setNamed(patch.id!.trim().length > 0)
    setSpec((current) => {
      const next = { ...current, ...patch }
      // The suggestion tracks the URL and the subdirectory until somebody names
      // it themselves; whatever is in the field is what gets passed as --id, so
      // the review step and the command can never disagree.
      if (!renaming && !named) next.id = suggestId(next.url, next.subdir)
      return next
    })
  }

  async function install(): Promise<void> {
    if (busy) return
    setBusy(true)
    const run = await runEngine(vault, args)
    setResult(run)
    setBusy(false)
    setStep('done')
    if (run.code === 0) onInstalled()
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Install a design</DialogTitle>
          <DialogDescription>
            {step === 'form'
              ? 'A design is fetched from a git repository into this vault, under templates/.'
              : step === 'review'
                ? 'What is about to happen, before it happens.'
                : 'What the engine did, in its own words.'}
          </DialogDescription>
        </DialogHeader>

        {step === 'form' && (
          <div className="space-y-3">
            <Field
              label="Repository URL"
              hint="https://…, ssh://…, or git@host:owner/repo.git"
              value={spec.url}
              autoFocus
              placeholder="https://github.com/owner/report-designs.git"
              onChange={(value) => edit({ url: value })}
              onEnter={() => ready && setStep('review')}
            />
            <div className="grid grid-cols-2 gap-3">
              <Field
                label="Install as"
                hint="Design id. Nesting groups it. Leave empty to let the engine name it."
                value={spec.id}
                placeholder="audits/company"
                onChange={(value) => edit({ id: value })}
                onEnter={() => ready && setStep('review')}
              />
              <Field
                label="Ref"
                hint="Branch, tag or commit. Empty takes the default branch."
                value={spec.ref}
                placeholder="main"
                onChange={(value) => edit({ ref: value })}
                onEnter={() => ready && setStep('review')}
              />
            </div>
            <Field
              label="Subdirectory"
              hint="When the design is not at the root of the repository."
              value={spec.subdir}
              placeholder="designs/company"
              onChange={(value) => edit({ subdir: value })}
              onEnter={() => ready && setStep('review')}
            />

            {/* The switch is the only control here: a <label> cannot activate a
                button, which is what a Radix switch is, so wrapping the text in
                one would look clickable and do nothing. */}
            <div className="flex items-start gap-2.5 rounded-md border border-border p-2.5">
              <Switch
                id="install-force"
                checked={spec.force}
                aria-label="Replace a design of the same id"
                onCheckedChange={(checked) => edit({ force: checked })}
                className="mt-0.5"
              />
              <div className="text-[11.5px] leading-relaxed">
                <Label htmlFor="install-force" className="text-[11.5px] font-medium">
                  Replace a design of the same id
                </Label>
                <p className="text-muted-foreground">
                  Passes <span className="font-mono">--force</span>. Without it, the install stops
                  rather than overwriting what is already here.
                </p>
              </div>
            </div>

            <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-muted-foreground">
              <ShieldAlert className="mt-px size-3.5 shrink-0" />
              An installed design runs at build time — this is code, not a colour scheme.
            </p>
          </div>
        )}

        {step === 'review' && (
          <div className="space-y-3 text-[11.5px]">
            <dl className="space-y-1.5 rounded-md border border-border p-3">
              <Fact label="From">
                <span className="font-mono break-all">{spec.url.trim()}</span>
                {parsed ? (
                  <span className="block text-muted-foreground">
                    host <span className="font-mono">{parsed.host}</span>
                  </span>
                ) : (
                  <span className="block text-destructive">
                    This does not read as a git URL. The engine will reject it if it cannot clone
                    it.
                  </span>
                )}
              </Fact>
              <Fact label="Install as">
                {id ? (
                  <span className="font-mono">{id}</span>
                ) : (
                  <span className="text-muted-foreground">
                    the engine chooses a name from the URL
                  </span>
                )}
              </Fact>
              <Fact label="Ref">
                <span className={spec.ref.trim() ? 'font-mono' : 'text-muted-foreground'}>
                  {spec.ref.trim() || 'default branch'}
                </span>
              </Fact>
              {spec.subdir.trim() && (
                <Fact label="Subdirectory">
                  <span className="font-mono">{spec.subdir.trim()}</span>
                </Fact>
              )}
            </dl>

            <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 leading-relaxed">
              <ShieldAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
              <p>
                <span className="font-medium">Installing this runs somebody else's code.</span> A
                design is Typst, and Typst runs every time you build a report with it. It can read
                any file inside this vault and place what it reads in the built PDF. Install only
                from a source you trust.
              </p>
            </div>

            {clash && (
              <p className="flex items-start gap-1.5 text-destructive">
                <TriangleAlert className="mt-px size-3.5 shrink-0" />
                <span>
                  <span className="font-mono">{id}</span> already exists in this vault. The install
                  will stop unless you go back and turn on “Replace a design of the same id”.
                </span>
              </p>
            )}

            <div>
              <p className="mb-1 text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
                The command
              </p>
              <pre className="overflow-auto rounded-md border border-border p-2 font-mono text-[11px] whitespace-pre-wrap">
                {commandLine(args)}
              </pre>
            </div>
          </div>
        )}

        {step === 'done' && <RunOutput args={args} run={result} />}

        <DialogFooter>
          {step === 'form' && (
            <>
              <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button size="sm" disabled={!ready} onClick={() => setStep('review')}>
                Review…
              </Button>
            </>
          )}
          {step === 'review' && (
            <>
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => setStep('form')}>
                Back
              </Button>
              <Button size="sm" disabled={busy} onClick={() => void install()}>
                {busy && <Loader2 className="size-3.5 animate-spin" />}
                {busy ? 'Installing…' : 'Install'}
              </Button>
            </>
          )}
          {step === 'done' && (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSpec(emptyInstall)
                  setNamed(false)
                  setResult(null)
                  setStep('form')
                }}
              >
                Install another
              </Button>
              <Button size="sm" onClick={() => onOpenChange(false)}>
                Done
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Field({
  label,
  hint,
  value,
  placeholder,
  autoFocus,
  onChange,
  onEnter
}: {
  label: string
  hint: string
  value: string
  placeholder?: string
  autoFocus?: boolean
  onChange: (value: string) => void
  onEnter: () => void
}) {
  return (
    <div className="space-y-1">
      <Label className="text-[11px]">{label}</Label>
      <Input
        value={value}
        autoFocus={autoFocus}
        spellCheck={false}
        placeholder={placeholder}
        className="h-8 font-mono text-xs"
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          // Enter moves to the review step and never installs: the last thing
          // this dialog should do is act on a keystroke aimed at a text field.
          if (event.key !== 'Enter') return
          event.preventDefault()
          onEnter()
        }}
      />
      <p className="text-[10.5px] leading-relaxed text-muted-foreground">{hint}</p>
    </div>
  )
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[92px_1fr] gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  )
}

// ── Removing one ─────────────────────────────────────────────────────────────

/**
 * Uninstalling names the folder about to be deleted and what stops working.
 *
 * A report records its design in its import line, so removing a design does not
 * fail loudly at removal time — it fails later, at the next build of every
 * report that named it. That consequence belongs in front of the person about to
 * confirm, not in the build log they see tomorrow.
 */
function UninstallBody({
  design,
  onCancel,
  onConfirm
}: {
  design: Design
  onCancel: () => void
  onConfirm: () => void
}) {
  const folder = design.installed?.folder ?? design.folder

  return (
    <>
      <DialogHeader>
        <DialogTitle>
          Remove <span className="font-mono">{design.id}</span>?
        </DialogTitle>
        <DialogDescription>
          <span className="font-mono">report-maker template uninstall {design.id}</span>
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-3 text-[11.5px] leading-relaxed">
        <div>
          <p className="text-muted-foreground">This folder is deleted:</p>
          <pre className="mt-1 overflow-auto rounded-md border border-border p-2 font-mono text-[11px] break-all whitespace-pre-wrap">
            {folder}
          </pre>
        </div>

        {design.uses > 0 ? (
          <p className="flex items-start gap-1.5 text-destructive">
            <TriangleAlert className="mt-px size-3.5 shrink-0" />
            <span>
              {design.uses} {design.uses === 1 ? 'report names' : 'reports name'} this design. Their
              import of <span className="font-mono">/.build/design/{design.id}/report.typ</span>{' '}
              will have nothing to resolve to, and they will stop building until they are moved to a
              design that exists.
            </span>
          </p>
        ) : (
          <p className="text-muted-foreground">
            No report in this vault uses it. Anything built with it in the past keeps its PDF —
            only future builds need a design that exists.
          </p>
        )}
      </div>

      <DialogFooter>
        <Button variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="destructive" size="sm" onClick={onConfirm}>
          <Trash2 className="size-3.5" />
          Remove design
        </Button>
      </DialogFooter>
    </>
  )
}
