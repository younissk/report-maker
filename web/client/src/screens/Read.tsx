import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertCircle,
  Check,
  Copy,
  Download,
  FileText,
  Hammer,
  Images,
  Link2,
  Loader2,
  RotateCw,
  Share2,
} from 'lucide-react'

import { useApp } from '@/App'
import { PageViewer, type PageViewerHandle } from '@/components/PageViewer'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { usePagesIndex } from '@/lib/pages'
import { api, errorText, isAbort, urls, type PagesIndex, type Share } from '@/lib/api'
import { guard } from '@/lib/session'
import { cn, useIsDesktop } from '@/lib/utils'

/**
 * The Read tab: the report as it will be read.
 *
 * Page images, never an embedded PDF. That is a decision and not a preference —
 * iOS Safari shows a PDF in an iframe as one dead page with no scroll and no way
 * in, and the engine already renders `out/pages/<id>/*.png` for consumers that
 * cannot host a PDF viewer. A desktop browser can host one, so it is offered the
 * PDF as an alternative; the pages stay the default so that what somebody sees
 * on a phone and what they see on a laptop is the same document.
 *
 * Nothing here knows anything about a vault. Whether the report is built is
 * whatever `GET /pages` answered, the share link is whatever `POST /share/:id`
 * returned, and the PDF is a URL the browser fetches for itself.
 */
export function Read() {
  const { reportId, revision, build, building, buildResult, setTab } = useApp()
  const { state, reload } = usePagesIndex(reportId, revision)
  const desktop = useIsDesktop()

  const viewer = useRef<PageViewerHandle>(null)
  const [page, setPage] = useState(1)
  const [jumping, setJumping] = useState(false)
  const [mode, setMode] = useState<'pages' | 'pdf'>('pages')

  // A rebuild writes new images to the same URLs. This is what tells the browser
  // to fetch them again, and it moves only when a build has actually finished.
  const [stamp, setStamp] = useState(0)
  useEffect(() => {
    if (buildResult) setStamp((n) => n + 1)
  }, [buildResult])

  const onPageChange = useCallback((n: number) => setPage(n), [])

  if (!reportId) {
    return (
      <Empty title="Nothing open yet.">
        <p>Choose a report and its built pages appear here.</p>
        <Button variant="outline" size="lg" className="mt-4" onClick={() => setTab('reports')}>
          Go to Reports
        </Button>
      </Empty>
    )
  }

  const index = state.status === 'ready' ? state.index : null

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar
        reportId={reportId}
        index={index}
        page={page}
        onJump={() => setJumping(true)}
        desktop={desktop}
        mode={mode}
        onMode={setMode}
      />

      {state.status === 'loading' && <Loading />}

      {state.status === 'unbuilt' && (
        <Unbuilt
          reportId={reportId}
          building={building}
          onBuild={build}
          stderr={buildResult && !buildResult.ok ? buildResult.stderr : null}
        />
      )}

      {state.status === 'failed' && (
        <Empty title="The pages could not be read.">
          <p className="break-anywhere">{state.message}</p>
          {state.detail && (
            /* The engine's refusals name the command that fixes them. Verbatim,
               scrolling inside its own box, never reflowed into an apology. */
            <pre className="scroll-x mt-3 max-h-48 w-full overflow-y-auto rounded-md border bg-muted p-3 text-left font-mono text-[11px] whitespace-pre">
              {state.detail}
            </pre>
          )}
          <Button variant="outline" size="lg" className="mt-4" onClick={reload}>
            <RotateCw aria-hidden />
            Try again
          </Button>
        </Empty>
      )}

      {index &&
        (desktop && mode === 'pdf' ? (
          <iframe
            key={`${reportId}:${stamp}`}
            src={urls.pdf(reportId)}
            title={`${reportId}, as a PDF`}
            className="min-h-0 flex-1 border-0 bg-muted"
          />
        ) : (
          <PageViewer
            ref={viewer}
            reportId={reportId}
            index={index}
            version={stamp}
            onPageChange={onPageChange}
          />
        ))}

      {index && (
        <JumpTo
          open={jumping}
          onOpenChange={setJumping}
          count={index.count}
          current={page}
          onPick={(n) => {
            setJumping(false)
            viewer.current?.scrollToPage(n)
          }}
        />
      )}
    </div>
  )
}

// ── the bar above the document ───────────────────────────────────────────────

