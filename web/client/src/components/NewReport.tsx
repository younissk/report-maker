import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { FolderPlus, Loader2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { api, ApiError, errorText, type NewReportRequest, type ReportRow } from '@/lib/api'
import {
  folderFor,
  groupsIn,
  lastAuthor,
  normaliseGroup,
  slugPreview,
  useTemplates,
} from '@/lib/reports'
import { guard } from '@/lib/session'
import { cn, useKeyboardOpen } from '@/lib/utils'

/**
 * Making a report: a bottom sheet on a phone, a dialog on a desktop.
 *
 * That is one component — `DialogContent` already switches shape at 1024px in
 * CSS — with one adjustment this file makes and the shared primitive cannot: when
 * the soft keyboard comes up, the sheet moves to the *top* of the screen. A
 * `position: fixed; bottom: 0` panel stays pinned to the bottom of the layout
 * viewport on iOS, which the keyboard is now covering, and the form the writer
 * is typing into disappears behind it.
 *
 * Every control is an argument to `report-maker new`, and this file runs none of
 * it: `POST /api/reports` returns the row the engine created and its `id` is
 * taken from there rather than derived. The one string derived on this side is
 * the folder preview, and {@link slugPreview} refuses to print one it cannot be
 * certain of.
 */

export type NewReportProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  /**
   * `list --json`, already held by the Reports screen. It feeds the group
   * suggestions and the author default; re-requesting a list the pane is holding
   * would be a subprocess for nothing.
   */
  rows: ReportRow[]
  /** Pre-filled group — the folder the writer was last looking at. */
  defaultGroup?: string
  /** The engine's id for what it created. The caller opens it in the Write tab. */
  onCreated: (id: string) => void
}

