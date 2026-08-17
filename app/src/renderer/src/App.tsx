import { useCallback, useEffect, useMemo, useState } from 'react'
import { Hammer, RefreshCw, Save, ShieldCheck } from 'lucide-react'
import type { Node, OpenResult, ReportRow, VaultList } from '../../shared/types'
import { Editor } from './components/Editor'
import { FileTree } from './components/FileTree'
import { VaultSwitcher } from './components/VaultSwitcher'
import { Viewer } from './components/Viewer'
import { Welcome } from './components/Welcome'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { locate, relative } from '@/lib/report'

export function App() {
  const [list, setList] = useState<VaultList>({ vaults: [], current: null })
  const [nodes, setNodes] = useState<Node[]>([])
  const [reports, setReports] = useState<ReportRow[]>([])
  const [openPath, setOpenPath] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [dirty, setDirty] = useState(false)
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [revision, setRevision] = useState(0)
  const [engineAt, setEngineAt] = useState('locating…')

  const vault = list.current

  // ── loading

  const refresh = useCallback(async (target: string | null) => {
    if (!target) {
      setNodes([])
      setReports([])
      return
    }
    setNodes(await window.api.files.tree(target))
    try {
      setReports(await window.api.engine.json<ReportRow[]>(target, ['list', '--json']))
    } catch (err) {
      setReports([])
      setStatus(String(err))
    }
  }, [])

  useEffect(() => {
    window.api.engine.where().then(setEngineAt)
    window.api.vaults.list().then(async (loaded) => {
      setList(loaded)
      await refresh(loaded.current)
    })
  }, [refresh])

  const switchVault = useCallback(
    async (path: string) => {
      const updated = await window.api.vaults.select(path)
      setList(updated)
      setOpenPath(null)
      setText('')
      setDirty(false)
      setStatus('')
      await refresh(updated.current)
    },
    [refresh]
  )

  const adopt = useCallback(
    async (result: OpenResult) => {
      if (!result.ok) {
        if (result.reason !== 'cancelled') setStatus(result.reason)
        return
      }
      setList(result.list)
      setOpenPath(null)
      setText('')
      setDirty(false)
      setStatus('')
      await refresh(result.list.current)
    },
    [refresh]
  )

  const openVault = useCallback(
    async () => adopt(await window.api.vaults.open()),
    [adopt]
  )

  const createVault = useCallback(
    async () => adopt(await window.api.vaults.create()),
    [adopt]
  )

  // Opening a vault to an empty editor wastes the first click: the common case is
  // that you came here to write the report you were last writing.
  useEffect(() => {
    if (!vault || openPath !== null || reports.length === 0) return
    const first = reports[0]
    void window.api.files
      .read(vault, `${vault}/reports/${first.id}/main.typ`)
      .then((contents) => {
        setOpenPath(`${vault}/reports/${first.id}/main.typ`)
        setText(contents)
        setDirty(false)
        setStatus(`reports/${first.id}/main.typ`)
      })
      .catch(() => undefined)
  }, [vault, openPath, reports])

  const openFile = useCallback(
    async (node: Node) => {
      if (!vault) return
      const contents = await window.api.files.read(vault, node.path)
      setOpenPath(node.path)
      setText(contents)
      setDirty(false)
      setStatus(node.rel)
    },
    [vault]
  )

  // ── acting

  const save = useCallback(async () => {
    if (!vault || !openPath || !dirty) return
    await window.api.files.write(vault, openPath, text)
    setDirty(false)
    setStatus(`saved ${relative(vault, openPath)}`)
  }, [vault, openPath, text, dirty])

  const located = useMemo(
    () => (vault && openPath ? locate(vault, openPath, reports.map((r) => r.id)) : null),
    [vault, openPath, reports]
  )

  const engine = useCallback(
    async (args: string[], done: string) => {
      if (!vault) return
      setBusy(true)
      setStatus(`${args.join(' ')}…`)
      const result = await window.api.engine.run(vault, args)
      setBusy(false)
      const output = (result.stdout + result.stderr).trimEnd()
      const tail = output.split('\n').filter(Boolean).slice(-2).join(' · ')
      setStatus(result.code === 0 ? `${done} ${tail}` : `failed (exit ${result.code}) ${tail}`)
      setRevision((n) => n + 1)
      await refresh(vault)
    },
    [vault, refresh]
  )

  const build = useCallback(async () => {
    if (!vault) return
    await save()
    // Whatever report the open file belongs to, or the whole vault when the file
    // is a design or a brand pack and could have changed any of them.
    await engine(['all', ...(located ? [located.id] : []), '--warn-only'], 'built')
  }, [vault, save, engine, located])

  const check = useCallback(
    () => engine(['check', ...(located ? [located.id] : [])], 'checked'),
    [engine, located]
  )

  // Menu-free shortcuts, so they work with focus anywhere in the window.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return
      if (event.key === 's') {
        event.preventDefault()
        void save()
      }
      if (event.key === 'b') {
        event.preventDefault()
        void build()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [save, build])

  // ── layout

  if (!vault) {
    return (
      <Welcome
        onOpen={openVault}
        onCreate={createVault}
        engine={engineAt}
        error={status || undefined}
      />
    )
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-3 pl-20">
        <VaultSwitcher
          list={list}
          onSelect={switchVault}
          onOpen={openVault}
          onCreate={createVault}
        />
        <Separator orientation="vertical" className="mx-1 h-5" />
        <Button variant="secondary" size="sm" className="h-7 gap-1.5 text-xs" disabled={!vault || busy} onClick={build}>
          <Hammer className="size-3.5" />
          Build
          <kbd className="ml-1 text-[10px] text-muted-foreground">⌘B</kbd>
        </Button>
        <Button variant="ghost" size="sm" className="h-7 gap-1.5 text-xs" disabled={!vault || busy} onClick={check}>
          <ShieldCheck className="size-3.5" />
          Check
        </Button>
        <Button variant="ghost" size="sm" className="h-7 gap-1.5 text-xs" disabled={!vault || !dirty} onClick={save}>
          <Save className="size-3.5" />
          Save
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          title="Reload the tree"
          disabled={!vault || busy}
          onClick={() => refresh(vault)}
        >
          <RefreshCw className="size-3.5" />
        </Button>

        <div className="ml-auto flex items-center gap-2">
          {located && (
            <Badge variant="outline" className="font-mono text-[10px] font-normal">
              {located.id}
            </Badge>
          )}
          <span className="max-w-[420px] truncate text-[11px] text-muted-foreground">{status}</span>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-64 shrink-0 flex-col border-r border-border">
          <div className="flex h-8 items-center justify-between px-3 text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
            <span>Vault</span>
            <span>{reports.length} reports</span>
          </div>
          <Separator />
          <div className="min-h-0 flex-1">
            <FileTree nodes={nodes} openPath={openPath} dirty={dirty} onOpen={openFile} />
          </div>
        </aside>

        <main className="flex min-w-0 flex-1 flex-col border-r border-border">
          <div className="flex h-8 shrink-0 items-center gap-2 px-3 text-[11px] text-muted-foreground">
            <span className="truncate font-mono">
              {openPath && vault ? relative(vault, openPath) : 'no file open'}
            </span>
            {dirty && <span className="text-[10px]">● unsaved</span>}
          </div>
          <Separator />
          <div className="min-h-0 flex-1">
            <Editor
              path={openPath}
              text={text}
              onChange={(next) => {
                setText(next)
                setDirty(true)
              }}
              onSave={save}
              onBuild={build}
            />
          </div>
        </main>

        <section className="flex w-[46%] min-w-[360px] flex-col">
          <div className="flex h-8 shrink-0 items-center justify-between px-3 text-[11px] text-muted-foreground">
            <span className="font-mono">{located ? located.pdf : 'no report'}</span>
            {located && (
              <button
                className="text-[11px] hover:text-foreground"
                onClick={() => vault && window.api.files.reveal(vault, `${vault}/${located.pdf}`)}
              >
                reveal
              </button>
            )}
          </div>
          <Separator />
          <div className="min-h-0 flex-1">
            <Viewer vault={vault} pdf={located?.pdf ?? null} revision={revision} building={busy} />
          </div>
        </section>
      </div>
    </div>
  )
}