function Toolbar({
  reportId,
  index,
  page,
  onJump,
  desktop,
  mode,
  onMode,
}: {
  reportId: string
  index: PagesIndex | null
  page: number
  onJump: () => void
  desktop: boolean
  mode: 'pages' | 'pdf'
  onMode: (mode: 'pages' | 'pdf') => void
}) {
  const [sharing, setSharing] = useState(false)
  const name = (index?.slug || reportId.split('/').pop() || 'report').replace(/[^\w.-]+/g, '-')

  return (
    <div className="flex shrink-0 items-center gap-2 border-b px-3 py-2">
      {index && index.count > 0 ? (
        <Button
          variant="outline"
          onClick={onJump}
          disabled={index.count < 2}
          aria-label={`Page ${page} of ${index.count}. Jump to a page.`}
          className="min-w-0 font-normal tabular-nums"
        >
          <span className="font-medium">{page}</span>
          <span className="text-muted-foreground">/ {index.count}</span>
        </Button>
      ) : (
        <span className="text-xs text-muted-foreground">Not built</span>
      )}

      <div className="ml-auto flex items-center gap-1">
        {desktop && index && (
          // A desktop browser renders a PDF perfectly well, so it is offered
          // one. It is never the default: the pages are what a phone gets, and
          // two readers of the same report should be looking at the same thing.
          <div className="mr-1 flex items-center rounded-md border p-0.5">
            <Toggle active={mode === 'pages'} onClick={() => onMode('pages')} label="Pages">
              <Images aria-hidden />
            </Toggle>
            <Toggle active={mode === 'pdf'} onClick={() => onMode('pdf')} label="PDF">
              <FileText aria-hidden />
            </Toggle>
          </div>
        )}

        <Button
          variant="ghost"
          size="icon"
          onClick={() => setSharing(true)}
          disabled={!index}
          aria-label="Share this report"
        >
          <Share2 aria-hidden />
        </Button>

        <Button
          asChild={!!index}
          variant="ghost"
          size="icon"
          disabled={!index}
          aria-label="Download the PDF"
        >
          {index ? (
            <a href={urls.pdf(reportId)} download={`${name}.pdf`} target="_blank" rel="noopener">
              <Download aria-hidden />
            </a>
          ) : (
            <Download aria-hidden />
          )}
        </Button>
      </div>

      <ShareDialog reportId={reportId} open={sharing} onOpenChange={setSharing} />
    </div>
  )
}

function Toggle({
  active,
  onClick,
  label,
  children,
}: {
  active: boolean
  onClick: () => void
  label: string
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'inline-flex h-8 items-center gap-1.5 rounded px-2 text-xs font-medium outline-none',
        'focus-visible:ring-[3px] focus-visible:ring-ring/50',
        active ? 'bg-secondary text-secondary-foreground' : 'text-muted-foreground active:bg-accent'
      )}
    >
      {children}
      {label}
    </button>
  )
}

// ── share ────────────────────────────────────────────────────────────────────

/**
 * The share link, and one sentence about what it is.
 *
 * That sentence is the product. A link to a PDF is a link to a PDF; this one
 * carries the archived page behind every citation, as it was on the day it was
 * cited, with its checksum. Nobody sending it knows that unless it is said, and
 * it is the only reason to send this rather than an export.
 */
