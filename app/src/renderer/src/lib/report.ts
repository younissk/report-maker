/**
 * Which report a file belongs to.
 *
 * The engine already knows every report id — `list --json` returns them — so this
 * is a match against that list rather than a guess from the path shape. It has to
 * be: `reports/acme/2026-08-12-audit/diagrams/flow.mmd` belongs to
 * `acme/2026-08-12-audit`, and only the id list says where the report folder
 * stops and its contents begin. Longest match wins, so a nested id is preferred
 * over its parent folder.
 */

export type Located = { id: string; pdf: string } | null

export function relative(vault: string, path: string): string {
  return path.startsWith(vault) ? path.slice(vault.length).replace(/^\//, '') : path
}

export function locate(vault: string, path: string, ids: string[]): Located {
  const rel = relative(vault, path)
  if (!rel.startsWith('reports/')) return null
  const inside = rel.slice('reports/'.length)

  const match = ids
    .filter((id) => inside === id || inside.startsWith(id + '/'))
    .sort((a, b) => b.length - a.length)[0]

  return match ? { id: match, pdf: `out/${match}.pdf` } : null
}
