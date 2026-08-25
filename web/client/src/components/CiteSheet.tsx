import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'
import { Check, Copy, CornerDownLeft, Loader2, ShieldAlert } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { api, ApiError, isAbort, type CiteResult } from '@/lib/api'
import { hasEditor, requestInsert, useCopy } from '@/lib/evidence'
import { guard } from '@/lib/session'
import { formatBytes } from '@/lib/utils'

/**
 * Cite a URL: the clerical half of the citation rule, in one field.
 *
 * The server fetches the page, archives it under `snapshots/`, extracts the
 * metadata and writes the entry — and then hands back the key. Nothing about any
 * of that happens here; this is a text field, a `POST`, and the answer.
 *
 * **The failure path is the important one.** `cite` reaches out to a URL a
 * stranger typed, so the server refuses loopback, link-local, private and
 * metadata addresses before it opens a socket. That refusal names the address it
 * resolved and why it would not go there — which is exactly what somebody who
 * typed an internal hostname by mistake needs to read, and exactly what a
 * generic "could not fetch that page" would throw away. So the message and the
 * `detail` are rendered verbatim, with the server's own error code beside them.
 * There is no branch here that decides a refusal is too technical to show.
 */

export type CiteSheetProps = {
  /** The report the source is being added to. */
  reportId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Called after a source is written, so the bibliography can be re-read. */
  onCited?: (result: CiteResult) => void
}

/**
 * How much of the screen the soft keyboard is covering, in pixels.
 *
 * A sheet pinned to `bottom: 0` sits at the bottom of the *layout* viewport,
 * which does not move when the keyboard opens — so on iOS the field being typed
 * into ends up behind the keyboard. `visualViewport` is the only thing that
 * knows the real number, and offsetting by it is what keeps the input and its
 * button above the keys.
 *
 * Local to this file on purpose: it is the one sheet in the Evidence tab with a
 * text field in it. If a second one appears, this belongs in `lib/utils.ts`.
 */
function useKeyboardInset(): number {
  const subscribe = useCallback((onChange: () => void) => {
    const vv = window.visualViewport
    if (!vv) return () => {}
    vv.addEventListener('resize', onChange)
    vv.addEventListener('scroll', onChange)
    return () => {
      vv.removeEventListener('resize', onChange)
      vv.removeEventListener('scroll', onChange)
    }
  }, [])

  const get = useCallback(() => {
    const vv = window.visualViewport
    if (!vv) return 0
    const inset = window.innerHeight - vv.height - vv.offsetTop
    // The same 120px floor the shell uses: a retracting address bar is not a
    // keyboard, and shoving the sheet up for one would look like a glitch.
    return inset > 120 ? Math.round(inset) : 0
  }, [])

  return useSyncExternalStore(subscribe, get, () => 0)
}

type Failure = { code: string; message: string; detail: string | null }

