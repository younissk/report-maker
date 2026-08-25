/**
 * Reading a built report, mechanically.
 *
 * Everything in this file is about *geometry and gestures* — where a page image
 * sits, how big it is, which one is under the reader's eye, and what two fingers
 * on a screen mean. None of it is about a vault. The only question it asks the
 * server is "which pages exist", and `GET /api/reports/:id/pages` answers that;
 * the answer is carried around unmodified.
 *
 * The one decision worth defending: the reader shows PNGs, never a PDF in an
 * iframe. iOS Safari renders an embedded PDF as a single unscrollable first page
 * with no way in, and the engine already writes `out/pages/<id>/*.png` for
 * exactly this class of consumer. A desktop browser handles a PDF fine and is
 * offered one as an alternative — but the pages are the default everywhere, so
 * what a phone shows and what a laptop shows is the same document.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'

import { ApiError, api, errorText, isAbort, urls, type PagesIndex } from '@/lib/api'
import { guard } from '@/lib/session'

// ── the page list ────────────────────────────────────────────────────────────

export type PagesState =
  /** No report selected. */
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; index: PagesIndex }
  /** The report has never been built, so there is nothing to read yet. */
  | { status: 'unbuilt' }
  | { status: 'failed'; message: string; detail: string | null }

/**
 * "Never built" is the server's answer, not an inference.
 *
 * A report with no `out/pages/<id>/` has no page index to serve, and the route
 * says so with a 404. Anything else — a 500 from a broken engine, a 429 from a
 * quota — is a real failure and must be shown as one, because "build it" is not
 * the fix for either.
 */
function isUnbuilt(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false
  if (error.status === 404) return true
  return /not_?built|unbuilt|no_?pages/i.test(error.code)
}

/**
 * The page index for one report, re-read whenever the vault changes underneath.
 *
 * `revision` comes from `useApp()`; a build bumps it, and this is what makes the
 * reader show the new pages without anybody wiring a callback.
 */
export function usePagesIndex(
  reportId: string | null,
  revision: number
): { state: PagesState; reload: () => void } {
  const [state, setState] = useState<PagesState>({ status: 'idle' })
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    if (!reportId) {
      setState({ status: 'idle' })
      return
    }
    const controller = new AbortController()
    setState({ status: 'loading' })

    guard((signal) => api.pages(reportId, signal), controller.signal)
      .then((index) => {
        if (controller.signal.aborted) return
        setState({ status: 'ready', index })
      })
      .catch((error) => {
        if (isAbort(error) || controller.signal.aborted) return
        if (isUnbuilt(error)) {
          setState({ status: 'unbuilt' })
          return
        }
        setState({
          status: 'failed',
          message: errorText(error),
          detail: error instanceof ApiError ? error.detail : null,
        })
      })

    return () => controller.abort()
  }, [reportId, revision, nonce])

  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { state, reload }
}

/**
 * Where one page image lives.
 *
 * The server may hand back its own URLs in `index.urls`; when it does they win,
 * because it knows things this file does not (a CDN, a share prefix). `version`
 * is a cache-buster: the URL of page 3 does not change when a report is rebuilt,
 * so without it the browser keeps showing the pages from the previous build.
 */
export function pageUrl(
  index: PagesIndex,
  reportId: string,
  n: number,
  version = 0
): string {
  const base = index.urls?.[n - 1] ?? urls.page(reportId, n)
  if (!version) return base
  return `${base}${base.includes('?') ? '&' : '?'}v=${version}`
}

// ── geometry ─────────────────────────────────────────────────────────────────

/** ISO 216 — the aspect a page is assumed to have until one has actually loaded. */
export const DEFAULT_ASPECT = 1 / Math.SQRT2

/** Fit-to-width. There is no reason to shrink a page below the screen it is on. */
export const MIN_SCALE = 1
export const MAX_SCALE = 6
/** What a double tap goes to. Roughly the point where 10pt body text is comfortable. */
export const TAP_SCALE = 2.5
/** A page is never rendered wider than this, so a 27" display does not get one line per inch. */
export const MAX_PAGE_WIDTH = 900

