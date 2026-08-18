import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Check,
  Copy,
  FilePlus2,
  Info,
  Loader2,
  Maximize2,
  Palette,
  RefreshCw,
  TriangleAlert,
  Workflow,
  ZoomIn,
  ZoomOut
} from 'lucide-react'
import type { Finding, LineClass, Settings } from '../../../shared/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable'
import { Separator } from '@/components/ui/separator'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { Editor, type EditorHandle } from '@/components/Editor'
import {
  EMPHASIS,
  SEED,
  classSyntax,
  diagramFileName,
  diagramPath,
  divergence,
  isPreparedFrom,
  readFailure,
  renderSvg,
  swatch,
  usePrepared,
  wrote,
  type MermaidFailure,
  type Prepared
} from '@/lib/mermaid'
import { describeError } from '@/lib/sources'
import { cn } from '@/lib/utils'

type Props = {
  vault: string
  /** Absolute path of the open `.mmd`, or null to offer making the first one. */
  path: string | null
  /** The report the diagram belongs to, as the engine names it. Null for a
   *  `.mmd` outside any report — a starter, a scratch file — which can be
   *  previewed but not rendered, since `diagrams` only walks reports. */
  reportId: string | null
  text: string
  settings: Settings
  /** True while the buffer differs from the file on disk. */
  dirty?: boolean
  findings?: readonly Finding[]
  lineClasses?: readonly LineClass[]
  onChange: (text: string) => void
  /** Write the buffer. Awaited before every preview, so the engine reads what is
   *  on screen rather than the last thing that was saved. */
  onSave: () => void | Promise<void>
  onBuild: () => void
  /** A new diagram was written. The shell opens it and re-reads the tree. */
  onCreated?: (path: string) => void
  /** The shell's handle on the editor, forwarded to the CodeMirror half. Without
   *  it a click in the Problems panel would open the file and then fail to move
   *  the cursor, which reads as the panel being broken. */
  handleRef?: React.Ref<EditorHandle>
  className?: string
}

/**
 * Write a diagram on the left, watch the *build's own input* on the right.
 *
 * The pane on the right is not a rendering of the text on the left, and that
 * distinction is the entire feature. The engine injects brand `classDef`s into
 * a `.mmd` before mermaid ever sees it, because Typst's SVG renderer honours
 * mermaid's inline styles over any stylesheet — a preview styled from the CSS
 * alone would look correct while the PDF came out grey. So every keystroke goes
 * back through `report-maker diagrams --prepare`, and what is drawn here is
 * byte-for-byte what mermaid-cli is handed at build time. See `lib/mermaid.ts`.
 *
 * Three consequences worth stating, because each one is visible in the UI:
 *
 * — **The file is what is previewed.** The engine prepares a path, not a string,
 *   so the buffer is written before each run. An unsaved diagram would otherwise
 *   preview as the last save, which is a picture of a file nobody can see.
 * — **The mermaid that draws this is not the mermaid that builds it.** The app
 *   carries its own; the vault installs mermaid-cli. When their majors diverge
 *   the banner says so, because a known difference stated out loud is honest and
 *   a silent one is a trap.
 * — **Colour is never typed.** The legend inserts `:::em-accent`, never a hex
 *   code, and the swatches are read back out of the classDefs the engine
 *   injected — so what the legend shows is what the brand pack decided.
 */
