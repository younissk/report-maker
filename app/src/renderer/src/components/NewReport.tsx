import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { Check, ChevronsUpDown, FolderPlus, Loader2, TriangleAlert } from 'lucide-react'
import type { ReportRow, Run, TemplateRow } from '../../../shared/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
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
import { Popover, PopoverAnchor, PopoverContent } from '@/components/ui/popover'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { loadReports, loadTemplates } from '@/lib/designs'
import { describeError } from '@/lib/sources'
import { useThumb } from '@/lib/thumbs'
import { cn } from '@/lib/utils'

/**
 * ⌘N — the dialog that means nobody has to type `report-maker new`.
 *
 * Every control here is an argument to that one command: the designs are
 * `templates --json`, the groups are the `group` column of `list --json`, and
 * the folder that appears on disk is whatever the engine decides to create. The
 * app scaffolds nothing; it fills in flags and reads back what happened.
 *
 * Two consequences shape the whole file.
 *
 * — **The folder preview is a claim, so it has to be true.** `scaffold.slugify`
 *   is `[^a-z0-9]+ → -` over a lowercased title, which JavaScript reproduces
 *   exactly for ASCII. It may *not* reproduce it for a non-ASCII letter, whose
 *   case mapping is a Unicode table this file has no business copying — so for
 *   those titles the preview says the engine will slugify it, rather than
 *   printing a path that might turn out to be a different folder.
 * — **Which report was created is asked, not computed.** Once `new` exits,
 *   `list --json` is read again and the id that was not there before is the new
 *   report. Deriving it from the form would mean a second copy of the group
 *   normalisation, the slug, and the name of the reports root — which a vault's
 *   `report-maker.toml` is free to change.
 */

type Props = {
  vault: string
  open: boolean
  onOpenChange: (open: boolean) => void
  /** `list --json`, when the shell already has it — it feeds the group list, the
   *  design thumbnails and the author default, and re-spawning the CLI for a
   *  list the window is already holding is a spawn for nothing. */
  reports?: ReportRow[]
  /** Pre-filled group — the folder the tree selection sits in, say. */
  defaultGroup?: string
  /**
   * Pre-selected design. The Designs screen's "Use for a new report" hands over
   * the id of the card that was clicked, and dropping it would open a dialog on
   * `base` a moment after the writer chose something else.
   *
   * It is a preference, not a constraint: an id this vault does not list falls
   * back to the engine's own default, the same as if nothing had been passed.
   */
  defaultTemplate?: string
  /** Bumped by the shell after a build, so thumbnails follow a rebuild. */
  revision?: number
  /** The engine's id for the report it created (`clients/acme/2026-08-18-audit`).
   *  The shell opens its `main.typ`. */
  onCreated: (reportId: string) => void
}

type Problem = {
  command: string
  output: string
  note: string | null
  /** A run that failed is framed as a failure; one that worked but left the app
   *  unable to name the report is not. */
  failed: boolean
}

/** How much of the folder name this side can honestly claim to know. */
type Slug = { text: string; state: 'ready' | 'empty' | 'unknown' }

// ── what the engine would do ─────────────────────────────────────────────────

/** `engine/scaffold.py:slugify`, transcribed. Only ever shown behind `derivable`. */
function slugify(title: string): string {
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return slug || 'report'
}

/**
 * True when the transcription above is certain to agree with Python's.
 *
 * Lowercasing is identical on both sides for ASCII, and every non-letter — an
 * em dash, a Devanagari digit, an emoji — is replaced by `-` in both without
 * either implementation consulting a case table. A non-ASCII *letter* is the one
 * place the two tables could disagree, and one wrong path in the preview costs
 * more trust than admitting the engine decides.
 */
function derivable(title: string): boolean {
  return ![...title].some((ch) => ch.charCodeAt(0) > 127 && /\p{L}/u.test(ch))
}

/** The engine's `into` normalisation: `(into or "").strip("/")`, plus the
 *  trimming this side does before passing the flag, so preview and argument are
 *  the same string. */
function normaliseGroup(group: string): string {
  return group.trim().replace(/^\/+|\/+$/g, '')
}

/** Today, locally. `toISOString()` is UTC and files an evening report under
 *  tomorrow for anyone east of Greenwich. */
