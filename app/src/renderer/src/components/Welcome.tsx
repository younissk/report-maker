import { FolderOpen, FolderPlus } from 'lucide-react'
import { Button } from '@/components/ui/button'

type Props = {
  onOpen: () => void
  onCreate: () => void
  engine: string
  error?: string
}

/**
 * What a fresh install shows.
 *
 * The app ships with no vault, the way an editor ships with no document: a vault
 * is a folder somewhere on the disk — in Documents, in a git repo, in a synced
 * drive — and opening one is the first thing anybody does here.
 */
export function Welcome({ onOpen, onCreate, engine, error }: Props) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-6 px-8">
      <div className="text-center">
        <h1 className="text-xl font-medium">report-maker</h1>
        <p className="mt-1.5 max-w-md text-xs text-muted-foreground">
          A vault is a folder holding <span className="font-mono">report-maker.toml</span>, with your
          reports, designs and brand packs inside it. Open one, or make a new one anywhere on your
          disk.
        </p>
      </div>

      <div className="flex gap-2">
        <Button size="sm" className="gap-1.5" onClick={onOpen}>
          <FolderOpen className="size-3.5" />
          Open a vault…
        </Button>
        <Button size="sm" variant="secondary" className="gap-1.5" onClick={onCreate}>
          <FolderPlus className="size-3.5" />
          Create a vault…
        </Button>
      </div>

      {error && (
        <p className="max-w-lg text-center font-mono text-[11px] whitespace-pre-wrap text-destructive">
          {error}
        </p>
      )}

      <p className="text-[11px] text-muted-foreground">
        engine: <span className="font-mono">{engine}</span>
      </p>
    </div>
  )
}
