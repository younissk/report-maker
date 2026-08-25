import * as TabsPrimitive from '@radix-ui/react-tabs'
import type * as React from 'react'

import { cn } from '@/lib/utils'

function Tabs({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Root>) {
  return (
    <TabsPrimitive.Root
      data-slot="tabs"
      className={cn(
        'flex min-h-0 flex-col gap-2 data-[orientation=vertical]:flex-row',
        className
      )}
      {...props}
    />
  )
}

/**
 * The list scrolls sideways inside itself rather than wrapping or shrinking its
 * triggers below the tap floor. Four evidence tabs at 44px do not fit across
 * 375px, and the answer is a scroll, never a 30px target.
 */
function TabsList({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      className={cn(
        'scroll-x inline-flex w-full items-center justify-start rounded-lg bg-muted p-[3px] text-muted-foreground lg:w-fit',
        'data-[orientation=vertical]:h-fit data-[orientation=vertical]:flex-col',
        className
      )}
      {...props}
    />
  )
}

function TabsTrigger({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      data-slot="tabs-trigger"
      className={cn(
        'inline-flex min-h-11 flex-1 items-center justify-center gap-1.5 rounded-md border border-transparent px-3 py-1 text-sm font-medium whitespace-nowrap text-foreground transition-[color,box-shadow] outline-none lg:min-h-7',
        'focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50',
        'data-[orientation=vertical]:w-full data-[state=active]:bg-background data-[state=active]:shadow-sm',
        'dark:data-[state=active]:border-input dark:data-[state=active]:bg-input/30 dark:data-[state=active]:text-foreground',
        "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        className
      )}
      {...props}
    />
  )
}

function TabsContent({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return (
    <TabsPrimitive.Content
      data-slot="tabs-content"
      className={cn('min-h-0 flex-1 outline-none', className)}
      {...props}
    />
  )
}

export { Tabs, TabsContent, TabsList, TabsTrigger }
