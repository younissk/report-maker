import { useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, FileText, Folder, FolderOpen } from 'lucide-react'
import type { Node } from '../../../shared/types'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

type Props = {
  nodes: Node[]
  openPath: string | null
  dirty: boolean
  onOpen: (node: Node) => void
}

const EDITABLE = /\.(typ|yml|yaml|json|toml|mmd|md|txt|csv)$/i

/** The three folders that are the vault's data model. They sort first and start
 *  open; anything else in the directory is somebody else's business. */
const VAULT_DIRS = ['reports', 'templates', 'brand']

function rank(node: Node): number {
  const index = VAULT_DIRS.indexOf(node.name)
  return index === -1 ? 1 : index - VAULT_DIRS.length
}

function ancestors(path: string): string[] {
  const parts = path.split('/')
  return parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join('/'))
}

function Row({
  node,
  depth,
  openPath,
  dirty,
  expanded,
  toggle,
  onOpen
}: {
  node: Node
  depth: number
  openPath: string | null
  dirty: boolean
  expanded: Set<string>
  toggle: (path: string) => void
  onOpen: (node: Node) => void
}) {
  const isOpenFile = node.path === openPath
  const editable = node.kind === 'file' && EDITABLE.test(node.name)

  if (node.kind === 'dir') {
    const open = expanded.has(node.path)
    const Chevron = open ? ChevronDown : ChevronRight
    const FolderIcon = open ? FolderOpen : Folder
    return (
      <div>
        <button
          onClick={() => toggle(node.path)}
          className="flex w-full items-center gap-1 rounded-sm px-2 py-[3px] text-left hover:bg-accent"
          style={{ paddingLeft: depth * 12 + 6 }}
        >
          <Chevron className="size-3 shrink-0 text-muted-foreground" />
          <FolderIcon className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate">{node.name}</span>
        </button>
        {open &&
          node.children?.map((child) => (
            <Row
              key={child.path}
              node={child}
              depth={depth + 1}
              openPath={openPath}
              dirty={dirty}
              expanded={expanded}
              toggle={toggle}
              onOpen={onOpen}
            />
          ))}
      </div>
    )
  }

  return (
    <button
      onClick={() => editable && onOpen(node)}
      disabled={!editable}
      className={cn(
        'flex w-full items-center gap-1.5 rounded-sm px-2 py-[3px] text-left',
        editable ? 'hover:bg-accent' : 'cursor-default opacity-40',
        isOpenFile && 'bg-accent text-accent-foreground'
      )}
      style={{ paddingLeft: depth * 12 + 21 }}
      title={node.rel}
    >
      <FileText className="size-3.5 shrink-0 text-muted-foreground" />
      <span className="truncate">{node.name}</span>
      {isOpenFile && dirty && <span className="ml-auto text-[10px] text-muted-foreground">●</span>}
    </button>
  )
}

export function FileTree({ nodes, openPath, dirty, onOpen }: Props) {
  // Expansion lives here, not per row: the tree has to reveal a file it did not
  // open itself — the app reopens the last report on launch, and a tree that
  // hides the file being edited is a tree you have to walk again by hand.
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  useEffect(() => {
    setExpanded((current) => {
      const next = new Set(current)
      for (const node of nodes) if (VAULT_DIRS.includes(node.name)) next.add(node.path)
      if (openPath) for (const path of ancestors(openPath)) next.add(path)
      return next
    })
  }, [nodes, openPath])

  const toggle = (path: string): void =>
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })

  if (nodes.length === 0) {
    return (
      <div className="p-4 text-xs text-muted-foreground">
        Empty vault. Run <span className="font-mono">report-maker new "Title"</span> to add a report.
      </div>
    )
  }

  return (
    <ScrollArea className="h-full">
      <div className="py-1 pr-1 text-[12.5px]">
        {[...nodes]
          .sort((a, b) => rank(a) - rank(b))
          .map((node) => (
            <Row
              key={node.path}
              node={node}
              depth={0}
              openPath={openPath}
              dirty={dirty}
              expanded={expanded}
              toggle={toggle}
              onOpen={onOpen}
            />
          ))}
      </div>
    </ScrollArea>
  )
}