function ShareDialog({
  reportId,
  open,
  onOpenChange,
}: {
  reportId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [share, setShare] = useState<Share | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ message: string; detail: string | null } | null>(null)
  const [copied, setCopied] = useState(false)
  const field = useRef<HTMLInputElement>(null)

  // A share is immutable and publishing one runs a build, so it is minted when
  // the dialog opens rather than speculatively on the way past.
  useEffect(() => {
    if (!open) return
    setShare(null)
    setError(null)
    setCopied(false)
    setBusy(true)
    const controller = new AbortController()
    guard((signal) => api.share(reportId, signal), controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return
        setShare(result)
        setBusy(false)
      })
      .catch((problem) => {
        if (isAbort(problem) || controller.signal.aborted) return
        setError({
          message: errorText(problem),
          detail: (problem as { detail?: string | null })?.detail ?? null,
        })
        setBusy(false)
      })
    return () => controller.abort()
  }, [open, reportId])

  const link = share ? absolute(share.url) : ''

  const copy = async () => {
    if (!link) return
    try {
      await navigator.clipboard.writeText(link)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard access is refused in plenty of ordinary situations. Selecting
      // the text is the fallback everybody already knows what to do with.
      field.current?.focus()
      field.current?.select()
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>Share this report</DialogTitle>
          <DialogDescription>
            Whoever opens this link sees the report and the archived evidence behind
            every citation — each cited page as it was on the day it was cited, with
            its checksum.
          </DialogDescription>
        </DialogHeader>

        <DialogBody>
          {busy && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" aria-hidden />
              Building the shareable copy…
            </div>
          )}

          {error && (
            <div className="text-sm">
              <p className="flex items-start gap-2 text-destructive break-anywhere">
                <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
                {error.message}
              </p>
              {error.detail && (
                <pre className="scroll-x mt-3 max-h-48 overflow-y-auto rounded-md border bg-muted p-3 font-mono text-[11px] whitespace-pre">
                  {error.detail}
                </pre>
              )}
            </div>
          )}

          {share && (
            <div className="flex flex-col gap-2">
              <label htmlFor="share-link" className="text-xs font-medium text-muted-foreground">
                Public link
              </label>
              <div className="flex items-center gap-2">
                <Input
                  id="share-link"
                  ref={field}
                  readOnly
                  value={link}
                  onFocus={(event) => event.currentTarget.select()}
                  className="font-mono"
                />
                <Button variant="outline" size="icon" onClick={copy} aria-label="Copy the link">
                  {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground" aria-live="polite">
                {copied ? 'Copied.' : 'Sharing again makes a new link; this one keeps showing this build.'}
              </p>
            </div>
          )}
        </DialogBody>

        <DialogFooter>
          {share && (
            <Button asChild>
              <a href={link} target="_blank" rel="noopener noreferrer">
                <Link2 aria-hidden />
                Open the link
              </a>
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** The server may answer with a path. A link somebody pastes into a message has
 *  to carry its origin. */
function absolute(url: string): string {
  try {
    return new URL(url, window.location.origin).href
  } catch {
    return url
  }
}

// ── jump to a page ───────────────────────────────────────────────────────────

function JumpTo({
  open,
  onOpenChange,
  count,
  current,
  onPick,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  count: number
  current: number
  onPick: (page: number) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby={undefined} className="lg:max-w-md">
        <DialogHeader>
          <DialogTitle>Jump to a page</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div className="grid grid-cols-5 gap-2 sm:grid-cols-6">
            {Array.from({ length: count }, (_, i) => i + 1).map((n) => (
              <button
                key={n}
                type="button"
                onClick={() => onPick(n)}
                aria-current={n === current ? 'page' : undefined}
                className={cn(
                  'tap flex items-center justify-center rounded-md border text-sm font-medium tabular-nums outline-none',
                  'focus-visible:ring-[3px] focus-visible:ring-ring/50 active:bg-accent',
                  n === current
                    ? 'border-foreground bg-secondary text-secondary-foreground'
                    : 'text-foreground'
                )}
              >
                {n}
              </button>
            ))}
          </div>
        </DialogBody>
      </DialogContent>
    </Dialog>
  )
}

// ── the states before there is a document ────────────────────────────────────

function Loading() {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center gap-2 overflow-hidden bg-muted/50 p-2">
      <Skeleton className="aspect-[1/1.414] w-full max-w-[900px] rounded-sm" />
      <Skeleton className="aspect-[1/1.414] w-full max-w-[900px] rounded-sm" />
    </div>
  )
}

/**
 * Never built.
 *
 * A build is one button and it is the only thing this pane can usefully offer,
 * so it is the whole screen rather than a line of grey text. When the last build
 * failed its stderr is here too: `all` ends with `check`, so a report that fails
 * the citation rule fails the build, and the findings are the answer rather than
 * an obstacle to it.
 */
function Unbuilt({
  reportId,
  building,
  onBuild,
  stderr,
}: {
  reportId: string
  building: boolean
  onBuild: () => void
  stderr: string | null
}) {
  return (
    <Empty title="Not built yet.">
      <p className="break-anywhere">
        <span className="font-mono text-[12px]">{reportId}</span> has no pages to read.
        Building runs the citation rule over it and renders every page.
      </p>
      {stderr && (
        <pre className="scroll-x mt-4 max-h-56 w-full overflow-y-auto rounded-md border bg-muted p-3 text-left font-mono text-[11px] whitespace-pre">
          {stderr}
        </pre>
      )}
      <Button size="lg" className="mt-4 w-full sm:w-auto" onClick={onBuild} disabled={building}>
        {building ? <Loader2 className="animate-spin" aria-hidden /> : <Hammer aria-hidden />}
        {building ? 'Building…' : 'Build it'}
      </Button>
    </Empty>
  )
}

function Empty({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="pane flex min-h-0 flex-1 items-center justify-center p-6">
      <div className="flex w-full max-w-md flex-col items-center text-center text-sm text-muted-foreground">
        <p className="text-base font-semibold text-foreground">{title}</p>
        <div className="mt-2 flex w-full flex-col items-center">{children}</div>
      </div>
    </div>
  )
}
