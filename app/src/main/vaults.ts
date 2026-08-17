/**
 * The vault list, kept in a JSON file in the app's user data.
 *
 * No database here either — the engine's whole premise is that the filesystem is
 * the source of truth, and a desktop shell that introduced its own store would
 * be the first thing to go stale. All this file remembers is which folders the
 * user has opened, and which one was last active.
 */

import { existsSync } from 'node:fs'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { app } from 'electron'

export type VaultList = { vaults: string[]; current: string | null }

const EMPTY: VaultList = { vaults: [], current: null }

function file(): string {
  return join(app.getPath('userData'), 'vaults.json')
}

export async function read(): Promise<VaultList> {
  try {
    const raw = JSON.parse(await readFile(file(), 'utf8')) as VaultList
    // A vault the user deleted or moved should quietly drop off the list.
    const vaults = (raw.vaults ?? []).filter((path) => existsSync(join(path, 'report-maker.toml')))
    const current = raw.current && vaults.includes(raw.current) ? raw.current : (vaults[0] ?? null)
    return { vaults, current }
  } catch {
    return EMPTY
  }
}

async function write(list: VaultList): Promise<VaultList> {
  await mkdir(dirname(file()), { recursive: true })
  await writeFile(file(), JSON.stringify(list, null, 2) + '\n', 'utf8')
  return list
}

export async function add(path: string): Promise<VaultList> {
  const target = resolve(path)
  const list = await read()
  const vaults = [target, ...list.vaults.filter((v) => v !== target)]
  return write({ vaults, current: target })
}

export async function select(path: string): Promise<VaultList> {
  const list = await read()
  if (!list.vaults.includes(path)) return add(path)
  return write({ ...list, current: path })
}

export async function forget(path: string): Promise<VaultList> {
  const list = await read()
  const vaults = list.vaults.filter((v) => v !== path)
  return write({ vaults, current: list.current === path ? (vaults[0] ?? null) : list.current })
}
