/**
 * The contract for `window.api`.
 *
 * Declared here rather than inferred from the preload implementation, because the
 * renderer compiles as its own project and cannot reach into preload's. The
 * preload script implements this interface, so the two cannot drift silently.
 */

import type { Node, OpenResult, Run, VaultList } from './types'

export interface Api {
  vaults: {
    list(): Promise<VaultList>
    open(): Promise<OpenResult>
    create(): Promise<OpenResult>
    select(path: string): Promise<VaultList>
    forget(path: string): Promise<VaultList>
  }
  files: {
    tree(vault: string): Promise<Node[]>
    read(vault: string, path: string): Promise<string>
    write(vault: string, path: string, text: string): Promise<void>
    bytes(vault: string, path: string): Promise<Uint8Array>
    exists(vault: string, path: string): Promise<boolean>
    reveal(vault: string, path: string): Promise<void>
  }
  engine: {
    run(vault: string, args: string[]): Promise<Run>
    json<T>(vault: string, args: string[]): Promise<T>
    manifest<T>(vault: string): Promise<T | null>
    /** Where the engine was found, for the status bar and for diagnosing a
     *  machine that has the app but not the CLI. */
    where(): Promise<string>
  }
  platform: string
}
