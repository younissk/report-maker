/**
 * The only channel between the renderer and the machine. Context isolation is on
 * and node integration is off, so the renderer can do exactly what this file
 * exposes and nothing else.
 */

import { contextBridge, ipcRenderer } from 'electron'
import type { Api } from '../shared/api'
import type { Node, OpenResult, Run, VaultList } from '../shared/types'

// Typed as Api so the renderer's view of window.api and this implementation are
// checked against one declaration rather than against each other.
const api: Api = {
  vaults: {
    list: (): Promise<VaultList> => ipcRenderer.invoke('vault:list'),
    open: (): Promise<OpenResult> => ipcRenderer.invoke('vault:open'),
    create: (): Promise<OpenResult> => ipcRenderer.invoke('vault:create'),
    select: (path: string): Promise<VaultList> => ipcRenderer.invoke('vault:select', path),
    forget: (path: string): Promise<VaultList> => ipcRenderer.invoke('vault:forget', path)
  },
  files: {
    tree: (vault: string): Promise<Node[]> => ipcRenderer.invoke('tree:read', vault),
    read: (vault: string, path: string): Promise<string> =>
      ipcRenderer.invoke('file:read', vault, path),
    write: (vault: string, path: string, text: string): Promise<void> =>
      ipcRenderer.invoke('file:write', vault, path, text),
    bytes: (vault: string, path: string): Promise<Uint8Array> =>
      ipcRenderer.invoke('file:bytes', vault, path),
    exists: (vault: string, path: string): Promise<boolean> =>
      ipcRenderer.invoke('file:exists', vault, path),
    reveal: (vault: string, path: string): Promise<void> =>
      ipcRenderer.invoke('file:reveal', vault, path)
  },
  engine: {
    run: (vault: string, args: string[]): Promise<Run> =>
      ipcRenderer.invoke('engine:run', vault, args),
    json: <T>(vault: string, args: string[]): Promise<T> =>
      ipcRenderer.invoke('engine:json', vault, args),
    manifest: <T>(vault: string): Promise<T | null> => ipcRenderer.invoke('engine:manifest', vault),
    where: (): Promise<string> => ipcRenderer.invoke('engine:where')
  },
  platform: process.platform
}

contextBridge.exposeInMainWorld('api', api)
