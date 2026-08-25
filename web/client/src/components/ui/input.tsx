import type * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * `text-base lg:text-sm` is not a style choice. Below 16px, iOS Safari zooms the
 * whole viewport the moment the caret lands in the field, and the way back out
 * is a pinch the user did not ask to perform. So the phone gets 16px and the
 * desktop gets the app's 14px, and no input anywhere may override the first half
 * of that.
 */
function Input({ className, type, ...props }: React.ComponentProps<'input'>) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        'flex h-11 w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-1 text-base shadow-xs transition-[color,box-shadow] outline-none lg:h-9 lg:text-sm',
        'selection:bg-primary selection:text-primary-foreground placeholder:text-muted-foreground',
        'file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground',
        'disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30',
        'focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50',
        'aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40',
        className
      )}
      {...props}
    />
  )
}

export { Input }
