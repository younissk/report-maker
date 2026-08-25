import { useCallback, useSyncExternalStore } from 'react'
import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** The shadcn idiom, unchanged from the desktop app so components port across. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * A media query, read synchronously so the first render is already right.
 *
 * There is no server render here, so `matchMedia` on the first paint is safe
 * and avoids the flash where a phone briefly lays itself out as a desktop.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const list = window.matchMedia(query)
      list.addEventListener('change', onChange)
      return () => list.removeEventListener('change', onChange)
    },
    [query]
  )
  const get = useCallback(() => window.matchMedia(query).matches, [query])
  return useSyncExternalStore(subscribe, get, () => false)
}

/**
 * The one breakpoint this product has: 1024px.
 *
 * Below it, one pane at a time and a bottom tab bar. At and above it, the three
 * panes side by side. Everything else is spacing.
 */
export const DESKTOP = '(min-width: 1024px)'

export function useIsDesktop(): boolean {
  return useMediaQuery(DESKTOP)
}

/**
 * Whether the soft keyboard is up.
 *
 * `visualViewport` shrinks when the keyboard opens on both iOS and Android; the
 * layout viewport does not. The bottom tab bar reads this and hides, because a
 * fixed bar over a keyboard is a bar that covers the word you are typing — and
 * on iOS it lands somewhere between the two and looks broken in the process.
 *
 * The 120px floor is there so an address bar retracting on scroll does not read
 * as a keyboard.
 */
export function useKeyboardOpen(): boolean {
  const subscribe = useCallback((onChange: () => void) => {
    const vv = window.visualViewport
    if (!vv) return () => {}
    vv.addEventListener('resize', onChange)
    vv.addEventListener('scroll', onChange)
    return () => {
      vv.removeEventListener('resize', onChange)
      vv.removeEventListener('scroll', onChange)
    }
  }, [])

  const get = useCallback(() => {
    const vv = window.visualViewport
    if (!vv) return false
    return window.innerHeight - vv.height > 120
  }, [])

  return useSyncExternalStore(subscribe, get, () => false)
}

/** Bytes, for a quota meter. The engine prints its own; this is chrome. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '—'
  if (bytes < 1024) return `${bytes} B`
  const units = ['kB', 'MB', 'GB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`
}
