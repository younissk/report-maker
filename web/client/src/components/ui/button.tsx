import { cva, type VariantProps } from 'class-variance-authority'
import { Slot } from '@radix-ui/react-slot'
import type * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * The desktop app's button, with one change: every size is at least 44px tall
 * until the desktop breakpoint, where it drops to the app's own compact heights.
 * A control a thumb has to hit is not the same object as one a mouse points at,
 * and shipping the mouse-sized one to a phone is the single most common way a
 * desktop design fails on a screen it was never checked on.
 *
 * Hover is an enhancement and never the only signal — Tailwind v4 already gates
 * `hover:` behind `(hover: hover)`, so `active:` carries the feedback on touch.
 */
const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 rounded-md text-sm font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default:
          'bg-primary text-primary-foreground hover:bg-primary/90 active:bg-primary/80',
        destructive:
          'bg-destructive text-white hover:bg-destructive/90 active:bg-destructive/80 focus-visible:ring-destructive/20 dark:bg-destructive/60 dark:focus-visible:ring-destructive/40',
        outline:
          'border bg-background shadow-xs hover:bg-accent hover:text-accent-foreground active:bg-accent dark:border-input dark:bg-input/30 dark:hover:bg-input/50',
        secondary:
          'bg-secondary text-secondary-foreground hover:bg-secondary/80 active:bg-secondary/70',
        ghost:
          'hover:bg-accent hover:text-accent-foreground active:bg-accent dark:hover:bg-accent/50',
        link: 'text-primary underline underline-offset-4 hover:no-underline',
      },
      size: {
        default: 'h-11 px-4 py-2 lg:h-9 has-[>svg]:px-3',
        sm: 'h-10 gap-1.5 rounded-md px-3 lg:h-8 has-[>svg]:px-2.5',
        lg: 'h-12 rounded-md px-6 text-base lg:h-10 lg:text-sm has-[>svg]:px-4',
        icon: 'size-11 lg:size-9',
        'icon-sm': 'size-10 lg:size-8',
        'icon-lg': 'size-12 lg:size-10',
        /** Only for a dense desktop toolbar. Never put one on a phone. */
        xs: "h-6 gap-1 rounded-md px-2 text-xs has-[>svg]:px-1.5 [&_svg:not([class*='size-'])]:size-3",
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  }
)

function Button({
  className,
  variant = 'default',
  size = 'default',
  asChild = false,
  type,
  ...props
}: React.ComponentProps<'button'> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : 'button'
  return (
    <Comp
      data-slot="button"
      data-variant={variant}
      data-size={size}
      // A button inside a form with no type submits it. That has surprised
      // somebody on every project that ever shipped a form.
      type={asChild ? undefined : (type ?? 'button')}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
