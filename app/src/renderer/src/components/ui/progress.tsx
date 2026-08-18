import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * A determinate bar, deliberately Radix-free: the app only ever shows progress
 * it can actually count — pages rendered, sources verified — so there is no
 * indeterminate state to model and no primitive worth the dependency.
 */
function Progress({
  className,
  value = 0,
  max = 100,
  ...props
}: Omit<React.ComponentProps<"div">, "children"> & {
  value?: number
  max?: number
}) {
  const percent = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0

  return (
    <div
      data-slot="progress"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
      className={cn(
        "relative h-1.5 w-full overflow-hidden rounded-full bg-muted",
        className
      )}
      {...props}
    >
      <div
        data-slot="progress-indicator"
        className="h-full rounded-full bg-primary transition-[width] duration-300 ease-out"
        style={{ width: `${percent}%` }}
      />
    </div>
  )
}

export { Progress }
