import { useEffect, useImperativeHandle, useRef, useState, type Ref } from 'react'
import { Maximize2, RotateCw, ZoomIn, ZoomOut } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  DEFAULT_ASPECT,
  MAX_PAGE_WIDTH,
  MAX_SCALE,
  MIN_SCALE,
  pageUrl,
  useMeasuredWidth,
  useVisiblePages,
  useZoomPan,
} from '@/lib/pages'
import { cn } from '@/lib/utils'
import type { PagesIndex } from '@/lib/api'

/**
 * The built report, as pages.
 *
 * A column of PNGs that scrolls vertically, one page across the width, pinchable
 * and double-tappable. It knows nothing about reports beyond the id it is given
 * and the index it is handed: which pages exist is the server's answer, and this
 * component only decides where they go and when to fetch them.
 *
 * The three properties that make it feel like a document rather than a web page:
 *
 *   1. Every slot reserves its exact height before the image arrives, so lazy
 *      loading can never move the page under the reader's thumb.
 *   2. Zoom is a layout change, not a permanent transform, so text is rasterised
 *      at the size it is read at and panning is the platform's own scrolling.
 *   3. Pinch and double-tap are implemented, not inherited. `touch-action` has
 *      to exclude `pinch-zoom` for the gestures to reach us at all, which means
 *      the browser's own zoom is gone and we owe the reader a replacement.
 */

export type PageViewerHandle = {
  scrollToPage: (n: number) => void
  fitWidth: () => void
}

export type PageViewerProps = {
  reportId: string
  index: PagesIndex
  /** Bumped after a build. Page URLs do not change, so without this the browser
   *  keeps showing the previous build's images. */
  version?: number
  onPageChange?: (page: number) => void
  className?: string
  ref?: Ref<PageViewerHandle>
}

