/**
 * The IPC vocabulary, shared by all three processes.
 *
 * It lives outside main/, preload/ and renderer/ because all three compile as
 * separate TypeScript projects: a type reached across that boundary has to sit
 * somewhere every project includes.
 */

export type VaultList = { vaults: string[]; current: string | null }

export type Run = { code: number; stdout: string; stderr: string; command: string }

export type Node = {
  name: string
  path: string
  rel: string
  kind: 'dir' | 'file'
  children?: Node[]
}

export type OpenResult = { ok: true; list: VaultList } | { ok: false; reason: string }

/** One row of `report-maker list --json`. */
export type ReportRow = {
  id: string
  group: string
  template: string
  built: boolean
  stale: boolean
  title?: string
  kind?: string
  date?: string
}