function todayISO(): string {
  const now = new Date()
  const pad = (value: number): string => String(value).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

function slugFor(title: string): Slug {
  if (!title.trim()) return { text: '…', state: 'empty' }
  if (!derivable(title)) return { text: '…', state: 'unknown' }
  return { text: slugify(title), state: 'ready' }
}

// ── reading the vault ────────────────────────────────────────────────────────

/** `list --json` spreads each report's own metadata into its row; `author` is
 *  one of the extras the shared type does not promise. */
type Row = ReportRow & { author?: string }

/**
 * The author of the most recent report, offered as the default for the next one.
 *
 * A vault is usually one person's, and retyping your own name is the kind of
 * friction that makes people scaffold by hand. It is a suggestion in an editable
 * field, taken from what the engine printed — not a fact the app stores.
 */
function lastAuthor(rows: Row[]): string {
  const written = rows.filter((row) => (row.author ?? '').trim().length > 0)
  const newest = [...written].sort((a, b) => (b.date ?? '').localeCompare(a.date ?? ''))[0]
  return newest?.author?.trim() ?? ''
}

/** Every group that exists, plus the folders above them: filing a report beside
 *  `clients/acme` often means filing it in `clients`. */
function groupsIn(rows: Row[]): string[] {
  const seen = new Set<string>()
  for (const row of rows) if (row.group) seen.add(row.group)
  for (const group of [...seen]) {
    const parts = group.split('/')
    for (let i = 1; i < parts.length; i += 1) seen.add(parts.slice(0, i).join('/'))
  }
  return [...seen].sort()
}

/** The report whose cover best stands in for a design: something that has been
 *  built at all, and failing a tie, the most recent. */
function better(candidate: Row, current: Row): boolean {
  if (candidate.built !== current.built) return candidate.built
  return (candidate.date ?? '') > (current.date ?? '')
}

/**
 * The id the engine just created.
 *
 * The difference between two `list --json` runs, which is the engine's own
 * answer. The printed folder only breaks a tie — possible when something else
 * wrote to the vault while the command ran — and even then it is matched back
 * against a row rather than believed on its own.
 */
async function createdId(vault: string, before: Set<string>, stdout: string): Promise<string | null> {
  const after = await loadReports(vault).catch(() => [])
  const fresh = after.map((row) => row.id).filter((id) => !before.has(id))
  if (fresh.length === 1) return fresh[0]

  const printed = /^\s*→\s*(.+)\/main\.typ\s*$/m.exec(stdout)
  const folder = printed ? (printed[1].split('/').pop() ?? '') : ''
  if (folder) {
    const pool = fresh.length > 0 ? fresh : after.map((row) => row.id)
    const match = pool.find((id) => id.split('/').pop() === folder)
    if (match) return match
  }
  return fresh[0] ?? null
}

// ── the dialog ───────────────────────────────────────────────────────────────

export function NewReport({
  vault,
  open,
  onOpenChange,
  reports,
  defaultGroup = '',
  defaultTemplate,
  revision = 0,
  onCreated
}: Props) {
  const fieldId = useId()
  const [title, setTitle] = useState('')
  const [group, setGroup] = useState(defaultGroup)
  const [date, setDate] = useState(todayISO)
  const [kind, setKind] = useState('')
  // null means "not chosen yet", so the suggested author can keep updating as the
  // vault loads without ever overwriting something typed.
  const [author, setAuthor] = useState<string | null>(null)
  const [diagram, setDiagram] = useState(false)
  const [chosen, setChosen] = useState('base')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<Problem | null>(null)
  const [exists, setExists] = useState(false)

  const [templates, setTemplates] = useState<Record<string, TemplateRow>>({})
  const [loadingTemplates, setLoadingTemplates] = useState(false)
  const [templatesError, setTemplatesError] = useState<string | null>(null)
  const [loadedRows, setLoadedRows] = useState<Row[]>([])

  const rows: Row[] = reports ?? loadedRows

  // Read through a ref, so a parent that recomputes `defaultGroup` while the
  // dialog is up cannot wipe a half-typed form.
  const initialGroup = useRef(defaultGroup)
  initialGroup.current = defaultGroup
  const initialTemplate = useRef(defaultTemplate)
  initialTemplate.current = defaultTemplate

  // Reopening must not show the last run, or the last title.
  useEffect(() => {
    if (!open) return
    setTitle('')
    setGroup(initialGroup.current)
    // Only when one was named. Left alone, `chosen` keeps the last design used,
    // which is the right default for somebody who opens this dialog repeatedly.
    if (initialTemplate.current) setChosen(initialTemplate.current)
    setDate(todayISO())
    setKind('')
    setAuthor(null)
    setDiagram(false)
    setBusy(false)
    setProblem(null)
    setExists(false)
  }, [open])

  useEffect(() => {
    if (!open || reports) return
    let stale = false
    void loadReports(vault)
      .then((found) => {
        if (!stale) setLoadedRows(found)
      })
      .catch(() => undefined)
    return () => {
      stale = true
    }
  }, [open, reports, vault])

  useEffect(() => {
    if (!open) return
    let stale = false
    setLoadingTemplates(true)
    setTemplatesError(null)
    void loadTemplates(vault)
      .then((found) => {
        if (stale) return
        setTemplates(found)
        // The last design used, when it still exists; otherwise the engine's own
        // default, which is what `report-maker new` picks with no --template.
        setChosen((current) =>
          current && found[current] ? current : found['base'] ? 'base' : (Object.keys(found).sort()[0] ?? '')
        )
      })
      .catch((err) => {
        if (!stale) setTemplatesError(describeError(err))
      })
      .finally(() => {
        if (!stale) setLoadingTemplates(false)
      })
    return () => {
      stale = true
    }
  }, [open, vault])

  const groups = useMemo(() => groupsIn(rows), [rows])
  const suggestedAuthor = useMemo(() => lastAuthor(rows), [rows])

  /** design id → the report whose cover illustrates it. */
  const examples = useMemo(() => {
    const best = new Map<string, Row>()
    for (const row of rows) {
      const current = best.get(row.template)
      if (!current || better(row, current)) best.set(row.template, row)
    }
    return best
  }, [rows])

  const order = useMemo(
    () =>
      Object.entries(templates).sort(([idA, a], [idB, b]) =>
        (a.group || '').localeCompare(b.group || '') ||
        (a.title || idA).localeCompare(b.title || idB)
      ),
    [templates]
  )

  const cleanGroup = normaliseGroup(group)
  const slug = slugFor(title)
  const folder = `reports/${cleanGroup ? `${cleanGroup}/` : ''}${date || todayISO()}-${slug.text}`

  // Best effort, and deliberately only when the name is known: `new` refuses to
  // overwrite an existing folder, and saying so before the click is cheaper than
  // reading it out of a failed run.
  useEffect(() => {
    if (!open || slug.state !== 'ready') {
      setExists(false)
      return
    }
    let stale = false
    const timer = setTimeout(() => {
      void window.api.files
        .exists(vault, `${vault}/${folder}`)
        .then((found) => {
          if (!stale) setExists(found)
        })
        .catch(() => undefined)
    }, 250)
    return () => {
      stale = true
      clearTimeout(timer)
    }
  }, [open, vault, folder, slug.state])

  const ready = title.trim().length > 0 && chosen.length > 0 && !busy

  async function submit(): Promise<void> {
    const name = title.trim()
    if (!name || !chosen || busy) return
    setBusy(true)
    setProblem(null)

    const args = ['new', name, '--template', chosen]
    if (cleanGroup) args.push('--into', cleanGroup)
    if (date) args.push('--date', date)
    if (kind.trim()) args.push('--kind', kind.trim())
    if ((author ?? suggestedAuthor).trim()) args.push('--author', (author ?? suggestedAuthor).trim())
    if (diagram) args.push('--with-diagram')

    const before = new Set((await loadReports(vault).catch(() => [])).map((row) => row.id))

    let run: Run
    try {
      run = await window.api.engine.run(vault, args)
    } catch (err) {
      setBusy(false)
      setProblem({
        command: `report-maker ${args.join(' ')}`,
        output: describeError(err),
        note: 'The engine could not be run.',
        failed: true
      })
      return
    }

    if (run.code !== 0) {
      // Verbatim: `new` refusing an existing folder, a design that does not
      // exist, a starter with no main.typ — the text is the thing to act on.
      setBusy(false)
      setProblem({
        command: run.command,
        output: (run.stderr || run.stdout || `exit ${run.code}`).trimEnd(),
        note: null,
        failed: true
      })
      return
    }

    const id = await createdId(vault, before, run.stdout)
    setBusy(false)
    if (!id) {
      setProblem({
        command: run.command,
        output: (run.stdout + run.stderr).trimEnd(),
        note: 'The report was created, but `list --json` does not name it — reload the vault to open it.',
        failed: false
      })
      return
    }
    onOpenChange(false)
    onCreated(id)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>New report</DialogTitle>
          <DialogDescription>
            The engine scaffolds the folder — <span className="font-mono">report-maker new</span>.
            Its <span className="font-mono">sources.yml</span> comes first: a claim with no key to
            point at is an opinion.
          </DialogDescription>
        </DialogHeader>

        <div className="-mr-2 max-h-[58vh] space-y-4 overflow-y-auto pr-2">
          <Field label="Title" hint="required" htmlFor={`${fieldId}-title`}>
            <Input
              id={`${fieldId}-title`}
              autoFocus
              value={title}
              disabled={busy}
              placeholder="Vendor security review"
              onChange={(event) => setTitle(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  void submit()
                }
              }}
            />
          </Field>

          <div className="grid gap-4 sm:grid-cols-[1fr_11rem]">
            <Field label="Group" hint="--into" htmlFor={`${fieldId}-group`}>
              <GroupField
                id={`${fieldId}-group`}
                value={group}
                groups={groups}
                disabled={busy}
                onChange={setGroup}
                onSubmit={() => void submit()}
              />
            </Field>
            <Field label="Date" hint="--date" htmlFor={`${fieldId}-date`}>
              <Input
                id={`${fieldId}-date`}
                type="date"
                value={date}
                disabled={busy}
                className="tabular-nums"
                onChange={(event) => setDate(event.target.value)}
              />
            </Field>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">
              Design
              <span className="font-mono text-[10px] font-normal text-muted-foreground">
                --template
              </span>
            </Label>
            {templatesError ? (
              <Output command="report-maker templates --json" output={templatesError} />
            ) : (
              <div className="max-h-[19rem] overflow-y-auto rounded-md border border-border p-2">
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {loadingTemplates && order.length === 0
                    ? [0, 1, 2].map((n) => <Skeleton key={n} className="h-[11.5rem] rounded-md" />)
                    : order.map(([id, row]) => (
                        <TemplateCard
                          key={id}
                          vault={vault}
                          id={id}
                          row={row}
                          example={examples.get(id) ?? null}
                          revision={revision}
                          selected={id === chosen}
                          onSelect={() => setChosen(id)}
                        />
                      ))}
                </div>
                {!loadingTemplates && order.length === 0 && (
                  <p className="px-1 py-3 text-xs text-muted-foreground">
                    This vault lists no designs. <span className="font-mono">templates --json</span>{' '}
                    came back empty.
                  </p>
                )}
              </div>
            )}
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Kind" hint="--kind" htmlFor={`${fieldId}-kind`}>
              <Input
                id={`${fieldId}-kind`}
                value={kind}
                disabled={busy}
                placeholder="the design's own"
                onChange={(event) => setKind(event.target.value)}
              />
            </Field>
            <Field label="Author" hint="--author" htmlFor={`${fieldId}-author`}>
              <Input
                id={`${fieldId}-author`}
                value={author ?? suggestedAuthor}
                disabled={busy}
                placeholder="Author Name"
                onChange={(event) => setAuthor(event.target.value)}
              />
            </Field>
          </div>

          <div className="flex items-center justify-between gap-4 rounded-md border border-border px-3 py-2">
            <Label htmlFor={`${fieldId}-diagram`} className="flex-col items-start gap-0.5">
              <span className="text-xs">Include the example diagram</span>
              <span className="text-[11px] font-normal text-muted-foreground">
                Copies the design's <span className="font-mono">.mmd</span>. Render it with{' '}
                <span className="font-mono">diagrams</span> before building — an unrendered diagram
                fails <span className="font-mono">check</span>.
              </span>
            </Label>
            <Switch
              id={`${fieldId}-diagram`}
              checked={diagram}
              disabled={busy}
              onCheckedChange={setDiagram}
            />
          </div>

          <div className="rounded-md border border-border bg-muted/40 px-3 py-2">
            <div className="flex items-center gap-2">
              <FolderPlus className="size-3.5 shrink-0 text-muted-foreground" />
              <code className="truncate font-mono text-xs">{folder}/</code>
            </div>
            {slug.state === 'unknown' && (
              <p className="mt-1 pl-[1.375rem] text-[11px] text-muted-foreground">
                The engine will slugify this — the folder name is{' '}
                <span className="font-mono">new</span>&apos;s to decide, and this title is outside
                what the app can predict character for character.
              </p>
            )}
            {slug.state === 'empty' && (
              <p className="mt-1 pl-[1.375rem] text-[11px] text-muted-foreground">
                The title becomes the folder name.
              </p>
            )}
            {exists && (
              // No warning token to reach for, so the triangle carries it — the
              // same convention the Problems panel uses.
              <p className="mt-1 flex items-start gap-1.5 pl-[1.375rem] text-[11px] text-muted-foreground">
                <TriangleAlert className="mt-px size-3 shrink-0" />
                That folder already exists. <span className="font-mono">new</span> will refuse it —
                change the title, the date or the group.
              </p>
            )}
          </div>

          {problem && (
            <Output
              command={problem.command}
              output={problem.output}
              note={problem.note}
              failed={problem.failed}
            />
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button size="sm" disabled={!ready} onClick={() => void submit()}>
            {busy && <Loader2 className="size-3.5 animate-spin" />}
            {busy ? 'Creating…' : 'Create report'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── pieces ───────────────────────────────────────────────────────────────────

function Field({
  label,
  hint,
  htmlFor,
  children
}: {
  label: string
  hint?: string
  htmlFor: string
  children: React.ReactNode
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={htmlFor} className="text-xs">
        {label}
        {hint && (
          <span className="font-mono text-[10px] font-normal text-muted-foreground">{hint}</span>
        )}
      </Label>
      {children}
    </div>
  )
}

/** What the CLI said, unedited and scrollable rather than clipped. */
function Output({
  command,
  output,
  note,
  failed = true
}: {
  command: string
  output: string
  note?: string | null
  failed?: boolean
}) {
  return (
    <div className="space-y-1">
      {note && <p className="text-[11px] text-muted-foreground">{note}</p>}
      <p className="font-mono text-[10.5px] break-all text-muted-foreground">{command}</p>
      <pre
        className={cn(
          'max-h-40 overflow-auto rounded-md border p-2 font-mono text-[11px] whitespace-pre-wrap',
          failed ? 'border-destructive/50' : 'border-border'
        )}
      >
        {output}
      </pre>
    </div>
  )
}

/**
 * A design, with a cover if the vault can show one.
 *
 * The thumbnail is the first page of a report already built with this design —
 * the honest illustration of what you are about to get, and free, because
 * `pages` has already rendered it. When no report uses the design, or none has
 * been built, the card falls back to a typographic stand-in rather than a
 * borrowed image from a different design.
 */
function TemplateCard({
  vault,
  id,
  row,
  example,
  revision,
  selected,
  onSelect
}: {
  vault: string
  id: string
  row: TemplateRow
  example: Row | null
  revision: number
  selected: boolean
  onSelect: () => void
}) {
  const { url, loading } = useThumb(vault, example?.built ? example.id : null, revision)

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      title={row.description}
      className={cn(
        'flex flex-col overflow-hidden rounded-md border border-border text-left transition-colors',
        selected ? 'border-primary ring-1 ring-primary' : 'hover:border-muted-foreground/40'
      )}
    >
      <div className="relative aspect-4/3 w-full overflow-hidden border-b border-border bg-muted">
        {url ? (
          <img src={url} alt="" className="size-full object-cover object-top" />
        ) : loading ? (
          <Skeleton className="size-full rounded-none" />
        ) : (
          <Specimen
            reason={
              !example
                ? 'no report uses it yet'
                : example.built
                  ? 'no page image yet'
                  : 'not built yet'
            }
          />
        )}
        {selected && (
          <span className="absolute top-1 right-1 rounded-full bg-primary p-0.5 text-primary-foreground">
            <Check className="size-3" />
          </span>
        )}
      </div>
      <div className="flex flex-col gap-1 p-2">
        <span className="truncate text-xs font-medium">{row.title || id}</span>
        <div className="flex flex-wrap items-center gap-1">
          <Badge variant="outline" className="px-1.5 font-mono text-[9.5px] font-normal">
            {id}
          </Badge>
          {row.group && (
            <Badge variant="outline" className="px-1.5 text-[9.5px] font-normal">
              {row.group}
            </Badge>
          )}
          <Badge variant="secondary" className="px-1.5 text-[9.5px] font-normal">
            {row.builtin ? 'built-in' : 'vault'}
          </Badge>
        </div>
        <p className="line-clamp-2 text-[10.5px] leading-snug text-muted-foreground">
          {row.description}
        </p>
      </div>
    </button>
  )
}

/** A page of type, with no claim to be this design's page. */
function Specimen({ reason }: { reason: string }) {
  return (
    <div className="flex size-full flex-col justify-center gap-1.5 px-4">
      <div className="h-1 w-1/3 rounded-full bg-foreground/25" />
      <div className="h-2 w-4/5 rounded-full bg-foreground/40" />
      <div className="mt-1 h-1 w-full rounded-full bg-foreground/15" />
      <div className="h-1 w-11/12 rounded-full bg-foreground/15" />
      <div className="h-1 w-2/3 rounded-full bg-foreground/15" />
      <span className="mt-2 text-[9px] tracking-wide text-muted-foreground">{reason}</span>
    </div>
  )
}

/**
 * Free text with the groups that already exist offered underneath.
 *
 * A group is a folder path, so anything is legal and the field must stay typable
 * — the list is a shortcut, never a constraint. Focus stays in the input the
 * whole time (the popover never takes it, and the rows cancel their own
 * mousedown), which is what lets the arrow keys and Enter keep working on a
 * field that also has a menu.
 */
function GroupField({
  id,
  value,
  groups,
  disabled,
  onChange,
  onSubmit
}: {
  id: string
  value: string
  groups: string[]
  disabled: boolean
  onChange: (value: string) => void
  onSubmit: () => void
}) {
  const input = useRef<HTMLInputElement>(null)
  const [open, setOpen] = useState(false)
  const [index, setIndex] = useState(-1)

  const matches = useMemo(() => {
    const needle = value.trim().toLowerCase()
    return groups.filter((group) => group.toLowerCase().includes(needle))
  }, [groups, value])

  const showing = open && matches.length > 0

  const pick = (group: string): void => {
    onChange(group)
    setOpen(false)
    setIndex(-1)
    input.current?.focus()
  }

  return (
    <Popover open={showing} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <div className="relative">
          <Input
            id={id}
            ref={input}
            value={value}
            disabled={disabled}
            spellCheck={false}
            placeholder="clients/acme"
            className="pr-8"
            autoComplete="off"
            onChange={(event) => {
              onChange(event.target.value)
              setIndex(-1)
              setOpen(true)
            }}
            onFocus={() => setOpen(true)}
            onBlur={() => setOpen(false)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown') {
                event.preventDefault()
                setOpen(true)
                setIndex((current) => Math.min(current + 1, matches.length - 1))
              } else if (event.key === 'ArrowUp') {
                event.preventDefault()
                setIndex((current) => Math.max(current - 1, -1))
              } else if (event.key === 'Enter') {
                event.preventDefault()
                const highlighted = showing && index >= 0 ? matches[index] : null
                if (highlighted) pick(highlighted)
                else onSubmit()
              } else if (event.key === 'Escape' && showing) {
                // Escape belongs to the list while the list is open; letting it
                // through would close the dialog and throw the form away.
                event.preventDefault()
                event.stopPropagation()
                setOpen(false)
                setIndex(-1)
              }
            }}
          />
          <button
            type="button"
            tabIndex={-1}
            disabled={disabled}
            aria-label="Show existing groups"
            className="absolute inset-y-0 right-0 flex w-8 items-center justify-center text-muted-foreground hover:text-foreground disabled:opacity-50"
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => {
              setOpen((current) => !current)
              input.current?.focus()
            }}
          >
            <ChevronsUpDown className="size-3.5" />
          </button>
        </div>
      </PopoverAnchor>
      <PopoverContent
        align="start"
        sideOffset={4}
        className="max-h-56 w-(--radix-popover-trigger-width) overflow-y-auto p-1"
        onOpenAutoFocus={(event) => event.preventDefault()}
        onCloseAutoFocus={(event) => event.preventDefault()}
      >
        {matches.map((group, position) => (
          <button
            key={group}
            type="button"
            className={cn(
              'flex w-full items-center gap-2 rounded-sm px-2 py-1 text-left font-mono text-xs',
              position === index ? 'bg-accent text-accent-foreground' : 'hover:bg-accent'
            )}
            // Cancelling the mousedown keeps the input focused, so the blur that
            // would close this list before the click lands never happens.
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => pick(group)}
          >
            <span className="truncate">{group}</span>
          </button>
        ))}
      </PopoverContent>
    </Popover>
  )
}
