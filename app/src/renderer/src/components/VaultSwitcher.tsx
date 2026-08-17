import { FolderPlus } from 'lucide-react'
import type { VaultList } from '../../../shared/types'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/select'

type Props = {
  list: VaultList
  onSelect: (path: string) => void
  onOpen: () => void
}

const ADD = '__add__'

function label(path: string): string {
  const parts = path.replace(/\/$/, '').split('/')
  return parts[parts.length - 1] || path
}

/**
 * A vault is a folder, so switching vaults is picking a folder. The list is the
 * folders opened before — the app remembers paths and nothing else.
 */
export function VaultSwitcher({ list, onSelect, onOpen }: Props) {
  return (
    <div className="flex items-center gap-1.5">
      <Select
        value={list.current ?? undefined}
        onValueChange={(value) => (value === ADD ? onOpen() : onSelect(value))}
      >
        <SelectTrigger className="h-7 w-[190px] border-border bg-secondary text-xs">
          <SelectValue placeholder="No vault open" />
        </SelectTrigger>
        <SelectContent>
          {list.vaults.map((path) => (
            <SelectItem key={path} value={path} className="text-xs">
              <span className="font-medium">{label(path)}</span>
              <span className="ml-2 text-muted-foreground">{path}</span>
            </SelectItem>
          ))}
          <SelectItem value={ADD} className="text-xs">
            Open a vault…
          </SelectItem>
        </SelectContent>
      </Select>
      <Button variant="ghost" size="icon" className="size-7" title="Open a vault" onClick={onOpen}>
        <FolderPlus className="size-3.5" />
      </Button>
    </div>
  )
}
