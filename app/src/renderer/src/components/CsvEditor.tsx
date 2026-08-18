import { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
import {
  Archive,
  Check,
  ChevronDown,
  Columns3,
  History,
  Loader2,
  Plus,
  Rows3,
  Save,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  TriangleAlert
} from 'lucide-react'
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import {
  DELIMITERS,
  cellAt,
  columnOfFinding,
  delimiterName,
  describeDialect,
  insertColumn,
  insertRow,
  removeColumn,
  removeRow,
  setCell,
  useCsv,
  width,
  type ColumnStat,
  type DataFinding,
  type RevisionRow,
  type ReviseSummary,
  type Sheet,
  type UseCsv
} from '@/lib/csv'
import { cn } from '@/lib/utils'

/**
 * How many body rows are rendered at once.
 *
 * A cap rather than a virtualiser: the grid is a reading and correcting surface
 * for a file small enough to live inside a report folder, and a 50,000-row
 * export is a thing to look at the head of, not to scroll. What matters is that
 * the number shown is *stated* — a table that silently renders the first 400
 * rows of 12,000 is the same class of lie as a data file that silently reports a
 * missing source as zero.
 */
const PAGE = 400

/**
 * The not-measured mark, and the words for it.
 *
 * A figure dash and "not measured", exactly as `engine/templates/base/data.typ`
 * prints an empty cell — the editor and the built page say the same thing about
 * the same cell, so nobody has to learn two vocabularies for one hole in the
 * data.
 */
const MISSING = '\u2012'
const MISSING_LABEL = 'not measured'

/** What the shell can ask of an open CSV. */
export type CsvEditorHandle = {
  save: () => Promise<void>
  isDirty: () => boolean
}

type Props = {
  vault: string
  /** Absolute path of the open `.csv` / `.tsv` / `.tab`. */
  path: string
  /** The report the file belongs to, as the engine names it. Without one there
   *  is no bibliography to be registered in, and the engine half is hidden. */
  reportId: string | null
  className?: string
  /** Told whenever the buffer's dirtiness changes, so the shell can show its
   *  unsaved marker without owning the buffer. */
  onDirtyChange?: (dirty: boolean) => void
  /** Something on disk changed — the file, or `sources.yml`. The shell re-reads
   *  what it caches: the tree, the sources panel, the findings. */
  onChanged?: () => void
  ref?: React.Ref<CsvEditorHandle>
}

type Cursor = { r: number; c: number }

/**
 * A CSV, as a grid, with the two things a spreadsheet will not do for you.
 *
 * **Empty cells are highlighted, and counted per column.** A blank cell in a
 * spreadsheet is invisible, and that invisibility is not a cosmetic problem. In
 * the failure this whole data layer was built around, a collector returned 0
 * where its database was absent, a derived label turned the 0 into "WHITE SPACE
 * (absent in AMS)", and that reached the front page of a report while the real
 * figure was 421. Nothing in the chain was a bug; a missing value and a measured
 * one were simply spelled the same way. An editor that makes absence visible at
 * the moment of entry is doing the one thing a spreadsheet structurally does
 * not, and the engine's own W007/W008/W009 findings are surfaced on the column
 * they are about rather than in a panel somewhere else.
 *
 * **The checksum is the banner, not a hidden error.** The file is registered in
 * `sources.yml` under a sha256, and E011 fires when the bytes stop matching.
 * Saving here therefore breaks the checksum on purpose, says so, and offers the
 * one sanctioned way through — `data revise`, which archives the outgoing
 * version under its own date before moving the recorded sha. Nothing
 * re-registers on save. A checksum a tool refreshes for you is not a checksum,
 * and the guarantee is the entire feature.
 */
export function CsvEditor({
  vault,
  path,
  reportId,
  className,
  onDirtyChange,
  onChanged,
  ref
}: Props) {
  const csv = useCsv(vault, path, reportId)
  const { sheet, columns, dirty, edit } = csv

  const grid = useRef<HTMLDivElement>(null)
  const [cursor, setCursor] = useState<Cursor | null>(null)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [limit, setLimit] = useState(PAGE)
  const [reviseOpen, setReviseOpen] = useState(false)

  const save = csv.save
  const notify = useRef(onChanged)
  notify.current = onChanged

  useImperativeHandle(ref, () => ({ save, isDirty: () => dirty }), [save, dirty])

  useEffect(() => {
    onDirtyChange?.(dirty)
  }, [dirty, onDirtyChange])

  // A different file is a different grid: nothing about where the cursor was in
  // the last one means anything in this one.
  useEffect(() => {
    setCursor(null)
    setEditing(false)
    setLimit(PAGE)
  }, [path])

  const header = sheet?.header ?? false
  const bodyStart = header ? 1 : 0
  const total = sheet ? Math.max(sheet.rows.length - bodyStart, 0) : 0
  const shown = Math.min(limit, total)
  const columnCount = sheet ? width(sheet) : 0

  const byColumn = useMemo(() => {
    const found = new Map<number, DataFinding[]>()
    for (const finding of csv.warnings) {
      const index = columnOfFinding(finding, columns)
      if (index === null) continue
      const existing = found.get(index)
      if (existing) existing.push(finding)
      else found.set(index, [finding])
    }
    return found
  }, [csv.warnings, columns])

  /** Findings about the file rather than about one of its columns — E011, W005,
   *  W006. They belong above the grid; a column badge has nowhere to put them. */
  const fileFindings = useMemo(
    () => csv.warnings.filter((finding) => columnOfFinding(finding, columns) === null),
    [csv.warnings, columns]
  )

  // ── moving and editing ─────────────────────────────────────────────────────

  const begin = useCallback(
    (r: number, c: number, seed?: string) => {
      if (!sheet) return
      setCursor({ r, c })
      setDraft(seed ?? cellAt(sheet, r, c))
      setEditing(true)
    },
    [sheet]
  )

  const move = useCallback(
    (dr: number, dc: number) => {
      if (!sheet) return
      const from = cursor ?? { r: bodyStart, c: 0 }
      const r = Math.min(Math.max(from.r + dr, 0), Math.max(sheet.rows.length - 1, 0))
      const c = Math.min(Math.max(from.c + dc, 0), Math.max(columnCount - 1, 0))
      // Walking off the rendered window grows it rather than trapping the cursor
      // at the cap. Done here rather than inside a `setCursor` updater: an
      // updater has to be pure, and React is entitled to run it twice.
      if (r - bodyStart >= limit) setLimit((value) => value + PAGE)
      setCursor({ r, c })
      setEditing(false)
    },
    [sheet, cursor, bodyStart, columnCount, limit]
  )

  const commit = useCallback(
    (dr: number, dc: number) => {
      if (!sheet || !cursor) return
      edit(setCell(sheet, cursor.r, cursor.c, draft))
      setEditing(false)
      if (dr !== 0 || dc !== 0) move(dr, dc)
    },
    [sheet, cursor, draft, edit, move]
  )

  // Focus follows the cursor, but only while nobody is typing — the input owns
  // the focus in that case, and stealing it back would end the edit.
  useEffect(() => {
    if (!cursor || editing) return
    const node = grid.current?.querySelector<HTMLElement>(
      `[data-cell="${cursor.r}:${cursor.c}"]`
    )
    node?.focus({ preventScroll: false })
  }, [cursor, editing])

  const onGridKey = useCallback(
    (event: React.KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        if (editing) commit(0, 0)
        void save()
        return
      }
      if (editing || !cursor || !sheet) return

      switch (event.key) {
        case 'ArrowUp':
          event.preventDefault()
          return move(-1, 0)
        case 'ArrowDown':
          event.preventDefault()
          return move(1, 0)
        case 'ArrowLeft':
          event.preventDefault()
          return move(0, -1)
        case 'ArrowRight':
          event.preventDefault()
          return move(0, 1)
        case 'Tab':
          event.preventDefault()
          return move(0, event.shiftKey ? -1 : 1)
        case 'Enter':
          event.preventDefault()
          return begin(cursor.r, cursor.c)
        case 'Backspace':
        case 'Delete':
          event.preventDefault()
          return edit(setCell(sheet, cursor.r, cursor.c, ''))
        case 'Escape':
          event.preventDefault()
          setCursor(null)
          return
        default:
          break
      }
      // Typing over a selected cell replaces it, the way every grid behaves.
      if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault()
        begin(cursor.r, cursor.c, event.key)
      }
    },
    [editing, cursor, sheet, move, begin, commit, edit, save]
  )

  const onInputKey = useCallback(
    (event: React.KeyboardEvent<HTMLInputElement>) => {
      switch (event.key) {
        case 'Escape':
          // Cancels: the draft is thrown away and the cell keeps what the file
          // says. Nothing has touched the sheet yet, so there is nothing to undo.
          event.preventDefault()
          event.stopPropagation()
          setEditing(false)
          return
        case 'Enter':
          event.preventDefault()
          return commit(1, 0)
        case 'Tab':
          event.preventDefault()
          return commit(0, event.shiftKey ? -1 : 1)
        case 'ArrowUp':
          event.preventDefault()
          return commit(-1, 0)
        case 'ArrowDown':
          event.preventDefault()
          return commit(1, 0)
        default:
          break
      }
    },
    [commit]
  )

  // ── shape edits ────────────────────────────────────────────────────────────

  const apply = useCallback((next: (current: Sheet) => Sheet) => {
    // Guarded through the hook's own copy so a stale closure cannot resurrect an
    // older sheet: every one of these is a pure function of the current one.
    if (!sheet) return
    edit(next(sheet))
  }, [sheet, edit])

  // ── render ─────────────────────────────────────────────────────────────────

  if (csv.loading && !sheet) {
    return (
      <div className={cn('space-y-2 p-4', className)}>
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }

  if (!sheet) {
    return (
      <div className={cn('flex h-full items-center justify-center p-8', className)}>
        <div className="max-w-md space-y-2 text-center">
          <p className="text-sm">This file could not be read as text.</p>
          {csv.error && (
            <pre className="overflow-auto rounded-md border border-destructive/50 p-2 text-left font-mono text-[11px] whitespace-pre-wrap">
              {csv.error}
            </pre>
          )}
          <Button size="xs" variant="secondary" onClick={csv.reload}>
            Try again
          </Button>
        </div>
      </div>
    )
  }

  return (
    <TooltipProvider delayDuration={200}>
      <div className={cn('flex h-full min-h-0 flex-col', className)}>
        <ChecksumBanner
          csv={csv}
          reportId={reportId}
          onRevise={() => setReviseOpen(true)}
          onRegister={async () => {
            if (await csv.register()) notify.current?.()
          }}
        />

        {/* ── toolbar ─────────────────────────────────────────────────────── */}
        <div className="flex shrink-0 flex-wrap items-center gap-1.5 px-2 py-1.5 text-[11px]">
          <span className="flex items-center gap-1 text-muted-foreground">
            <Rows3 className="size-3" />
            {total} {total === 1 ? 'row' : 'rows'}
            <Columns3 className="ml-1 size-3" />
            {columnCount} {columnCount === 1 ? 'column' : 'columns'}
          </span>

          <Separator orientation="vertical" className="mx-1 h-4" />

          <DropdownMenu>
            <Tooltip>
              <TooltipTrigger asChild>
                <DropdownMenuTrigger asChild>
                  <Button size="xs" variant="ghost" className="font-mono text-[10.5px]">
                    {describeDialect(sheet.dialect)}
                    <ChevronDown className="size-3" />
                  </Button>
                </DropdownMenuTrigger>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="max-w-[300px]">
                The dialect this file is written in, and the one it will be written back
                in. Rows you do not touch are re-emitted exactly as they were read, so a
                save that changed one cell changes one line.
                {dirty && ' Save or discard before changing the delimiter.'}
              </TooltipContent>
            </Tooltip>
            <DropdownMenuContent align="start">
              <DropdownMenuRadioGroup
                value={sheet.dialect.delimiter}
                onValueChange={(value) => csv.reparse(value)}
              >
                {DELIMITERS.map((delimiter) => (
                  <DropdownMenuRadioItem
                    key={delimiter}
                    value={delimiter}
                    disabled={dirty}
                    className="text-xs"
                  >
                    {delimiterName(delimiter)}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>

          <Separator orientation="vertical" className="mx-1 h-4" />

          <Tooltip>
            <TooltipTrigger asChild>
              <label className="flex cursor-pointer items-center gap-1.5 text-muted-foreground">
                <Switch
                  checked={header}
                  onCheckedChange={csv.setHeader}
                  className="scale-75"
                  aria-label="the first row names the columns"
                />
                header row
              </label>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="max-w-[300px]">
              How this grid reads the file. It changes nothing on disk: the engine
              sniffs the header itself when it builds the table, and `srctable` takes
              its own <span className="font-mono">header:</span> argument.
            </TooltipContent>
          </Tooltip>

          <div className="ml-auto flex items-center gap-1.5">
            {csv.status && csv.status.revisions.length > 0 && (
              <Revisions revisions={csv.status.revisions} />
            )}
            <Button
              size="xs"
              variant="ghost"
              onClick={() => apply((current) => insertRow(current, current.rows.length))}
            >
              <Plus className="size-3" />
              Row
            </Button>
            <Button
              size="xs"
              variant="ghost"
              onClick={() => apply((current) => insertColumn(current, width(current)))}
            >
              <Plus className="size-3" />
              Column
            </Button>
            <Button size="xs" variant="secondary" disabled={!dirty || csv.busy} onClick={() => void save()}>
              {csv.busy ? <Loader2 className="size-3 animate-spin" /> : <Save className="size-3" />}
              Save
            </Button>
          </div>
        </div>
        <Separator />

        {fileFindings.length > 0 && (
          <div className="shrink-0 space-y-1 px-3 py-1.5">
            {fileFindings.map((finding, index) => (
              <p
                key={`${finding.code}-${index}`}
                className="flex gap-1.5 text-[11px] leading-relaxed text-muted-foreground"
              >
                <span
                  className={cn(
                    'font-mono',
                    finding.level === 'error' ? 'text-destructive' : 'text-rail-assessed'
                  )}
                >
                  {finding.code}
                </span>
                <span>{finding.message}</span>
              </p>
            ))}
          </div>
        )}

        {/* ── the grid ────────────────────────────────────────────────────── */}
        <div
          ref={grid}
          tabIndex={0}
          onKeyDown={onGridKey}
          onFocus={(event) => {
            // Tabbing into the grid lands on the first cell. Only when the focus
            // arrived at the container itself: a cell button focusing bubbles a
            // focusin through here too, and moving the cursor back to 0,0 on
            // every arrow key would be a grid nobody can drive.
            if (event.target !== event.currentTarget || cursor) return
            setCursor({ r: bodyStart, c: 0 })
          }}
          className="min-h-0 flex-1 overflow-auto focus:outline-none"
        >
          {columnCount === 0 ? (
            <div className="p-6 text-xs text-muted-foreground">
              This file is empty.{' '}
              <button
                className="underline underline-offset-2"
                onClick={() => apply((current) => insertRow(current, 0))}
              >
                Add a row
              </button>{' '}
              to start it.
            </div>
          ) : (
            <table className="w-max border-separate border-spacing-0 font-mono text-[12px]">
              <thead className="sticky top-0 z-10 bg-background">
                <tr>
                  <th className="sticky left-0 z-20 w-10 border-r border-b border-border bg-background px-1 py-1 text-right text-[10px] font-normal text-muted-foreground">
                    #
                  </th>
                  {columns.map((column) => (
                    <HeaderCell
                      key={column.index}
                      column={column}
                      findings={byColumn.get(column.index) ?? []}
                      editable={header}
                      cell={
                        header ? (
                          <CellBox
                            r={0}
                            c={column.index}
                            value={cellAt(sheet, 0, column.index)}
                            active={cursor?.r === 0 && cursor?.c === column.index}
                            editing={editing}
                            draft={draft}
                            numeric={false}
                            onDraft={setDraft}
                            onOpen={begin}
                            onInputKey={onInputKey}
                            onBlur={() => commit(0, 0)}
                          />
                        ) : null
                      }
                      onDelete={() => apply((current) => removeColumn(current, column.index))}
                      onInsert={() => apply((current) => insertColumn(current, column.index + 1))}
                    />
                  ))}
                </tr>
              </thead>
              <tbody>
                {sheet.rows.slice(bodyStart, bodyStart + shown).map((row, offset) => {
                  const r = bodyStart + offset
                  return (
                    <tr key={r} className="group">
                      <th
                        scope="row"
                        className="sticky left-0 z-[1] w-10 border-r border-b border-border bg-background px-1 py-0 text-right align-middle text-[10px] font-normal text-muted-foreground"
                      >
                        <span className="group-hover:hidden">{offset + 1}</span>
                        <button
                          title={`delete row ${offset + 1}`}
                          onClick={() => apply((current) => removeRow(current, r))}
                          className="hidden w-full justify-end text-muted-foreground hover:text-destructive group-hover:flex"
                        >
                          <Trash2 className="size-3" />
                        </button>
                      </th>
                      {columns.map((column) => (
                        <td
                          key={column.index}
                          className="border-r border-b border-border p-0 align-top"
                        >
                          <CellBox
                            r={r}
                            c={column.index}
                            value={row.cells[column.index] ?? ''}
                            active={cursor?.r === r && cursor?.c === column.index}
                            editing={editing}
                            draft={draft}
                            numeric={column.numeric}
                            onDraft={setDraft}
                            onOpen={begin}
                            onInputKey={onInputKey}
                            onBlur={() => commit(0, 0)}
                          />
                        </td>
                      ))}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* ── footer ──────────────────────────────────────────────────────── */}
        <Separator />
        <div className="flex shrink-0 items-center gap-3 px-3 py-1.5 text-[10.5px] text-muted-foreground">
          <span className="flex items-center gap-1">
            <span className="rounded-xs bg-rail-assessed/15 px-1 font-mono text-rail-assessed">
              {MISSING}
            </span>
            {MISSING_LABEL} — an empty cell, marked the way the built table marks it
          </span>
          <span className="ml-auto">
            {shown < total ? `showing ${shown} of ${total} rows` : `${total} rows`}
          </span>
          {shown < total && (
            <Button size="xs" variant="ghost" onClick={() => setLimit((value) => value + PAGE)}>
              Show {Math.min(PAGE, total - shown)} more
            </Button>
          )}
        </div>

        <ReviseDialog
          open={reviseOpen}
          onOpenChange={setReviseOpen}
          busy={csv.busy}
          failure={csv.failure}
          rel={csv.paths.reportRel}
          onRevise={async (note) => {
            const done = await csv.revise(note)
            if (done) notify.current?.()
            return done
          }}
        />
      </div>
    </TooltipProvider>
  )
}

// ── One cell ─────────────────────────────────────────────────────────────────

/**
 * A cell is a button until it is an input.
 *
 * An empty cell is painted and marked rather than left blank, which is the whole
 * argument of this editor stated in eight lines of JSX: absence has to look like
 * something, or it reads as a value.
 */
function CellBox({
  r,
  c,
  value,
  active,
  editing,
  draft,
  numeric,
  onDraft,
  onOpen,
  onInputKey,
  onBlur
}: {
  r: number
  c: number
  value: string
  active: boolean
  editing: boolean
  draft: string
  numeric: boolean
  onDraft: (text: string) => void
  onOpen: (r: number, c: number) => void
  onInputKey: (event: React.KeyboardEvent<HTMLInputElement>) => void
  onBlur: () => void
}) {
  if (active && editing) {
    return (
      <input
        autoFocus
        value={draft}
        spellCheck={false}
        onChange={(event) => onDraft(event.target.value)}
        onKeyDown={onInputKey}
        onBlur={onBlur}
        className={cn(
          'h-6 w-full min-w-[6rem] bg-background px-1.5 font-mono text-[12px] outline-none',
          'ring-2 ring-ring ring-inset',
          numeric && 'text-right tabular-nums'
        )}
      />
    )
  }

  const empty = value.trim().length === 0
  return (
    <button
      type="button"
      data-cell={`${r}:${c}`}
      tabIndex={active ? 0 : -1}
      onClick={() => onOpen(r, c)}
      title={empty ? `${MISSING_LABEL} — this cell is empty in the file` : value}
      className={cn(
        'h-6 w-full min-w-[6rem] truncate px-1.5 text-left outline-none',
        numeric && 'text-right tabular-nums',
        empty && 'bg-rail-assessed/10 text-center text-rail-assessed',
        active && 'ring-2 ring-ring ring-inset',
        !active && 'hover:bg-accent/60'
      )}
    >
      {empty ? <span aria-label={MISSING_LABEL}>{MISSING}</span> : value}
    </button>
  )
}

// ── One column header ────────────────────────────────────────────────────────

/**
 * The name, the empty count, and whatever `data check` says about this column.
 *
 * The count is the cheap half of the feature: a column that is 40% empty is a
 * fact about the export that nobody reading cell by cell would notice, and it
 * sits above the column rather than in a report nobody opens.
 */
function HeaderCell({
  column,
  findings,
  editable,
  cell,
  onDelete,
  onInsert
}: {
  column: ColumnStat
  findings: DataFinding[]
  editable: boolean
  cell: React.ReactNode
  onDelete: () => void
  onInsert: () => void
}) {
  return (
    <th
      scope="col"
      className="group/col min-w-[6rem] border-r border-b border-border bg-background p-0 align-top text-left font-normal"
    >
      <div className="flex items-center gap-1 pr-1">
        <div className="min-w-0 flex-1">
          {editable ? (
            cell
          ) : (
            <span className="block h-6 truncate px-1.5 leading-6 text-muted-foreground">
              {column.label}
            </span>
          )}
        </div>
        <button
          title={`insert a column after ${column.name}`}
          onClick={onInsert}
          className="hidden text-muted-foreground hover:text-foreground group-hover/col:block"
        >
          <Plus className="size-3" />
        </button>
        <button
          title={`delete ${column.name}`}
          onClick={onDelete}
          className="hidden text-muted-foreground hover:text-destructive group-hover/col:block"
        >
          <Trash2 className="size-3" />
        </button>
      </div>

      <div className="flex items-center gap-1 px-1.5 pb-1 font-sans text-[9.5px] text-muted-foreground">
        {column.empty > 0 ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                tabIndex={0}
                className="rounded-xs bg-rail-assessed/15 px-1 text-rail-assessed"
              >
                {column.empty} empty
              </span>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="max-w-[280px]">
              {column.empty} of {column.rows} rows have no value in {column.name}. The
              built table prints {MISSING_LABEL} there — it never prints a blank, and
              never a zero.
            </TooltipContent>
          </Tooltip>
        ) : (
          <span className="text-muted-foreground/60">{column.rows} filled</span>
        )}

        {findings.map((finding, index) => (
          <Tooltip key={`${finding.code}-${index}`}>
            <TooltipTrigger asChild>
              <Badge
                variant="outline"
                tabIndex={0}
                className={cn(
                  'shrink-0 border-dashed px-1 py-0 font-mono text-[9.5px] font-normal',
                  finding.level === 'error' ? 'text-destructive' : 'text-rail-assessed'
                )}
              >
                {finding.code}
              </Badge>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="max-w-[340px] leading-relaxed">
              {finding.message}
            </TooltipContent>
          </Tooltip>
        ))}
      </div>
    </th>
  )
}

// ── The checksum ─────────────────────────────────────────────────────────────

/**
 * What `sources.yml` records about this file, across the top of the editor.
 *
 * Four states, and the awkward one is deliberately the loudest: a file whose
 * bytes no longer match the recorded sha is already failing E011, and the report
 * that cites it is quoting numbers that have moved. The action is a decision
 * somebody takes, never something a save does on their behalf.
 */
function ChecksumBanner({
  csv,
  reportId,
  onRevise,
  onRegister
}: {
  csv: UseCsv
  reportId: string | null
  onRevise: () => void
  onRegister: () => void
}) {
  if (!reportId) {
    return (
      <Line tone="muted">
        This file is not inside a report, so no bibliography records it. Bring it in with{' '}
        <span className="font-mono">report-maker data add</span> before a table reads it.
      </Line>
    )
  }

  const status = csv.status
  const registered = status ? status.registered : csv.derived !== 'unregistered'
  const drifted = status ? !status.matches : csv.derived === 'stale'

  if (!registered) {
    return (
      <Line tone="warn" icon={<TriangleAlert className="size-3.5" />}>
        <span className="flex-1">
          Not registered as a source — no table can cite these numbers yet.
        </span>
        <Button size="xs" variant="secondary" disabled={csv.busy} onClick={onRegister}>
          Register this data
        </Button>
        {csv.failure && <Failure text={csv.failure} />}
      </Line>
    )
  }

  if (drifted || csv.dirty) {
    return (
      <Line tone="alert" icon={<ShieldAlert className="size-3.5" />}>
        <span className="flex-1">
          {csv.dirty
            ? 'Edited — saving will break the checksum this report records for these numbers.'
            : "Edited — this report's recorded checksum no longer matches (E011)."}
          {status?.key && <span className="ml-1 font-mono opacity-70">@{status.key}</span>}
        </span>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button size="xs" variant="secondary" disabled={csv.busy} onClick={onRevise}>
              Re-register this data
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-[320px]">
            Nothing does this on save. The checksum is what stops a spreadsheet moving
            under a report that has already been signed off, and one a tool refreshes for
            you is not a checksum.
          </TooltipContent>
        </Tooltip>
        {csv.failure && <Failure text={csv.failure} />}
      </Line>
    )
  }

  // Without `data status` the only honest statement is the negative one: the
  // linter found no checksum problem. Saying "in sync, @key, sha256 …" out of a
  // silence would be inventing the half of the answer that matters.
  if (!status) {
    return (
      <Line tone="muted" icon={<ShieldCheck className="size-3.5" />}>
        <span className="flex-1">
          <span className="font-mono">data check</span> reports no checksum problem for
          this file.
        </span>
        {csv.statusNote && <span className="truncate opacity-70">{csv.statusNote}</span>}
      </Line>
    )
  }

  return (
    <Line tone="muted" icon={<ShieldCheck className="size-3.5" />}>
      <span className="flex-1">
        In sync{status.key && <span className="ml-1 font-mono">@{status.key}</span>}
        {status.recorded_sha && (
          <span className="ml-1 opacity-70">
            · sha256 {status.recorded_sha.slice(0, 12)}
          </span>
        )}
        {typeof status.rows === 'number' && (
          <span className="ml-1 opacity-70">
            · {status.rows} rows × {status.columns} columns
          </span>
        )}
      </span>
    </Line>
  )
}

function Line({
  tone,
  icon,
  children
}: {
  tone: 'muted' | 'warn' | 'alert'
  icon?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div
      className={cn(
        'flex shrink-0 items-center gap-2 border-b px-3 py-1.5 text-[11px]',
        tone === 'muted' && 'border-border text-muted-foreground',
        tone === 'warn' && 'border-rail-assessed/40 bg-rail-assessed/10 text-foreground',
        tone === 'alert' && 'border-destructive/40 bg-destructive/10 text-foreground'
      )}
    >
      {icon}
      {children}
    </div>
  )
}

function Failure({ text }: { text: string }) {
  return (
    <span className="max-w-[40%] truncate font-mono text-[10.5px] text-destructive" title={text}>
      {text}
    </span>
  )
}

// ── Earlier versions ─────────────────────────────────────────────────────────

/** Every dated copy the engine has kept of this file. The reassurance that the
 *  old numbers still exist is most of what makes re-registering safe to do. */
function Revisions({ revisions }: { revisions: RevisionRow[] }) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button size="xs" variant="ghost">
          <History className="size-3" />
          {revisions.length} earlier {revisions.length === 1 ? 'version' : 'versions'}
        </Button>
      </PopoverTrigger>
      <PopoverContent side="bottom" align="end" className="w-96 p-3">
        <p className="mb-2 text-[11px] leading-relaxed text-muted-foreground">
          Every version this report has cited, kept beside the file under its own date.
          Nothing here is ever overwritten.
        </p>
        <div className="space-y-1">
          {revisions.map((revision) => (
            <div key={revision.path} className="flex items-baseline gap-2 text-[11px]">
              <Archive className="size-3 shrink-0 text-muted-foreground" />
              <span className="font-mono">{revision.date}</span>
              <span className="text-muted-foreground">
                {revision.rows}×{revision.columns}
              </span>
              <span className="ml-auto truncate font-mono text-[10px] text-muted-foreground">
                {revision.sha256.slice(0, 12)}
              </span>
            </div>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  )
}

// ── Re-registering ───────────────────────────────────────────────────────────

/**
 * `data revise`, with the receipt.
 *
 * The dialog asks for one thing the engine cannot know — why the numbers moved —
 * and then shows what actually changed, because a command that silently moves a
 * checksum teaches people that the checksum did not mean anything.
 */
function ReviseDialog({
  open,
  onOpenChange,
  busy,
  failure,
  rel,
  onRevise
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  busy: boolean
  failure: string | null
  rel: string
  onRevise: (note: string) => Promise<ReviseSummary | null>
}) {
  const [note, setNote] = useState('')
  const [done, setDone] = useState<ReviseSummary | null>(null)

  useEffect(() => {
    if (!open) return
    setNote('')
    setDone(null)
  }, [open])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Re-register {rel}</DialogTitle>
          <DialogDescription>
            The engine keeps the version this report cites today as a dated copy beside
            the file, then moves the checksum in{' '}
            <span className="font-mono">sources.yml</span> onto the file as it is now.
            Nothing else in the system moves a recorded checksum.
          </DialogDescription>
        </DialogHeader>

        {done ? (
          <div className="space-y-3 text-sm">
            <p className="text-base">
              <span className="font-mono">@{done.key}</span> — {done.headline}
            </p>
            <p className="font-mono text-[11px] text-muted-foreground">
              sha256 {(done.old_sha ?? 'unregistered').slice(0, 12)} →{' '}
              {done.new_sha.slice(0, 12)}
            </p>
            {done.archived && (
              <p className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
                <Archive className="size-3.5" />
                the previous copy is kept as{' '}
                <span className="font-mono">{done.archived}</span>
              </p>
            )}
            <p className="text-[12px] leading-relaxed">
              Re-read every table and sentence citing{' '}
              <span className="font-mono">@{done.key}</span> — the numbers under them
              have moved.
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            <label className="text-xs text-muted-foreground" htmlFor="revise-note">
              Why did the numbers move? It goes into the entry's note, beside the
              checksum, so the next reader sees the reason and not only the delta.
            </label>
            <Textarea
              id="revise-note"
              rows={3}
              value={note}
              disabled={busy}
              placeholder="Re-exported after the Q3 close; three refunds landed late."
              onChange={(event) => setNote(event.target.value)}
            />
            {failure && (
              <pre className="max-h-40 overflow-auto rounded-md border border-destructive/50 p-2 font-mono text-[11px] whitespace-pre-wrap">
                {failure}
              </pre>
            )}
          </div>
        )}

        <DialogFooter>
          {done ? (
            <Button size="sm" onClick={() => onOpenChange(false)}>
              <Check className="size-3.5" />
              Done
            </Button>
          ) : (
            <>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                disabled={busy}
                onClick={async () => {
                  const result = await onRevise(note)
                  if (result) setDone(result)
                }}
              >
                {busy && <Loader2 className="size-3.5 animate-spin" />}
                Archive and re-register
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