export function MermaidEditor({
  vault,
  path,
  reportId,
  text,
  settings,
  dirty = false,
  findings,
  lineClasses,
  onChange,
  onSave,
  onBuild,
  onCreated,
  handleRef,
  className
}: Props) {
  const editor = useRef<EditorHandle | null>(null)

  // One callback ref feeding two consumers: the legend, which types into the
  // document, and whoever upstream asked for a handle.
  const attach = useCallback(
    (instance: EditorHandle | null) => {
      editor.current = instance
      if (typeof handleRef === 'function') handleRef(instance)
      else if (handleRef) (handleRef as { current: EditorHandle | null }).current = instance
    },
    [handleRef]
  )

  // The buffer reaches the file before the engine reads it. Held in a ref so the
  // hook's effect does not restart every time the parent hands down a new
  // callback identity.
  const save = useRef(onSave)
  save.current = onSave
  const settle = useCallback(async () => {
    await save.current()
  }, [])

  const { outcome, running, refresh } = usePrepared(vault, path, text, settle)
  const prepared = outcome?.state === 'ok' ? outcome.prepared : null

  if (path === null) {
    return <Empty vault={vault} reportId={reportId} onCreated={onCreated} className={className} />
  }

  return (
    <TooltipProvider delayDuration={200}>
      <div className={cn('flex h-full min-h-0 flex-col', className)}>
        <ResizablePanelGroup direction="horizontal" className="min-h-0 flex-1">
          <ResizablePanel defaultSize={48} minSize={24}>
            <div className="flex h-full min-h-0 flex-col">
              <div className="min-h-0 flex-1">
                <Editor
                  ref={attach}
                  path={path}
                  rel={path.startsWith(vault) ? path.slice(vault.length).replace(/^\//, '') : path}
                  text={text}
                  settings={settings}
                  findings={findings}
                  lineClasses={lineClasses}
                  stale={dirty}
                  onChange={onChange}
                  onSave={() => void onSave()}
                  onBuild={onBuild}
                />
              </div>
              <Separator />
              <Legend
                prepared={prepared}
                onInsert={(name) => {
                  const view = editor.current?.view()
                  if (!view) return
                  const snippet = classSyntax(name)
                  const { from, to } = view.state.selection.main
                  view.dispatch({
                    changes: { from, to, insert: snippet },
                    selection: { anchor: from + snippet.length }
                  })
                  view.focus()
                }}
              />
            </div>
          </ResizablePanel>

          <ResizableHandle withHandle />

          <ResizablePanel defaultSize={52} minSize={24}>
            <Preview
              vault={vault}
              reportId={reportId}
              text={text}
              dirty={dirty}
              running={running}
              outcome={outcome}
              onRefresh={refresh}
            />
          </ResizablePanel>
        </ResizablePanelGroup>
      </div>
    </TooltipProvider>
  )
}

// ── the preview half ─────────────────────────────────────────────────────────

type PreviewProps = {
  vault: string
  reportId: string | null
  text: string
  dirty: boolean
  running: boolean
  outcome: ReturnType<typeof usePrepared>['outcome']
  onRefresh: () => void
}

/** `viewBox="0 0 W H"` out of the SVG mermaid produced — the natural size, before
 *  the `max-width` it sets for a web page it is not being drawn on. */
function naturalSize(svg: string): { w: number; h: number } | null {
  const found = /viewBox="\s*[-\d.]+\s+[-\d.]+\s+([\d.]+)\s+([\d.]+)/.exec(svg)
  if (!found) return null
  const w = Number(found[1])
  const h = Number(found[2])
  return w > 0 && h > 0 ? { w, h } : null
}

function Preview({ vault, reportId, text, dirty, running, outcome, onRefresh }: PreviewProps) {
  const prepared = outcome?.state === 'ok' ? outcome.prepared : null

  const [svg, setSvg] = useState<string | null>(null)
  const [failure, setFailure] = useState<MermaidFailure | null>(null)
  const [scale, setScale] = useState(1)
  const [fitting, setFitting] = useState(true)
  const [copied, setCopied] = useState(false)
  const [render, setRender] = useState<{ busy: boolean; text: string; ok: boolean } | null>(null)
  const frame = useRef<HTMLDivElement>(null)

  // Draw whatever the engine last prepared. A failed render keeps the previous
  // picture: a diagram half-typed is broken for a second at a time, and blanking
  // the pane on every intermediate keystroke reads as breakage rather than as
  // typing.
  useEffect(() => {
    if (!prepared) return
    let alive = true
    renderSvg(prepared)
      .then((drawn) => {
        if (!alive) return
        setSvg(drawn)
        setFailure(null)
      })
      .catch((err) => {
        if (!alive) return
        setFailure(readFailure(err, prepared, text))
      })
    return () => {
      alive = false
    }
    // `text` only sharpens the error message; the picture comes from `prepared`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prepared])

  const fit = useCallback(() => {
    const box = frame.current
    const size = svg ? naturalSize(svg) : null
    if (!box || !size) return
    const room = Math.min((box.clientWidth - 24) / size.w, (box.clientHeight - 24) / size.h)
    setScale(Math.max(0.1, Math.min(4, room)))
  }, [svg])

  useEffect(() => {
    if (fitting) fit()
  }, [fit, fitting])

  useEffect(() => {
    const box = frame.current
    if (!box || !fitting) return
    const observer = new ResizeObserver(() => fit())
    observer.observe(box)
    return () => observer.disconnect()
  }, [fit, fitting])

  const zoom = (factor: number): void => {
    setFitting(false)
    setScale((current) => Math.max(0.1, Math.min(4, current * factor)))
  }

  async function copy(): Promise<void> {
    if (!svg) return
    try {
      await navigator.clipboard.writeText(svg)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1200)
    } catch {
      // Nothing to recover: the clipboard either took it or it did not.
    }
  }

  /**
   * `report-maker diagrams <report>` — the real thing.
   *
   * The preview is a preview; the `.svg` beside the `.mmd` is what a report
   * imports and what `check` looks for (E007). So this button exists to close
   * that gap deliberately, and it reports the files the engine said it wrote
   * rather than claiming success from an exit code.
   */
  async function renderForReal(): Promise<void> {
    if (!reportId) return
    setRender({ busy: true, text: 'Running report-maker diagrams…', ok: true })
    try {
      const run = await window.api.engine.run(vault, ['diagrams', reportId])
      const written = wrote(run)
      const summary =
        run.code !== 0
          ? (run.stderr || run.stdout).trim() || `exit ${run.code}`
          : written.length > 0
            ? written.map((file) => `${file.path} (${file.status})`).join('\n')
            : (run.stdout || run.stderr).trim() || 'the command printed nothing'
      setRender({ busy: false, text: summary, ok: run.code === 0 })
    } catch (err) {
      setRender({ busy: false, text: describeError(err), ok: false })
    }
  }

  const stale = prepared !== null && !isPreparedFrom(prepared, text)
  const gap = prepared ? divergence(prepared.mermaidVersion) : null

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-8 shrink-0 items-center gap-1 px-2">
        <Button size="icon-xs" variant="ghost" title="Zoom out" onClick={() => zoom(1 / 1.2)}>
          <ZoomOut />
        </Button>
        <button
          className="w-11 shrink-0 text-center font-mono text-[10.5px] text-muted-foreground hover:text-foreground"
          title="Back to 100%"
          onClick={() => {
            setFitting(false)
            setScale(1)
          }}
        >
          {Math.round(scale * 100)}%
        </button>
        <Button size="icon-xs" variant="ghost" title="Zoom in" onClick={() => zoom(1.2)}>
          <ZoomIn />
        </Button>
        <Button
          size="icon-xs"
          variant={fitting ? 'secondary' : 'ghost'}
          title="Fit to the pane, and keep it fitted"
          onClick={() => setFitting((current) => !current)}
        >
          <Maximize2 />
        </Button>

        <Separator orientation="vertical" className="mx-1 h-4" />

        <Button size="xs" variant="ghost" disabled={!svg} onClick={() => void copy()}>
          {copied ? <Check /> : <Copy />}
          {copied ? 'Copied' : 'Copy SVG'}
        </Button>

        <Tooltip>
          <TooltipTrigger asChild>
            <span>
              <Button
                size="xs"
                variant="secondary"
                disabled={!reportId || Boolean(render?.busy)}
                onClick={() => void renderForReal()}
              >
                {render?.busy ? <Loader2 className="animate-spin" /> : <Workflow />}
                Render for real
              </Button>
            </span>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-[280px]">
            {reportId
              ? 'report-maker diagrams — writes the .svg beside the .mmd, which is the file the report actually imports.'
              : 'This .mmd is not inside a report, and report-maker diagrams only walks reports.'}
          </TooltipContent>
        </Tooltip>

        <div className="ml-auto flex items-center gap-1.5">
          {prepared && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge
                  tabIndex={0}
                  variant="outline"
                  className="gap-1 px-1.5 py-0 text-[9.5px] font-normal"
                >
                  <Palette className="size-2.5" />
                  {prepared.pack}
                </Badge>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="max-w-[280px]">
                The brand pack this report's design names. Every colour in the picture came from
                it — <span className="font-mono">{prepared.config}</span>
              </TooltipContent>
            </Tooltip>
          )}
          <Button size="icon-xs" variant="ghost" title="Prepare and draw again" onClick={onRefresh}>
            <RefreshCw className={cn(running && 'animate-spin')} />
          </Button>
        </div>
      </div>
      <Separator />

      {gap && gap.tone !== 'ok' && (
        <Strip tone={gap.tone === 'warn' ? 'warn' : 'note'}>{gap.text}</Strip>
      )}

      {stale && (
        <Strip tone="note">
          Showing the file as it was last written{dirty ? ', which is behind the editor' : ''}. The
          preview follows the file, because that is what the engine prepares.
        </Strip>
      )}

      {outcome?.state === 'unsupported' && (
        <Strip tone="warn">
          This engine has no <span className="font-mono">diagrams --prepare</span> yet, so there is
          nothing safe to draw — rendering the raw text would show colours the build would not use.
          Update report-maker, or use <span className="font-mono">Render for real</span> and read the
          SVG.
        </Strip>
      )}

      {outcome?.state === 'failed' && (
        <Strip tone="warn">
          <span className="font-mono whitespace-pre-wrap">{outcome.message}</span>
        </Strip>
      )}

      <div ref={frame} className="min-h-0 flex-1 overflow-auto p-3">
        {svg ? (
          <div
            className="origin-top-left [&>svg]:h-auto [&>svg]:max-w-none"
            style={{ transform: `scale(${scale})` }}
            // The markup is mermaid's own output, produced from the vault's file
            // under the securityLevel the generated config sets — the same config
            // mermaid-cli runs with at build time.
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        ) : (
          <p className="px-1 text-xs text-muted-foreground">
            {running ? 'Preparing…' : outcome ? 'Nothing drawn yet.' : 'Waiting for the engine…'}
          </p>
        )}
      </div>

      {failure && <Failure failure={failure} />}

      {render && !render.busy && (
        <div className="shrink-0 border-t border-border">
          <pre
            className={cn(
              'max-h-28 overflow-auto p-2 font-mono text-[10.5px] whitespace-pre-wrap',
              !render.ok && 'text-destructive'
            )}
          >
            {render.text}
          </pre>
        </div>
      )}

      {gap && gap.tone === 'ok' && (
        <div className="shrink-0 border-t border-border px-3 py-1 font-mono text-[10px] text-muted-foreground">
          {gap.text}
        </div>
      )}
    </div>
  )
}

function Strip({ tone, children }: { tone: 'note' | 'warn'; children: React.ReactNode }) {
  return (
    <div
      className={cn(
        'flex shrink-0 items-start gap-1.5 border-b px-3 py-1.5 text-[10.5px] leading-relaxed',
        tone === 'warn'
          ? 'border-destructive/40 bg-destructive/5 text-destructive'
          : 'border-border text-muted-foreground'
      )}
    >
      {tone === 'warn' ? (
        <TriangleAlert className="mt-px size-3 shrink-0" />
      ) : (
        <Info className="mt-px size-3 shrink-0" />
      )}
      <span className="min-w-0">{children}</span>
    </div>
  )
}

/**
 * What mermaid said, next to the line it said it about.
 *
 * mermaid throws rather than returning, and an uncaught throw here would empty
 * the pane — which tells an author their diagram disappeared instead of that
 * they are one bracket short. Line numbers are the author's own, because the
 * engine appends its classDefs rather than inserting them.
 */
function Failure({ failure }: { failure: MermaidFailure }) {
  return (
    <div className="shrink-0 border-t border-destructive/40 bg-destructive/5 px-3 py-2 text-[11px] text-destructive">
      <p className="flex items-center gap-1.5 font-medium">
        <TriangleAlert className="size-3.5 shrink-0" />
        {failure.line === null ? 'mermaid could not read this diagram' : `Line ${failure.line}`}
        {!failure.authored && (
          <span className="font-normal">
            — in the generated classDef block, which is a bug in report-maker, not in your file
          </span>
        )}
      </p>
      <pre className="mt-1 max-h-24 overflow-auto font-mono text-[10.5px] whitespace-pre-wrap">
        {failure.message}
      </pre>
    </div>
  )
}

// ── the legend ───────────────────────────────────────────────────────────────

/**
 * The four emphasis classes, and a click that types one.
 *
 * This is the only colour control a diagram gets, and it exists because the
 * alternative is somebody reaching for a hex code at the exact moment they want
 * one node to stand out. A swatch is drawn from the classDef the engine actually
 * injected, so a class already in use shows the brand's real colour, and one
 * that is not shows an outline — the honest answer, since until the source
 * references it the engine has nothing to inject.
 */
function Legend({ prepared, onInsert }: { prepared: Prepared | null; onInsert: (name: string) => void }) {
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-1 px-2 py-1.5">
      <span className="mr-1 text-[9.5px] font-medium tracking-widest text-muted-foreground uppercase">
        Emphasis
      </span>
      {EMPHASIS.map((role) => {
        const colours = swatch(prepared?.classDefs[role.name])
        const live = Boolean(colours.fill)
        return (
          <Tooltip key={role.name}>
            <TooltipTrigger asChild>
              <button
                className="flex items-center gap-1.5 rounded-md border border-border px-1.5 py-0.5 font-mono text-[10px] hover:bg-accent"
                onClick={() => onInsert(role.name)}
              >
                <span
                  className={cn('size-2.5 rounded-[3px] border', !live && 'border-dashed')}
                  style={{
                    backgroundColor: colours.fill,
                    borderColor: colours.stroke,
                    borderStyle: colours.dashed ? 'dashed' : undefined
                  }}
                />
                {role.name}
              </button>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-[260px]">
              <p>{role.means}.</p>
              <p className="mt-1">
                Inserts <span className="font-mono">{classSyntax(role.name)}</span> at the cursor.
                {live
                  ? ' The swatch is the colour the brand pack gives it.'
                  : ' Nothing in this file uses it yet, so the engine has no colour to report.'}
              </p>
            </TooltipContent>
          </Tooltip>
        )
      })}
      <span className="ml-auto text-[10px] text-muted-foreground">never a hex code</span>
    </div>
  )
}

