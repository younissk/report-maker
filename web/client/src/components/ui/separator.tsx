import type * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * A line. Written out rather than pulled from Radix — the primitive exists to
 * get `role` and `aria-orientation` right, and that is four lines of JSX. One
 * fewer package to keep current for the rest of the project's life.
 */
function Separator({
  className,
  orientation = 'horizontal',
  decorative = true,
  ...props
}: React.ComponentProps<'div'> & {
  orientation?: 'horizontal' | 'vertical'
  decorative?: boolean
}) {
  return (
    <div
      data-slot="separator"
      data-orientation={orientation}
      role={decorative ? 'none' : 'separator'}
      aria-orientation={decorative ? undefined : orientation}
      className={cn(
        'shrink-0 bg-border',
        orientation === 'horizontal' ? 'h-px w-full' : 'h-full w-px',
        className
      )}
      {...props}
    />
  )
}

export { Separator }
