/**
 * The vault, read as a tree.
 *
 * The engine's data model is the folder structure, so the file tree is not a
 * projection of anything — it is the model, walked directly. Generated
 * directories are skipped: `.build/` is staged output and `out/` is products,
 * neither of which anyone should be editing.
 */

import { readdir, readFile, stat, writeFile } from 'node:fs/promises'
import { join, relative, resolve, sep } from 'node:path'

export type Node = {
  name: string
  path: string
  rel: string
  kind: 'dir' | 'file'
  children?: Node[]
}

const SKIP_DIRS = new Set(['.build', 'out', 'node_modules', '.git', '__pycache__'])
const EDITABLE = /\.(typ|yml|yaml|json|toml|mmd|md|txt|csv)$/i

/** Everything the shell will open. Anything else is shown but not editable. */
export function isEditable(path: string): boolean {
  return EDITABLE.test(path)
}

/**
 * Refuse any path outside the vault. The renderer sends paths back over IPC, so
 * this is the boundary that keeps a bug — or a crafted message — from reading or
 * writing anywhere on the disk.
 */
export function within(vault: string, path: string): string {
  const root = resolve(vault)
  const target = resolve(path)
  if (target !== root && !target.startsWith(root + sep)) {
    throw new Error(`refusing to touch ${target}: outside the vault ${root}`)
  }
  return target
}

async function walk(vault: string, dir: string, depth: number): Promise<Node[]> {
  if (depth > 12) return []
  const entries = await readdir(dir, { withFileTypes: true })
  const nodes: Node[] = []
  for (const entry of entries) {
    if (entry.name.startsWith('.') && entry.name !== '.gitkeep') continue
    if (entry.isDirectory() && SKIP_DIRS.has(entry.name)) continue
    const path = join(dir, entry.name)
    const node: Node = {
      name: entry.name,
      path,
      rel: relative(vault, path),
      kind: entry.isDirectory() ? 'dir' : 'file'
    }
    if (entry.isDirectory()) node.children = await walk(vault, path, depth + 1)
    nodes.push(node)
  }
  // Folders first, then files, each alphabetical — the order a person expects.
  nodes.sort((a, b) => (a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === 'dir' ? -1 : 1))
  return nodes
}

export async function tree(vault: string): Promise<Node[]> {
  const root = resolve(vault)
  return walk(root, root, 0)
}

export async function read(vault: string, path: string): Promise<string> {
  return readFile(within(vault, path), 'utf8')
}

export async function write(vault: string, path: string, text: string): Promise<void> {
  const target = within(vault, path)
  if (!isEditable(target)) throw new Error(`refusing to write a non-text file: ${target}`)
  await writeFile(target, text, 'utf8')
}

export async function bytes(vault: string, path: string): Promise<Uint8Array> {
  return new Uint8Array(await readFile(within(vault, path)))
}

export async function exists(vault: string, path: string): Promise<boolean> {
  try {
    await stat(within(vault, path))
    return true
  } catch {
    return false
  }
}