// ── no diagram yet ───────────────────────────────────────────────────────────

/**
 * A report with no `diagrams/` folder.
 *
 * `report-maker new --with-diagram` seeds one at scaffold time and there is no
 * command that adds one afterwards, so this writes the file itself — the one
 * place in this component that touches the vault directly, and only to create a
 * file the engine would have created a moment earlier.
 */
function Empty({
  vault,
  reportId,
  onCreated,
  className
}: {
  vault: string
  reportId: string | null
  onCreated?: (path: string) => void
  className?: string
}) {
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fileName = useMemo(() => diagramFileName(typed), [typed])

  async function create(): Promise<void> {
    if (!reportId || busy) return
    setBusy(true)
    setError(null)
    const target = diagramPath(vault, reportId, fileName)
    try {
      if (await window.api.files.exists(vault, target)) {
        setError(`${fileName} already exists in this report.`)
        return
      }
      await window.api.files.write(vault, target, SEED)
      onCreated?.(target)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  if (!reportId) {
    return (
      <div className={cn('flex h-full items-center justify-center px-8 text-center', className)}>
        <p className="max-w-sm text-sm text-muted-foreground">
          Open a <span className="font-mono">.mmd</span> from a report to edit it. A diagram outside
          a report has no design, so there is no brand pack to draw it with.
        </p>
      </div>
    )
  }

  return (
    <div className={cn('flex h-full items-center justify-center p-8', className)}>
      <div className="w-full max-w-md space-y-3">
        <h2 className="text-sm font-medium">No diagrams in this report</h2>
        <p className="text-[11.5px] leading-relaxed text-muted-foreground">
          A diagram is mermaid in <span className="font-mono">diagrams/*.mmd</span>, rendered by{' '}
          <span className="font-mono">report-maker diagrams</span> and placed with{' '}
          <span className="font-mono">diagram(…)</span>. It is cited like any other figure, and its
          colour comes from the brand pack — never from the file.
        </p>

        <div className="space-y-1">
          <Label className="text-[11px]">Name</Label>
          <div className="flex gap-1.5">
            <Input
              value={typed}
              autoFocus
              spellCheck={false}
              placeholder="example-flow"
              className="h-8 font-mono text-xs"
              onChange={(event) => setTyped(event.target.value)}
              onKeyDown={(event) => {
                if (event.key !== 'Enter') return
                event.preventDefault()
                void create()
              }}
            />
            <Button size="sm" disabled={busy} onClick={() => void create()}>
              {busy ? <Loader2 className="animate-spin" /> : <FilePlus2 />}
              Create
            </Button>
          </div>
          <p className="font-mono text-[10.5px] text-muted-foreground">
            reports/{reportId}/diagrams/{fileName}
          </p>
        </div>

        {error && <p className="text-[11px] text-destructive">{error}</p>}

        <p className="text-[10.5px] leading-relaxed text-muted-foreground">
          It starts as a small flowchart using <span className="font-mono">em-accent</span>,{' '}
          <span className="font-mono">em-muted</span> and <span className="font-mono">em-good</span>,
          so the emphasis classes are visible before anything has to be looked up.
        </p>
      </div>
    </div>
  )
}
