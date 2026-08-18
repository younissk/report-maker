/**
 * The contract for `window.api`.
 *
 * Declared here rather than inferred from the preload implementation, because the
 * renderer compiles as its own project and cannot reach into preload's. The
 * preload script implements this interface, so the two cannot drift silently.
 */

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
} from './types'

export interface Api {
  vaults: {
    /**
     * The remembered vaults, ordered pinned-first then most-recent, each flagged
     * `missing` when the folder is gone. At startup `current` is decided by
     * `startup.reopenLast` — unless a vault was named on the command line, which
     * wins over everything.
     */
    list(): Promise<VaultList>
    open(): Promise<OpenResult>
    create(): Promise<OpenResult>
    select(path: string): Promise<VaultList>
    /** Drop it from the list. Nothing on disk is touched, and forgetting the
     *  open vault leaves no vault open rather than jumping to another. */
    forget(path: string): Promise<VaultList>
    /** Hold it above the recency order. */
    pin(path: string, pinned: boolean): Promise<VaultList>
  }
  files: {
    tree(vault: string): Promise<Node[]>
    read(vault: string, path: string): Promise<string>
    write(vault: string, path: string, text: string): Promise<void>
    bytes(vault: string, path: string): Promise<Uint8Array>
    exists(vault: string, path: string): Promise<boolean>
    reveal(vault: string, path: string): Promise<void>
    /**
     * Open the file with whatever the system opens it with — an archived
     * snapshot belongs in a browser, not selected in Finder. Resolves to the
     * empty string on success, or to the system's error message.
     */
    open(vault: string, path: string): Promise<string>
  }
  engine: {
    run(vault: string, args: string[]): Promise<Run>
    json<T>(vault: string, args: string[]): Promise<T>
    manifest<T>(vault: string): Promise<T | null>
    /** Where the engine was found, for the status bar and for diagnosing a
     *  machine that has the app but not the CLI. */
    where(): Promise<string>
    /** `report-maker --version`, or null on an engine that predates the flag —
     *  both are ordinary, so the caller says "unavailable" rather than showing
     *  argparse's usage line where a number belongs. */
    version(): Promise<string | null>
    /** `report-maker doctor` verbatim, plus the PATH the app searched. The
     *  answer to "but it works in my terminal". */
    doctor(vault: string | null): Promise<Diagnostics>
  }
  /**
   * The application menu, for the items whose state lives in the renderer. Main
   * dispatches rather than acts: `open-vault` means "call `vaults.open()`", and
   * the dialog stays in one place whichever end asked for it.
   */
  menu: {
    /** Subscribe. Returns the unsubscribe function. */
    on(cb: (command: MenuCommand) => void): () => void
  }
  /** Preferences, global to the app and persisted in userData. */
  settings: {
    get(): Promise<Settings>
    /** Merges over what is stored and returns the result, so callers never have
     *  to send back a whole settings object to change one field. */
    set(patch: DeepPartial<Settings>): Promise<Settings>
    reset(): Promise<Settings>
  }
  /**
   * A live `report-maker watch` run. Exactly one may be running: `start` replaces
   * whatever was running before, because two watchers on one vault would fight
   * over the same output directory.
   */
  watch: {
    start(vault: string, target: string): Promise<void>
    stop(): Promise<void>
    /** Subscribe to the run's output. Returns the unsubscribe function. */
    on(cb: (event: WatchEvent) => void): () => void
  }
  /**
   * Git, as the engine sees it. The app knows nothing about branches or remotes;
   * it asks `report-maker sync`, which is the only place the safety rules live.
   */
  git: {
    state(vault: string): Promise<GitState>
    sync(vault: string, push: boolean): Promise<Run>
  }
  /** System font families, for the brand studio's font pickers. */
  fonts: {
    list(): Promise<string[]>
  }
  platform: string
}
