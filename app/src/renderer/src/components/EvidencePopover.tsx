import { Archive, ArchiveX, ExternalLink } from 'lucide-react'
import { createRoot } from 'react-dom/client'
import { EditorView, hoverTooltip, type TooltipView } from '@codemirror/view'
import type { Extension } from '@codemirror/state'
import type { SourceRow } from '../../../shared/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { shortDate } from '@/lib/sources'
import { cn } from '@/lib/utils'

type Props = {
  source: SourceRow
  /** Opens the archived HTML. Omitted where there is nothing to open it with. */
  onOpenSnapshot?: (key: string) => void
  className?: string
}

/**
 * One source, told in full: what it is, when it was read, and whether we still
 * hold a copy of it.
 *
 * The URL renders as text, not as a link. This is an offline archive — the whole
 * point of a snapshot is that the report does not depend on what the live page
 * says today — so the affordance that matters is "open the copy we kept", and a
 * link that quietly fetches the current page would undermine the evidence it
 * sits next to.
 *
 * It takes no context beyond its props: the same component renders inside a
 * Popover in the sources panel and inside a bare React root in a CodeMirror
 * hover tooltip, where none of the app's providers exist.
 */
export function EvidencePopover({ source, onOpenSnapshot, className }: Props) {
  const archived = source.snapshot

  return (
    <div className={cn('space-y-2 text-xs', className)}>
      <div className="flex items-center gap-1.5">
        <span className="truncate font-mono text-[11px] font-medium">@{source.key}</span>
        <Badge variant="outline" className="shrink-0 px-1.5 py-0 text-[10px] font-normal">
          {source.type}
        </Badge>
      </div>

      <div>
        <p className="text-[12.5px] leading-snug font-medium">{source.title || 'untitled'}</p>
        {source.author && <p className="mt-0.5 text-muted-foreground">{source.author}</p>}
      </div>

      {source.url && (
        <p
          className="font-mono text-[10.5px] leading-snug break-all text-muted-foreground select-text"
          title="The address this was read from. Read the archived copy, not the live page."
        >
          {source.url}
        </p>
      )}

      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px]">
        <dt className="text-muted-foreground">accessed</dt>
        <dd className="font-mono">{source.accessed ?? '—'}</dd>

        <dt className="text-muted-foreground">archive</dt>
        <dd className="flex items-center gap-1.5">
          {archived ? (
            <>
              <Archive className="size-3 shrink-0 text-muted-foreground" />
              <span className="font-mono">{shortDate(archived.fetched)}</span>
              <span className="truncate font-mono text-muted-foreground">
                {archived.sha256.slice(0, 12)}
              </span>
            </>
          ) : (
            <>
              <ArchiveX className="size-3 shrink-0 text-muted-foreground" />
              <span className="text-muted-foreground">not archived</span>
            </>
          )}
        </dd>

        <dt className="text-muted-foreground">cited</dt>
        <dd className={cn(source.uses === 0 && 'text-muted-foreground')}>
          {source.uses === 0 ? 'nothing cites this yet' : `${source.uses}×`}
        </dd>
      </dl>

      {archived && onOpenSnapshot && (
        <Button
          size="xs"
          variant="secondary"
          className="w-full"
          onClick={() => onOpenSnapshot(source.key)}
        >
          <ExternalLink className="size-3" />
          Open snapshot
        </Button>
      )}
      {!archived && (
        <p className="text-[11px] text-muted-foreground">
          No copy kept. <span className="font-mono">report-maker cite --refresh</span> archives it.
        </p>
      )}
    </div>
  )
}

// ── The same card, hovering over a @key in the editor ────────────────────────

/** A citation as it is written in Typst — the same span `lib/typst.ts` colours. */
const CITATION = /@([A-Za-z][\w.:+-]*)/g

/** CodeMirror draws its own chrome around a tooltip; this card brings its own,
 *  and two borders around one popover reads as a bug. */
const tooltipChrome = EditorView.theme({
  '.cm-tooltip.cm-tooltip-hover': {
    border: 'none',
    backgroundColor: 'transparent'
  }
})

function mount(source: SourceRow, onOpenSnapshot?: (key: string) => void): TooltipView {
  const dom = document.createElement('div')
  const root = createRoot(dom)
  root.render(
    <EvidencePopover
      source={source}
      onOpenSnapshot={onOpenSnapshot}
      className="w-80 rounded-md border border-border bg-popover p-3 font-sans text-popover-foreground shadow-md"
    />
  )
  return {
    dom,
    destroy() {
      // React refuses to unmount a root while it is rendering, and CodeMirror
      // destroys a tooltip from inside its own update cycle. A tick's delay is
      // the documented way out of that.
      setTimeout(() => root.unmount())
    }
  }
}

/**
 * Hovering a `@key` shows the source behind it.
 *
 * Only keys that resolve get a tooltip. A key that names nothing is already the
 * linter's business (E-level, gutter marker, problems panel); answering a hover
 * with "no such source" would say the same thing again, more quietly and in the
 * wrong place.
 */
export function citationHover(
  getSources: () => SourceRow[],
  onOpenSnapshot?: (key: string) => void
): Extension {
  return [
    hoverTooltip((view, pos, side) => {
      const line = view.state.doc.lineAt(pos)
      for (const match of line.text.matchAll(CITATION)) {
        const from = line.from + (match.index ?? 0)
        const to = from + match[0].length
        if (pos < from || pos > to) continue
        // At either edge, honour which side of the boundary the pointer is on,
        // so a citation touching another word does not claim its hover.
        if (pos === from && side < 0) continue
        if (pos === to && side > 0) continue

        const source = getSources().find((row) => row.key === match[1])
        if (!source) return null
        return { pos: from, end: to, above: true, create: () => mount(source, onOpenSnapshot) }
      }
      return null
    }),
    tooltipChrome
  ]
}
