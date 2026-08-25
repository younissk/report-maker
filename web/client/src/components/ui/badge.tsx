import { cva, type VariantProps } from 'class-variance-authority'
import { Slot } from '@radix-ui/react-slot'
import type * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * The desktop app's badge, plus the three the citation rule needs. A finding is
 * `error` or `warning`, a line is cited, assessed or unmarked — and those are
 * the same colours the editor rail and the density meter use, so a report read
 * three ways reads the same.
 */
const badgeVariants = cva(
  "inline-flex w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-full border border-transparent px-2 py-0.5 text-xs font-medium whitespace-nowrap transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 [&>svg]:pointer-events-none [&>svg]:size-3",
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground [a&]:hover:bg-primary/90',
        secondary: 'bg-secondary text-secondary-foreground [a&]:hover:bg-secondary/90',
        destructive:
          'bg-destructive text-white dark:bg-destructive/60 [a&]:hover:bg-destructive/90',
        outline:
          'border-border text-foreground [a&]:hover:bg-accent [a&]:hover:text-accent-foreground',
        ghost: '[a&]:hover:bg-accent [a&]:hover:text-accent-foreground',
        /** A `check` error — E002, E012, and the rest. */
        error: 'border-destructive/30 bg-destructive/10 text-destructive',
        /** A `check` warning — W001 and friends. Real, but not a broken build. */
        warning: 'border-rail-assessed/40 bg-rail-assessed/15 text-rail-assessed',
        cited: 'border-rail-cited/40 bg-rail-cited/15 text-rail-cited',
        assessed: 'border-rail-assessed/40 bg-rail-assessed/15 text-rail-assessed',
        unmarked: 'border-rail-unmarked/40 bg-rail-unmarked/15 text-rail-unmarked',
      },
    },
    defaultVariants: { variant: 'default' },
  }
)

function Badge({
  className,
  variant = 'default',
  asChild = false,
  ...props
}: React.ComponentProps<'span'> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : 'span'
  return (
    <Comp
      data-slot="badge"
      data-variant={variant}
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  )
}

export { Badge, badgeVariants }