export function PageViewer({
  reportId,
  index,
  version = 0,
  onPageChange,
  className,
  ref,
}: PageViewerProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)

  const width = useMeasuredWidth(scrollRef)
  const { scale, isZoomed, zoomIn, zoomOut, fitWidth, reset } = useZoomPan({ scrollRef, contentRef })
  const { near, current, register, scrollTo } = useVisiblePages(scrollRef, index.count)

  useImperativeHandle(ref, () => ({ scrollToPage: scrollTo, fitWidth }), [scrollTo, fitWidth])

  useEffect(() => {
    onPageChange?.(current)
  }, [current, onPageChange])

  // A different document is a different reading position. A rebuild of the same
  // one is not, so this deliberately does not fire on `version`.
  useEffect(() => {
    reset()
  }, [reportId, reset])

  // Pages are assumed to be the same size until one proves otherwise: almost
  // every report is, and measuring each page separately would mean a state
  // update per image with nothing to show for it.
  const [aspect, setAspect] = useState(DEFAULT_ASPECT)
  const [odd, setOdd] = useState<Record<number, number>>({})
  const [broken, setBroken] = useState<Record<number, number>>({})
  const measured = useRef(false)
  const aspectRef = useRef(DEFAULT_ASPECT)
  aspectRef.current = aspect

  const column = Math.max(160, Math.min(width - 16, MAX_PAGE_WIDTH)) * scale
  const pages = Array.from({ length: index.count }, (_, i) => i + 1)

  return (
    <div className={cn('relative flex min-h-0 flex-1 flex-col', className)}>
      <div
        ref={scrollRef}
        tabIndex={0}
        role="group"
        aria-label={`${index.count} ${index.count === 1 ? 'page' : 'pages'}, page ${current}`}
        className={cn(
          // Its own scroll box in both axes: when a page is zoomed past the
          // screen it is this element that scrolls sideways, never the document.
          'relative flex-1 overflow-auto overscroll-contain bg-muted/50 outline-none',
          'focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset'
        )}
        style={{
          // One finger scrolls natively, two fingers reach `useZoomPan`.
          // Excluding `pinch-zoom` also removes the browser's double-tap zoom
          // and the 300ms delay that comes with it.
          touchAction: 'pan-x pan-y',
          // The page column is sized from this element's width, and its height
          // follows from that width. Letting a desktop scrollbar come and go
          // would change the width, which changes the height, which decides
          // whether there is a scrollbar — a loop. Reserving the gutter is what
          // stops the question from being circular.
          scrollbarGutter: 'stable',
        }}
      >
        {width > 0 && (
          <div
            ref={contentRef}
            className="mx-auto flex flex-col items-stretch gap-2 py-2"
            style={{ width: column }}
          >
            {pages.map((n) => {
              const ratio = odd[n] ?? aspect
              const visible = near.has(n)
              const failed = broken[n] !== undefined
              return (
                <div
                  key={n}
                  data-page={n}
                  ref={register(n)}
                  className="relative w-full overflow-hidden rounded-sm border bg-card shadow-sm"
                  style={{ aspectRatio: String(ratio) }}
                >
                  {failed ? (
                    <button
                      type="button"
                      onClick={() =>
                        setBroken((prev) => {
                          const next = { ...prev }
                          delete next[n]
                          return next
                        })
                      }
                      className="tap absolute inset-0 flex flex-col items-center justify-center gap-2 px-4 text-center text-xs text-muted-foreground active:bg-accent"
                    >
                      <RotateCw className="size-4" aria-hidden />
                      <span>Page {n} did not load. Tap to try again.</span>
                    </button>
                  ) : visible ? (
                    <img
                      src={pageUrl(index, reportId, n, version)}
                      alt={`Page ${n} of ${index.count}`}
                      draggable={false}
                      // Not `loading="lazy"`: the near window above already
                      // decided this page is worth having, and the browser's own
                      // heuristic is tighter than ours — it would sit on the
                      // prefetch until the page was nearly on screen, which is
                      // the delay the window exists to avoid.
                      decoding="async"
                      className="absolute inset-0 block h-full w-full select-none"
                      onLoad={(event) => {
                        const image = event.currentTarget
                        const measuredRatio = image.naturalWidth / image.naturalHeight
                        if (!Number.isFinite(measuredRatio) || measuredRatio <= 0) return
                        if (!measured.current) {
                          measured.current = true
                          setAspect(measuredRatio)
                          return
                        }
                        const drift =
                          Math.abs(measuredRatio - aspectRef.current) / aspectRef.current
                        if (drift > 0.01) {
                          setOdd((prev) =>
                            prev[n] === measuredRatio ? prev : { ...prev, [n]: measuredRatio }
                          )
                        }
                      }}
                      onError={() => setBroken((prev) => ({ ...prev, [n]: Date.now() }))}
                    />
                  ) : (
                    <Skeleton className="absolute inset-0 size-full rounded-none" />
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* The zoom controls exist because pinching is a gesture, and a gesture is
          not an affordance everybody can perform. Bottom right, above the tab
          bar, out of the way of the text — and never the only way to do this. */}
      <div className="pointer-events-none absolute right-3 bottom-3 flex flex-col gap-2">
        {isZoomed && (
          <Button
            variant="outline"
            size="icon"
            onClick={fitWidth}
            aria-label="Fit the page to the width"
            className="pointer-events-auto border-border bg-background/95 shadow-md dark:bg-background/95"
          >
            <Maximize2 aria-hidden />
          </Button>
        )}
        <Button
          variant="outline"
          size="icon"
          onClick={zoomIn}
          disabled={scale >= MAX_SCALE - 0.01}
          aria-label="Zoom in"
          className="pointer-events-auto border-border bg-background/95 shadow-md dark:bg-background/95"
        >
          <ZoomIn aria-hidden />
        </Button>
        <Button
          variant="outline"
          size="icon"
          onClick={zoomOut}
          disabled={scale <= MIN_SCALE + 0.01}
          aria-label="Zoom out"
          className="pointer-events-auto border-border bg-background/95 shadow-md dark:bg-background/95"
        >
          <ZoomOut aria-hidden />
        </Button>
      </div>
    </div>
  )
}