export function CiteSheet({ reportId, open, onOpenChange, onCited }: CiteSheetProps) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<CiteResult | null>(null)
  const [failure, setFailure] = useState<Failure | null>(null)
  const { copied, copy } = useCopy()
  const inset = useKeyboardInset()

  // Every opening starts clean. A key from the last URL still on screen when the
  // sheet reopens is the kind of thing that gets pasted into the wrong sentence.
  useEffect(() => {
    if (!open) return
    setUrl('')
    setResult(null)
    setFailure(null)
    setBusy(false)
  }, [open])

  const submit = useCallback(() => {
    const trimmed = url.trim()
    if (!reportId || !trimmed || busy) return
    setBusy(true)
    setFailure(null)
    setResult(null)
    guard((signal) => api.cite(reportId, trimmed, signal))
      .then((cited) => {
        setResult(cited)
        setBusy(false)
        onCited?.(cited)
      })
      .catch((error: unknown) => {
        if (isAbort(error)) return
        setBusy(false)
        if (error instanceof ApiError) {
          setFailure({ code: error.code, message: error.message, detail: error.detail })
        } else {
          setFailure({
            code: 'unknown',
            message: error instanceof Error ? error.message : String(error),
            detail: null,
          })
        }
      })
  }, [url, reportId, busy, onCited])

  const key = result ? `@${result.key}` : ''

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        // The whole sheet rides above the keyboard rather than under it.
        style={inset > 0 ? { bottom: inset } : undefined}
        className="lg:bottom-auto"
      >
        <DialogHeader>
          <DialogTitle>Cite a URL</DialogTitle>
          <DialogDescription>
            The page is fetched, archived under{' '}
            <span className="font-mono">snapshots/</span> with its sha256 and the
            moment it was fetched, and written into{' '}
            <span className="font-mono">sources.yml</span>. What you get back is a
            key to cite with.
          </DialogDescription>
        </DialogHeader>

        <DialogBody className="flex flex-col gap-4">
          <form
            onSubmit={(event) => {
              event.preventDefault()
              submit()
            }}
            className="flex flex-col gap-2"
          >
            <label htmlFor="cite-url" className="text-xs font-medium text-muted-foreground">
              Page address
            </label>
            <Input
              id="cite-url"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              // `url` rather than `text`: the phone keyboard gains a slash and a
              // .com key, and loses the autocapitalise that breaks a hostname.
              type="url"
              inputMode="url"
              enterKeyHint="go"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              placeholder="https://example.com/pricing"
              disabled={busy || !reportId}
              aria-invalid={failure ? true : undefined}
              aria-describedby={failure ? 'cite-error' : undefined}
            />
            {/* The action sits directly under the field and inside the scrolling
                body — not in a footer bar the keyboard would cover. */}
            <Button type="submit" size="lg" disabled={busy || !url.trim() || !reportId}>
              {busy ? <Loader2 className="animate-spin" aria-hidden /> : null}
              {busy ? 'Fetching and archiving…' : 'Cite'}
            </Button>
            {!reportId && (
              <p className="text-xs text-muted-foreground">
                Select a report first — a source belongs to the report that cites it.
              </p>
            )}
          </form>

          {failure && (
            <div
              id="cite-error"
              role="alert"
              className="flex flex-col gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <ShieldAlert className="size-4 shrink-0 text-destructive" aria-hidden />
                <Badge variant="error" className="font-mono">
                  {failure.code}
                </Badge>
              </div>
              {/* Verbatim. The refusal names the address it resolved and why it
                  would not go there, and that sentence is the answer. */}
              <p className="text-sm leading-snug break-anywhere">{failure.message}</p>
              {failure.detail && (
                <pre className="scroll-x max-h-48 overflow-y-auto rounded-md border bg-background p-2 font-mono text-[11px] whitespace-pre">
                  {failure.detail}
                </pre>
              )}
            </div>
          )}

          {result && (
            <div className="flex flex-col gap-3 rounded-lg border p-3">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="font-mono text-base font-semibold break-anywhere">{key}</span>
                {!result.created && (
                  <Badge variant="secondary">already a source</Badge>
                )}
              </div>

              {result.title && (
                <p className="text-sm leading-snug break-anywhere">{result.title}</p>
              )}
              <p className="font-mono text-[11px] text-muted-foreground break-anywhere">
                {result.url}
              </p>

              {result.snapshot && (
                <p className="font-mono text-[10px] text-muted-foreground break-anywhere">
                  archived · {formatBytes(result.snapshot.bytes)} ·{' '}
                  {result.snapshot.sha256.slice(0, 16)}…
                </p>
              )}

              {!result.created && (
                <p className="text-xs text-muted-foreground break-anywhere">
                  This URL was already in <span className="font-mono">sources.yml</span> and
                  kept its key. Nothing was overwritten — an archive you are willing to
                  overwrite is not an archive.
                </p>
              )}

              <div className="flex flex-col gap-2 lg:flex-row">
                <Button
                  variant="outline"
                  className="w-full lg:w-auto"
                  onClick={() => void copy(key, result.key)}
                >
                  {copied === result.key ? (
                    <Check aria-hidden />
                  ) : (
                    <Copy aria-hidden />
                  )}
                  {copied === result.key ? 'Copied' : `Copy ${key}`}
                </Button>
                {hasEditor() && (
                  <Button
                    className="w-full lg:w-auto"
                    onClick={() => {
                      requestInsert({ report: reportId, text: key })
                      onOpenChange(false)
                    }}
                  >
                    <CornerDownLeft aria-hidden />
                    Insert at the cursor
                  </Button>
                )}
              </div>

              <Button
                variant="ghost"
                className="w-full lg:w-auto lg:self-start"
                onClick={() => {
                  setResult(null)
                  setUrl('')
                }}
              >
                Cite another
              </Button>
            </div>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  )
}
