import * as SheetPrimitive from '@radix-ui/react-dialog'
import { XIcon } from 'lucide-react'
import type * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * A sheet: content that arrives from an edge over what you were looking at.
 *
 * On a phone this is where findings, sources and the pad live, and it comes up
 * from the bottom for one reason — that is the half of the screen a thumb can
 * reach. Its close affordance is the grab handle and a 44px button, never a
 * 16px cross in the far corner, and it caps at 85dvh so the thing underneath
 * stays visible and tappable.
 *
 * `dvh`, not `vh`: with a retracted address bar, `vh` puts the bottom of the
 * sheet below the bottom of the screen.
 */

function Sheet(props: React.ComponentProps<typeof SheetPrimitive.Root>) {
  return <SheetPrimitive.Root data-slot="sheet" {...props} />
}

function SheetTrigger(props: React.ComponentProps<typeof SheetPrimitive.Trigger>) {
  return <SheetPrimitive.Trigger data-slot="sheet-trigger" {...props} />
}

function SheetClose(props: React.ComponentProps<typeof SheetPrimitive.Close>) {
  return <SheetPrimitive.Close data-slot="sheet-close" {...props} />
}

function SheetOverlay({
  className,
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Overlay>) {
  return (
    <SheetPrimitive.Overlay
      data-slot="overlay"
      className={cn('fixed inset-0 z-50 bg-black/50', className)}
      {...props}
    />
  )
}

function SheetContent({
  className,
  children,
  side = 'bottom',
  showHandle = true,
  showCloseButton = true,
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Content> & {
  side?: 'bottom' | 'right'
  showHandle?: boolean
  showCloseButton?: boolean
}) {
  return (
    <SheetPrimitive.Portal>
      <SheetOverlay />
      <SheetPrimitive.Content
        data-slot="sheet-content"
        data-rm-surface={side === 'bottom' ? 'sheet' : 'sheet-right'}
        data-side={side}
        className={cn(
          'fixed z-50 flex flex-col border bg-background shadow-lg',
          side === 'bottom' &&
            // The safe-area inset is padding inside the sheet, not a gap under
            // it: a sheet that stops short of the bottom edge shows a strip of
            // whatever was behind it and reads as a rendering bug.
            'inset-x-0 bottom-0 max-h-[85dvh] rounded-t-2xl pb-[var(--safe-bottom)]',
          side === 'right' &&
            'inset-y-0 right-0 w-full max-w-md border-l pt-[var(--safe-top)] pb-[var(--safe-bottom)]',
          className
        )}
        {...props}
      >
        {side === 'bottom' && showHandle && (
          <div className="flex shrink-0 justify-center pt-2 pb-1" aria-hidden>
            <div className="h-1 w-10 rounded-full bg-border" />
          </div>
        )}
        {children}
        {showCloseButton && (
          <SheetPrimitive.Close
            data-slot="sheet-close"
            className="tap absolute top-2 right-2 inline-flex items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 active:bg-accent"
          >
            <XIcon className="size-5" />
            <span className="sr-only">Close</span>
          </SheetPrimitive.Close>
        )}
      </SheetPrimitive.Content>
    </SheetPrimitive.Portal>
  )
}

function SheetHeader({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="sheet-header"
      className={cn('flex shrink-0 flex-col gap-1 px-4 pt-2 pb-3 pr-14', className)}
      {...props}
    />
  )
}

/** The scrolling middle. Everything long in a sheet belongs in one of these. */
function SheetBody({ className, ...props }: React.ComponentProps<'div'>) {
  return <div data-slot="sheet-body" className={cn('pane flex-1 px-4 pb-4', className)} {...props} />
}

function SheetFooter({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="sheet-footer"
      className={cn(
        'flex shrink-0 flex-col-reverse gap-2 border-t px-4 pt-3 pb-3 sm:flex-row sm:justify-end',
        className
      )}
      {...props}
    />
  )
}

function SheetTitle({ className, ...props }: React.ComponentProps<typeof SheetPrimitive.Title>) {
  return (
    <SheetPrimitive.Title
      data-slot="sheet-title"
      className={cn('text-base leading-tight font-semibold break-anywhere', className)}
      {...props}
    />
  )
}

function SheetDescription({
  className,
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Description>) {
  return (
    <SheetPrimitive.Description
      data-slot="sheet-description"
      className={cn('text-sm text-muted-foreground break-anywhere', className)}
      {...props}
    />
  )
}

export {
  Sheet,
  SheetBody,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetOverlay,
  SheetTitle,
  SheetTrigger,
}
