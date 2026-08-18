import { useEffect, useState } from 'react'
import { FileWarning, Loader2 } from 'lucide-react'

type Props = {
  vault: string | null
  /** Project-relative path of the PDF the open file belongs to, if any. */
  pdf: string | null
  /** Bumped after a build, to force a reload of the same path. */
  revision: number
  building: boolean
  /**
   * Which page to open at — the shell's cursor→page estimate, or null when it
   * has none. It is an estimate and it is treated as one: it moves the viewer,
   * which the reader can scroll away from, and nothing else depends on it.
   */
  page?: number | null
}

/**
 * The built report.
 *
 * The PDF is read over IPC and handed to Chromium's own viewer as a blob, rather
 * than pointed at with a file:// URL — the renderer is served over http in dev,
 * so a file:// frame would be blocked, and a blob works identically in both.
 */
export function Viewer({ vault, pdf, revision, building, page = null }: Props) {
  const [url, setUrl] = useState<string | null>(null)
  const [missing, setMissing] = useState(false)

  useEffect(() => {
    let stale = false
    let created: string | null = null

    async function load() {
      if (!vault || !pdf) {
        setUrl(null)
        setMissing(false)
        return
      }
      if (!(await window.api.files.exists(vault, `${vault}/${pdf}`))) {
        if (!stale) {
          setUrl(null)
          setMissing(true)
        }
        return
      }
      const bytes = await window.api.files.bytes(vault, `${vault}/${pdf}`)
      if (stale) return
      // .slice() copies into a plain ArrayBuffer, which is what Blob accepts.
      const buffer = bytes.slice().buffer as ArrayBuffer
      created = URL.createObjectURL(new Blob([buffer], { type: 'application/pdf' }))
      setMissing(false)
      setUrl(created)
    }

    load()
    return () => {
      stale = true
      if (created) URL.revokeObjectURL(created)
    }
  }, [vault, pdf, revision])

  if (building) {
    return (
      <Empty>
        <Loader2 className="size-4 animate-spin" />
        <span>Building…</span>
      </Empty>
    )
  }
  if (!pdf) {
    return <Empty>Open a file inside a report to see its PDF.</Empty>
  }
  if (missing || !url) {
    return (
      <Empty>
        <FileWarning className="size-4" />
        <span>
          Not built yet — press <kbd className="font-mono">⌘B</kbd>.
        </span>
      </Empty>
    )
  }

  // Keyed on the page as well as the blob, because Chromium's viewer reads the
  // fragment once, when the frame loads: changing `src#page=` on a live iframe
  // is a same-document navigation the plugin never hears about. Remounting is
  // what actually scrolls it, and it is cheap — the blob is already in memory.
  return (
    <iframe
      key={`${url}:${page ?? 1}`}
      src={`${url}#page=${page ?? 1}&view=FitH&toolbar=1`}
      title="Built report"
      className="h-full w-full border-0 bg-neutral-900"
    />
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center text-xs text-muted-foreground">
      {children}
    </div>
  )
}
