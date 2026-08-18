/**
 * The only channel between the renderer and the machine. Context isolation is on
 * and node integration is off, so the renderer can do exactly what this file
 * exposes and nothing else.
 */

import { contextBridge, type IpcRendererEvent, ipcRenderer } from 'electron'
import type { Api } from '../shared/api'
import type {
  DeepPartial,
  Diagnostics,
  GitState,
  MenuCommand,
  Node,
  OpenResult,
  Run,
  Settings,
  VaultList,
  WatchEvent
} from '../shared/types'

// Typed as Api so the renderer's view of window.api and this implementation are
// checked against one declaration rather than against each other.
const api: Api = {
  vaults: {
    list: (): Promise<VaultList> => ipcRenderer.invoke('vault:list'),
    open: (): Promise<OpenResult> => ipcRenderer.invoke('vault:open'),
    create: (): Promise<OpenResult> => ipcRenderer.invoke('vault:create'),
    select: (path: string): Promise<VaultList> => ipcRenderer.invoke('vault:select', path),
    forget: (path: string): Promise<VaultList> => ipcRenderer.invoke('vault:forget', path),
    pin: (path: string, pinned: boolean): Promise<VaultList> =>
      ipcRenderer.invoke('vault:pin', path, pinned)
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
      ipcRenderer.invoke('file:reveal', vault, path),
    open: (vault: string, path: string): Promise<string> =>
      ipcRenderer.invoke('file:open', vault, path)
  },
  engine: {
    run: (vault: string, args: string[]): Promise<Run> =>
      ipcRenderer.invoke('engine:run', vault, args),
    json: <T>(vault: string, args: string[]): Promise<T> =>
      ipcRenderer.invoke('engine:json', vault, args),
    manifest: <T>(vault: string): Promise<T | null> => ipcRenderer.invoke('engine:manifest', vault),
    where: (): Promise<string> => ipcRenderer.invoke('engine:where'),
    version: (): Promise<string | null> => ipcRenderer.invoke('engine:version'),
    doctor: (vault: string | null): Promise<Diagnostics> =>
      ipcRenderer.invoke('engine:doctor', vault)
  },
  menu: {
    // Same shape as `watch.on`, and for the same reason: one named channel,
    // wrapped, with the Electron event object kept out of the callback.
    on: (cb: (command: MenuCommand) => void): (() => void) => {
      const listener = (_e: IpcRendererEvent, command: MenuCommand): void => cb(command)
      ipcRenderer.on('menu:command', listener)
      return () => ipcRenderer.removeListener('menu:command', listener)
    }
  },
  settings: {
    get: (): Promise<Settings> => ipcRenderer.invoke('settings:get'),
    set: (patch: DeepPartial<Settings>): Promise<Settings> =>
      ipcRenderer.invoke('settings:set', patch),
    reset: (): Promise<Settings> => ipcRenderer.invoke('settings:reset')
  },
  watch: {
    start: (vault: string, target: string): Promise<void> =>
      ipcRenderer.invoke('watch:start', vault, target),
    stop: (): Promise<void> => ipcRenderer.invoke('watch:stop'),
    // One named channel, wrapped. The renderer never gets `ipcRenderer.on`: an
    // allowlist of exactly the events it may hear is the difference between a
    // bridge and a hole, and it is why the callback also never sees the Electron
    // event object it could reach the sender through.
    on: (cb: (event: WatchEvent) => void): (() => void) => {
      const listener = (_e: IpcRendererEvent, payload: WatchEvent): void => cb(payload)
      ipcRenderer.on('watch:event', listener)
      return () => ipcRenderer.removeListener('watch:event', listener)
    }
  },
  git: {
    state: (vault: string): Promise<GitState> => ipcRenderer.invoke('git:state', vault),
    sync: (vault: string, push: boolean): Promise<Run> =>
      ipcRenderer.invoke('git:sync', vault, push)
  },
  fonts: {
    list: (): Promise<string[]> => ipcRenderer.invoke('fonts:list')
  },
  platform: process.platform
}

contextBridge.exposeInMainWorld('api', api)