export function clamp(value: number, min = MIN_SCALE, max = MAX_SCALE): number {
  return Math.min(max, Math.max(min, value))
}

/**
 * The scroller's own width, so the page column can be sized in real pixels.
 *
 * Two mechanisms, deliberately. The observer catches a rotation or a pane being
 * dragged wider; the layout effect catches the case the observer is late for —
 * a pane that was `display: none` when this mounted, which on a phone is three
 * panes out of four. A `ResizeObserver` does fire when an element gains a box,
 * but only on the next rendering frame, and one frame of empty document every
 * time somebody taps Read is exactly the kind of flicker nobody can name and
 * everybody notices.
 */
export function useMeasuredWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0)

  useLayoutEffect(() => {
    const element = ref.current
    if (element && element.clientWidth !== width) setWidth(element.clientWidth)
  })

  useEffect(() => {
    const element = ref.current
    if (!element) return
    const observer = new ResizeObserver(() => setWidth(element.clientWidth))
    observer.observe(element)
    return () => observer.disconnect()
  }, [ref])

  return width
}

// ── zoom ─────────────────────────────────────────────────────────────────────

/**
 * A point to keep still while the scale changes.
 *
 * `u`/`v` are that point in the content's own normalised coordinates, `vx`/`vy`
 * are where it should sit inside the viewport afterwards. Anchoring this way is
 * why a pinch stays under the fingers and a double tap magnifies the paragraph
 * you tapped rather than the middle of the page.
 */
type Anchor = { u: number; v: number; vx: number; vy: number }

export type Zoom = {
  scale: number
  isZoomed: boolean
  /** True while two fingers are down, so the caller can suspend other work. */
  gesturing: boolean
  zoomIn: () => void
  zoomOut: () => void
  /** Back to one page across the width, keeping what is on screen on screen. */
  fitWidth: () => void
  /** A different document: fit the width and go back to the first page. */
  reset: () => void
}

/**
 * Pinch, double tap, trackpad and buttons — one scale, four ways to change it.
 *
 * The mechanism, because it is not obvious from the code alone:
 *
 *   - Scale is *layout*, not a permanent transform. The page column is sized in
 *     real pixels at `base × scale`, so the browser rasterises the images at the
 *     size they are shown at and panning is native scrolling with real momentum.
 *     A viewer built on a permanent CSS transform gets blurry text and inertia
 *     that feels borrowed from another platform.
 *   - *During* a pinch that would mean a relayout every frame, so the gesture
 *     paints a transform on the content instead — cheap, GPU, exact — and
 *     commits it to the layout on release, adjusting the scroll offsets so
 *     nothing appears to move at the moment of the swap.
 *   - `touch-action: pan-x pan-y` on the scroller (set by the caller) hands one
 *     finger to the browser and keeps pinch for us. It also means the browser
 *     may scroll *during* a two-finger gesture, which is why the drift between
 *     the scroll offset at gesture start and now is added back into the
 *     transform every frame.
 */