export function NewReport({
  open,
  onOpenChange,
  rows,
  defaultGroup = '',
  onCreated,
}: NewReportProps) {
  const fieldId = useId()
  const keyboard = useKeyboardOpen()

  const [title, setTitle] = useState('')
  const [group, setGroup] = useState(defaultGroup)
  const [kind, setKind] = useState('')
  // null means "not typed in yet", so the suggested author can keep updating as
  // the vault loads without ever overwriting something somebody wrote.
  const [author, setAuthor] = useState<string | null>(null)
  const [chosen, setChosen] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<{ message: string; detail: string | null } | null>(null)

  const { order, loading: loadingTemplates, error: templatesError } = useTemplates(open)

  // Read the incoming group through a ref, so a parent that recomputes
  // `defaultGroup` while the form is up cannot wipe a half-typed one.
  const initialGroup = useRef(defaultGroup)
  initialGroup.current = defaultGroup

  // Reopening must not show the last title, or the last failure. `chosen`
  // survives on purpose: somebody who opens this twice usually wants the design
  // they picked the first time.
  useEffect(() => {
    if (!open) return
    setTitle('')
    setGroup(initialGroup.current)
    setKind('')
    setAuthor(null)
    setBusy(false)
    setProblem(null)
  }, [open])

  // The engine's own default with no `--template` is `base`; otherwise the first
  // design this vault lists. Never a design that is not there.
  useEffect(() => {
    if (order.length === 0) return
    setChosen((current) => {
      if (current && order.some(([id]) => id === current)) return current
      if (order.some(([id]) => id === 'base')) return 'base'
      return order[0][0]
    })
  }, [order])

  const groups = useMemo(() => groupsIn(rows), [rows])
  const suggestedAuthor = useMemo(() => lastAuthor(rows), [rows])
  const authorValue = author ?? suggestedAuthor

  const slug = slugPreview(title)
  const folder = folderFor(group, slug)
  const canCreate = title.trim().length > 0 && !busy

  async function create(): Promise<void> {
    if (!canCreate) return
    setBusy(true)
    setProblem(null)

    const body: NewReportRequest = { title: title.trim() }
    const cleanGroup = normaliseGroup(group)
    if (cleanGroup) body.group = cleanGroup
    if (chosen) body.template = chosen
    if (kind.trim()) body.kind = kind.trim()
    if (authorValue.trim()) body.author = authorValue.trim()

    try {
      const created = await guard((signal) => api.createReport(body, signal))
      onOpenChange(false)
      onCreated(created.id)
    } catch (cause) {
      setProblem({
        message: errorText(cause),
        detail: cause instanceof ApiError ? cause.detail : null,
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => (busy ? undefined : onOpenChange(next))}>
      <DialogContent
        className={cn(
          // The keyboard is up: dock to the top edge, where it cannot reach.
          keyboard &&
            'top-[var(--safe-top)] bottom-auto max-h-[calc(100dvh-var(--safe-top))] rounded-t-none rounded-b-2xl lg:top-1/2 lg:bottom-auto lg:rounded-lg'
        )}
        onOpenAutoFocus={(event) => {
          // Radix would focus the first tabbable node, which is the close cross.
          // The title is what somebody came here to type.
          event.preventDefault()
          document.getElementById(`${fieldId}-title`)?.focus()
        }}
      >
        <form
          className="contents"
          onSubmit={(event) => {
            event.preventDefault()
            void create()
          }}
        >
          <DialogHeader>
            <DialogTitle>New report</DialogTitle>
            <DialogDescription>
              A folder under <span className="font-mono">reports/</span> with a{' '}
              <span className="font-mono">main.typ</span> and a{' '}
              <span className="font-mono">sources.yml</span>. Start with the sources — a
              claim with nothing to point at is an opinion.
            </DialogDescription>
          </DialogHeader>

          <DialogBody className="flex flex-col gap-4">
            <Field label="Title" htmlFor={`${fieldId}-title`} hint="required">
              <Input
                id={`${fieldId}-title`}
                value={title}
                disabled={busy}
                required
                autoComplete="off"
                enterKeyHint="done"
                placeholder="Company audit — Example Ltd"
                onChange={(event) => setTitle(event.target.value)}
              />
            </Field>

            <Field
              label="Folder"
              htmlFor={`${fieldId}-group`}
              hint="optional, nests as deep as you like"
            >
              <Input
                id={`${fieldId}-group`}
                value={group}
                disabled={busy}
                list={`${fieldId}-groups`}
                autoComplete="off"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                placeholder="clients/acme"
                onChange={(event) => setGroup(event.target.value)}
              />
              {groups.length > 0 && (
                <datalist id={`${fieldId}-groups`}>
                  {groups.map((name) => (
                    <option key={name} value={name} />
                  ))}
                </datalist>
              )}
              {groups.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {groups.slice(0, 6).map((name) => (
                    <Button
                      key={name}
                      type="button"
                      size="sm"
                      variant={normaliseGroup(group) === name ? 'secondary' : 'outline'}
                      disabled={busy}
                      className="h-11 px-2.5 font-mono text-[11px] lg:h-7"
                      onClick={() => setGroup(name)}
                    >
                      {name}
                    </Button>
                  ))}
                </div>
              )}
            </Field>

            <fieldset className="min-w-0">
              <legend className="mb-1.5 flex w-full items-baseline justify-between gap-2 text-xs font-medium">
                <span>Design</span>
                <span className="font-mono text-[10px] font-normal text-muted-foreground">
                  templates
                </span>
              </legend>

              {templatesError ? (
                <Output
                  command="report-maker templates --json"
                  message="The designs could not be listed."
                  detail={templatesError}
                />
              ) : loadingTemplates && order.length === 0 ? (
                <div className="flex flex-col gap-2">
                  {[0, 1, 2].map((n) => (
                    <Skeleton key={n} className="h-14 w-full" />
                  ))}
                </div>
              ) : order.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  This vault lists no designs. The report will be made with the
                  engine's own default.
                </p>
              ) : (
                <div className="max-h-64 overflow-y-auto overflow-x-hidden rounded-md border p-1">
                  <div className="flex flex-col gap-1">
                    {order.map(([id, template]) => (
                      <label
                        key={id}
                        className={cn(
                          'flex min-h-[var(--tap)] cursor-pointer items-start gap-2.5 rounded-md border border-transparent px-2.5 py-2 transition-colors',
                          'has-[:checked]:border-ring has-[:checked]:bg-accent',
                          'has-[:focus-visible]:ring-[3px] has-[:focus-visible]:ring-ring/50',
                          'active:bg-accent'
                        )}
                      >
                        <input
                          type="radio"
                          name={`${fieldId}-template`}
                          value={id}
                          checked={chosen === id}
                          disabled={busy}
                          onChange={() => setChosen(id)}
                          className="mt-1 size-4 shrink-0 accent-[var(--primary)]"
                        />
                        <span className="min-w-0 flex-1">
                          <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                            <span className="text-[13px] leading-tight font-medium break-anywhere">
                              {template.title || id}
                            </span>
                            <span className="font-mono text-[10px] text-muted-foreground break-anywhere">
                              {id}
                            </span>
                            {!template.builtin && (
                              <Badge variant="outline" className="px-1.5 py-0 text-[10px] font-normal">
                                vault
                              </Badge>
                            )}
                          </span>
                          {template.description && (
                            <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground break-anywhere">
                              {template.description}
                            </span>
                          )}
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </fieldset>

            <div className="grid gap-4 lg:grid-cols-2">
              <Field label="Kind" htmlFor={`${fieldId}-kind`} hint="optional">
                <Input
                  id={`${fieldId}-kind`}
                  value={kind}
                  disabled={busy}
                  autoComplete="off"
                  placeholder="the design's own"
                  onChange={(event) => setKind(event.target.value)}
                />
              </Field>
              <Field label="Author" htmlFor={`${fieldId}-author`} hint="optional">
                <Input
                  id={`${fieldId}-author`}
                  value={authorValue}
                  disabled={busy}
                  autoComplete="name"
                  placeholder="Author name"
                  onChange={(event) => setAuthor(event.target.value)}
                />
              </Field>
            </div>

            {/* What will appear on disk. A claim, so it has to be true. */}
            <div className="rounded-md border bg-muted/40 px-3 py-2">
              <div className="flex items-start gap-2">
                <FolderPlus className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                <code className="font-mono text-[11px] break-anywhere">{folder}/</code>
              </div>
              {slug.state === 'unknown' && (
                <p className="mt-1 pl-[1.375rem] text-[11px] text-muted-foreground">
                  The engine slugifies the title — the folder name is its decision,
                  not this page's guess.
                </p>
              )}
            </div>

            {problem && (
              <Output
                command="report-maker new"
                message={problem.message}
                detail={problem.detail}
              />
            )}
          </DialogBody>

          <DialogFooter>
            <Button type="submit" disabled={!canCreate}>
              {busy && <Loader2 className="animate-spin" aria-hidden />}
              {busy ? 'Creating…' : 'Create report'}
            </Button>
            <DialogClose asChild>
              <Button type="button" variant="outline" disabled={busy}>
                Cancel
              </Button>
            </DialogClose>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function Field({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: string
  hint?: string
  htmlFor: string
  children: ReactNode
}) {
  return (
    <div className="min-w-0">
      <label
        htmlFor={htmlFor}
        className="mb-1.5 flex items-baseline justify-between gap-2 text-xs font-medium"
      >
        <span>{label}</span>
        {hint && <span className="text-[10px] font-normal text-muted-foreground">{hint}</span>}
      </label>
      {children}
    </div>
  )
}

/**
 * A refusal, in the engine's own words.
 *
 * `detail` is usually stderr, and the engine's refusals name the command that
 * fixes them. Paraphrasing one into "something went wrong" throws away the only
 * useful half, so it is printed verbatim — inside its own scrolling box, which
 * is the one sanctioned way for something to be wider than the screen.
 */
function Output({
  command,
  message,
  detail,
}: {
  command: string
  message: string
  detail: string | null
}) {
  return (
    <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3" role="alert">
      <p className="font-mono text-[10px] text-muted-foreground">{command}</p>
      <p className="mt-1 text-sm leading-snug break-anywhere">{message}</p>
      {detail && (
        <pre className="scroll-x mt-2 max-h-40 overflow-y-auto rounded border bg-background p-2 font-mono text-[11px] whitespace-pre">
          {detail}
        </pre>
      )}
    </div>
  )
}
