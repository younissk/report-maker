/**
 * The system's font families, for the brand studio's font pickers.
 *
 * A brand pack names fonts by family — `["Didot", "Bodoni 72", "Times New
 * Roman"]` — and Typst resolves them against what is installed. So the picker
 * has to offer what this machine actually has, or it offers a document that
 * silently renders in a fallback face.
 *
 * There is no cheap, portable API for this. `fc-list` is fast and exact where it
 * exists; macOS's own `system_profiler SPFontsDataType` takes several seconds,
 * which is too slow to sit behind a picker, so a curated list of the families
 * that ship with macOS and Windows is the floor. The result is cached for the
 * process: fonts do not change while an app is open, and nothing here may ever
 * run at startup — it is called when a picker opens, and not before.
 */

import { execFile } from 'node:child_process'
import { app } from 'electron'

let cached: Promise<string[]> | null = null

/**
 * Families that ship with macOS or Windows. Not a guess at what is installed —
 * a floor, so the picker is never empty on a machine without fontconfig.
 */
const FALLBACK = [
  'Andale Mono',
  'Arial',
  'Avenir',
  'Avenir Next',
  'Baskerville',
  'Bodoni 72',
  'Calibri',
  'Cambria',
  'Candara',
  'Charter',
  'Consolas',
  'Courier New',
  'Didot',
  'Futura',
  'Garamond',
  'Georgia',
  'Gill Sans',
  'Helvetica',
  'Helvetica Neue',
  'Hoefler Text',
  'Iowan Old Style',
  'Lucida Grande',
  'Menlo',
  'Monaco',
  'Optima',
  'Palatino',
  'Papyrus',
  'SF Mono',
  'Segoe UI',
  'Tahoma',
  'Times New Roman',
  'Trebuchet MS',
  'Verdana'
]

function exec(cmd: string, args: string[], timeout: number): Promise<string | null> {
  return new Promise((done) => {
    execFile(cmd, args, { timeout, maxBuffer: 8 * 1024 * 1024 }, (err, stdout) => {
      done(err ? null : stdout)
    })
  })
}

/** `fc-list : family` prints one line per face, families comma-separated. */
function familiesFromFcList(stdout: string): string[] {
  const found = new Set<string>()
  for (const line of stdout.split('\n')) {
    for (const name of line.split(',')) {
      const family = name.trim()
      // Skip the localised aliases and the internal PostScript-ish names.
      if (family && !family.startsWith('.') && family.length < 64) found.add(family)
    }
  }
  return [...found]
}

function tidy(names: string[]): string[] {
  const unique = [...new Set(names.filter(Boolean))]
  return unique.sort((a, b) => a.localeCompare(b))
}

async function enumerate(): Promise<string[]> {
  // Electron has floated a getSystemFonts() more than once; use it if this build
  // has it rather than shelling out, but never depend on it.
  const maybe = (app as unknown as { getSystemFonts?: () => string[] }).getSystemFonts
  if (typeof maybe === 'function') {
    try {
      const names = maybe.call(app)
      if (Array.isArray(names) && names.length) return tidy(names)
    } catch {
      // Fall through to fontconfig.
    }
  }

  const listed = await exec('fc-list', [':', 'family'], 4000)
  if (listed) {
    const families = familiesFromFcList(listed)
    if (families.length) return tidy([...families, ...FALLBACK])
  }

  return tidy(FALLBACK)
}

/** The families this machine can render, sorted. Computed once, on demand. */
export function list(): Promise<string[]> {
  cached ??= enumerate().catch(() => tidy(FALLBACK))
  return cached
}