export function useZoomPan({
  scrollRef,
  contentRef,
}: {
  scrollRef: RefObject<HTMLDivElement | null>
  contentRef: RefObject<HTMLElement | null>
}): Zoom {
  const [scale, setScale] = useState(1)
  const [gesturing, setGesturing] = useState(false)
  const scaleRef = useRef(1)
  const pending = useRef<(Anchor & { scale: number }) | null>(null)

  scaleRef.current = scale

  /** Where a client-space point sits in the content, given the view as it is now. */
  const anchorAt = useCallback(
    (clientX: number, clientY: number): Anchor | null => {
      const scroller = scrollRef.current
      const content = contentRef.current
      if (!scroller || !content) return null
      const rect = scroller.getBoundingClientRect()
      const vx = clientX - rect.left
      const vy = clientY - rect.top
      const w = content.offsetWidth || 1
      const h = content.offsetHeight || 1
      return {
        u: (scroller.scrollLeft + vx - content.offsetLeft) / w,
        v: (scroller.scrollTop + vy - content.offsetTop) / h,
        vx,
        vy,
      }
    },
    [scrollRef, contentRef]
  )

  const commit = useCallback((next: number, anchor: Anchor | null) => {
    const target = clamp(next)
    if (Math.abs(target - scaleRef.current) < 0.001) return
    if (anchor) pending.current = { ...anchor, scale: target }
    setScale(target)
  }, [])

  // The scroll correction has to land in the same frame the new layout does, or
  // the reader sees the document jump and then jump back.
  useLayoutEffect(() => {
    const wanted = pending.current
    pending.current = null
    const scroller = scrollRef.current
    const content = contentRef.current
    if (!wanted || !scroller || !content) return
    const w = content.offsetWidth
    const h = content.offsetHeight
    scroller.scrollLeft = Math.max(0, content.offsetLeft + wanted.u * w - wanted.vx)
    scroller.scrollTop = Math.max(0, content.offsetTop + wanted.v * h - wanted.vy)
  }, [scale, scrollRef, contentRef])

  const centreAnchor = useCallback((): Anchor | null => {
    const scroller = scrollRef.current
    if (!scroller) return null
    const rect = scroller.getBoundingClientRect()
    return anchorAt(rect.left + rect.width / 2, rect.top + rect.height / 2)
  }, [scrollRef, anchorAt])

  const zoomIn = useCallback(
    () => commit(scaleRef.current * 1.5, centreAnchor()),
    [commit, centreAnchor]
  )
  const zoomOut = useCallback(
    () => commit(scaleRef.current / 1.5, centreAnchor()),
    [commit, centreAnchor]
  )
  // Anchored, like every other way of changing the scale. Dropping back to fit
  // without one moves the reader several pages up the document, because the
  // scroll offset they had was measured against content that just got smaller.
  const fitWidth = useCallback(
    () => commit(MIN_SCALE, centreAnchor()),
    [commit, centreAnchor]
  )

  const reset = useCallback(() => {
    pending.current = null
    setScale(1)
    const scroller = scrollRef.current
    if (scroller) {
      scroller.scrollLeft = 0
      scroller.scrollTop = 0
    }
  }, [scrollRef])

  // ── the gestures ───────────────────────────────────────────────────────────

  useEffect(() => {
    const scroller = scrollRef.current
    if (!scroller) return

    const points = new Map<number, { x: number; y: number }>()
    let pinch:
      | null
      | {
          dist: number
          mx: number
          my: number
          fx: number
          fy: number
          w: number
          h: number
          left0: number
          top0: number
        } = null
    let live: { k: number; mx: number; my: number } | null = null
    let down: { t: number; x: number; y: number; moved: boolean } | null = null
    let lastTap: { t: number; x: number; y: number } | null = null

    const paint = (transform: string) => {
      const content = contentRef.current
      if (!content) return
      content.style.transformOrigin = '0 0'
      content.style.transform = transform
      content.style.willChange = transform ? 'transform' : ''
    }

    const beginPinch = () => {
      const content = contentRef.current
      if (!content || points.size !== 2) return
      const [a, b] = [...points.values()]
      const rect = content.getBoundingClientRect()
      const mx = (a.x + b.x) / 2
      const my = (a.y + b.y) / 2
      pinch = {
        dist: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)),
        mx,
        my,
        fx: mx - rect.left,
        fy: my - rect.top,
        w: content.offsetWidth || 1,
        h: content.offsetHeight || 1,
        left0: scroller.scrollLeft,
        top0: scroller.scrollTop,
      }
      live = { k: 1, mx, my }
      down = null
      setGesturing(true)
    }

    const endPinch = () => {
      const started = pinch
      const last = live
      pinch = null
      live = null
      setGesturing(false)
      paint('')
      if (!started || !last) return
      const scroller_ = scrollRef.current
      if (!scroller_) return
      const rect = scroller_.getBoundingClientRect()
      commit(scaleRef.current * last.k, {
        u: started.fx / started.w,
        v: started.fy / started.h,
        vx: last.mx - rect.left,
        vy: last.my - rect.top,
      })
    }

    const onDown = (event: PointerEvent) => {
      if (event.pointerType !== 'touch') return
      points.set(event.pointerId, { x: event.clientX, y: event.clientY })
      if (points.size === 1) {
        down = { t: Date.now(), x: event.clientX, y: event.clientY, moved: false }
      } else if (points.size === 2) {
        beginPinch()
      }
    }

    const onMove = (event: PointerEvent) => {
      if (event.pointerType !== 'touch') return
      if (!points.has(event.pointerId)) return
      points.set(event.pointerId, { x: event.clientX, y: event.clientY })

      if (down && !down.moved) {
        if (Math.hypot(event.clientX - down.x, event.clientY - down.y) > 12) down.moved = true
      }

      if (!pinch || points.size < 2) return
      const [a, b] = [...points.values()]
      const dist = Math.max(1, Math.hypot(a.x - b.x, a.y - b.y))
      const mx = (a.x + b.x) / 2
      const my = (a.y + b.y) / 2
      // Never let the gesture run past the limits — a rubber band that snaps
      // back on release reads as a bug rather than as a boundary.
      const k = clamp(
        (dist / pinch.dist) * scaleRef.current,
        MIN_SCALE,
        MAX_SCALE
      ) / scaleRef.current
      // The browser is still scrolling under us; add back what it moved.
      const drift = {
        x: scroller.scrollLeft - pinch.left0,
        y: scroller.scrollTop - pinch.top0,
      }
      const tx = pinch.fx * (1 - k) + (mx - pinch.mx) + drift.x
      const ty = pinch.fy * (1 - k) + (my - pinch.my) + drift.y
      live = { k, mx, my }
      paint(`translate3d(${tx}px, ${ty}px, 0) scale(${k})`)
    }

    const onUp = (event: PointerEvent) => {
      if (event.pointerType !== 'touch') return
      points.delete(event.pointerId)

      if (pinch && points.size < 2) {
        endPinch()
        // The finger still on the glass must not be read as the start of a tap.
        down = null
        return
      }

      if (points.size !== 0) return
      const tap = down
      down = null
      if (!tap || tap.moved || Date.now() - tap.t > 300) {
        lastTap = null
        return
      }
      const now = Date.now()
      const previous = lastTap
      lastTap = { t: now, x: event.clientX, y: event.clientY }
      if (
        previous &&
        now - previous.t < 320 &&
        Math.hypot(event.clientX - previous.x, event.clientY - previous.y) < 44
      ) {
        lastTap = null
        const anchor = anchorAt(event.clientX, event.clientY)
        commit(scaleRef.current > 1.01 ? MIN_SCALE : TAP_SCALE, anchor)
      }
    }

    const onCancel = (event: PointerEvent) => {
      points.delete(event.pointerId)
      down = null
      if (pinch && points.size < 2) endPinch()
    }

    // A trackpad pinch arrives as ctrl+wheel. Passive is not an option: the
    // default is the browser zooming the whole page, which throws away the
    // layout the whole app is built on.
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return
      event.preventDefault()
      const anchor = anchorAt(event.clientX, event.clientY)
      commit(scaleRef.current * Math.exp(-event.deltaY / 220), anchor)
    }

    const onDoubleClick = (event: MouseEvent) => {
      const anchor = anchorAt(event.clientX, event.clientY)
      commit(scaleRef.current > 1.01 ? MIN_SCALE : TAP_SCALE, anchor)
    }

    scroller.addEventListener('pointerdown', onDown)
    scroller.addEventListener('pointermove', onMove)
    scroller.addEventListener('pointerup', onUp)
    scroller.addEventListener('pointercancel', onCancel)
    scroller.addEventListener('wheel', onWheel, { passive: false })
    scroller.addEventListener('dblclick', onDoubleClick)

    return () => {
      scroller.removeEventListener('pointerdown', onDown)
      scroller.removeEventListener('pointermove', onMove)
      scroller.removeEventListener('pointerup', onUp)
      scroller.removeEventListener('pointercancel', onCancel)
      scroller.removeEventListener('wheel', onWheel)
      scroller.removeEventListener('dblclick', onDoubleClick)
      paint('')
    }
  }, [scrollRef, contentRef, anchorAt, commit])

  return { scale, isZoomed: scale > 1.01, gesturing, zoomIn, zoomOut, fitWidth, reset }
}

// ── which pages matter right now ─────────────────────────────────────────────

export type PageWindow = {
  /** Pages close enough to the viewport to be worth having in memory. */
  near: Set<number>
  /** The page the reader is looking at, 1-based. */
  current: number
  /** Attach to each page slot: `ref={register(n)}`. */
  register: (n: number) => (element: HTMLElement | null) => void
  /** Put page `n` at the top of the viewport. */
  scrollTo: (n: number) => void
}

/**
 * Lazy loading and the page indicator, from the same two observers.
 *
 * `near` is deliberately generous — two viewports either side — so a page has
 * been decoded well before it is scrolled to, and a fast flick does not show a
 * column of grey boxes. Pages outside it are dropped, because a hundred-page
 * report at 110 ppi is more bitmap than a phone will hold.
 *
 * The slots keep their height whether or not the image is in them, so nothing
 * here can move the scroll position. That is the property that makes lazy
 * loading invisible instead of infuriating.
 */
export function useVisiblePages(
  scrollRef: RefObject<HTMLElement | null>,
  count: number
): PageWindow {
  const [near, setNear] = useState<Set<number>>(() => new Set([1, 2]))
  const [current, setCurrent] = useState(1)
  const slots = useRef(new Map<number, HTMLElement>())
  const loader = useRef<IntersectionObserver | null>(null)
  const indicator = useRef<IntersectionObserver | null>(null)

  // One callback per page, cached: a fresh function every render would make
  // React detach and re-attach every slot on every scale change. The slots
  // are also observed here rather than in the effect below, because a slot can
  // appear long after the observers do — the pane is `display: none` until
  // somebody taps Read, and nothing is laid out until it is not.
  const callbacks = useRef(new Map<number, (element: HTMLElement | null) => void>())
  const register = useCallback((n: number) => {
    const existing = callbacks.current.get(n)
    if (existing) return existing
    const callback = (element: HTMLElement | null) => {
      const previous = slots.current.get(n)
      if (previous && previous !== element) {
        loader.current?.unobserve(previous)
        indicator.current?.unobserve(previous)
      }
      if (element) {
        slots.current.set(n, element)
        loader.current?.observe(element)
        indicator.current?.observe(element)
      } else {
        slots.current.delete(n)
      }
    }
    callbacks.current.set(n, callback)
    return callback
  }, [])

  const scrollTo = useCallback(
    (n: number) => {
      const root = scrollRef.current
      const slot = slots.current.get(n)
      if (!root || !slot) return
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      root.scrollTo({
        top: Math.max(0, slot.offsetTop - 8),
        behavior: reduced ? 'auto' : 'smooth',
      })
    },
    [scrollRef]
  )

  useEffect(() => {
    const root = scrollRef.current
    if (!root || count === 0) return

    const nearby = new Set<number>()
    const ratios = new Map<number, number>()
    const numberOf = (element: Element): number =>
      Number((element as HTMLElement).dataset.page ?? 0)

    loader.current = new IntersectionObserver(
      (entries) => {
        let changed = false
        for (const entry of entries) {
          const n = numberOf(entry.target)
          if (!n) continue
          if (entry.isIntersecting) {
            if (!nearby.has(n)) {
              nearby.add(n)
              changed = true
            }
          } else if (nearby.delete(n)) {
            changed = true
          }
        }
        if (changed) setNear(new Set(nearby))
      },
      { root, rootMargin: '200% 0px', threshold: 0 }
    )

    indicator.current = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const n = numberOf(entry.target)
          if (n) ratios.set(n, entry.intersectionRatio)
        }
        let best = 0
        let bestRatio = 0
        for (const [n, ratio] of ratios) {
          if (ratio > bestRatio + 0.001) {
            best = n
            bestRatio = ratio
          }
        }
        // Every ratio is zero while this pane is hidden. Keeping the last
        // answer is right: nothing was read, so nothing changed.
        if (best) setCurrent(best)
      },
      { root, threshold: [0, 0.1, 0.25, 0.5, 0.75, 1] }
    )

    for (const element of slots.current.values()) {
      loader.current.observe(element)
      indicator.current.observe(element)
    }

    return () => {
      loader.current?.disconnect()
      indicator.current?.disconnect()
      loader.current = null
      indicator.current = null
    }
  }, [scrollRef, count])

  return { near, current, register, scrollTo }
}
