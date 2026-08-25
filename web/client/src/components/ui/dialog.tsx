import * as DialogPrimitive from '@radix-ui/react-dialog'
import { XIcon } from 'lucide-react'
import type * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * One dialog, two shapes.
 *
 * Below 1024px it is a bottom sheet: it rises from the edge of the screen a
 * thumb can actually reach, and the primary action sits at the bottom where the
 * hand already is. At 1024px and above it is the centred dialog the desktop app
 * uses. This is not a nicety — a centred modal on a 375px screen puts its
 * confirm button in the middle of the display and its close cross in the corner
 * furthest from every finger, and one-handed use stops being possible.
 *
 * The switch is CSS, and the two animations are keyed off `data-rm-surface` in
 * `styles.css` behind the same media query. No JavaScript breakpoint, so it is
 * right on the first frame and stays right through a rotation.
 */

function Dialog(props: React.ComponentProps<typeof DialogPrimitive.Root>) {
  return <DialogPrimitive.Root data-slot="dialog" {...props} />
}

function DialogTrigger(props: React.ComponentProps<typeof DialogPrimitive.Trigger>) {
  return <DialogPrimitive.Trigger data-slot="dialog-trigger" {...props} />
}

function DialogPortal(props: React.ComponentProps<typeof DialogPrimitive.Portal>) {
  return <DialogPrimitive.Portal data-slot="dialog-portal" {...props} />
}

function DialogClose(props: React.ComponentProps<typeof DialogPrimitive.Close>) {
  return <DialogPrimitive.Close data-slot="dialog-close" {...props} />
}

function DialogOverlay({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Overlay>) {
  return (
    <DialogPrimitive.Overlay
      data-slot="overlay"
      className={cn('fixed inset-0 z-50 bg-black/50', className)}
      {...props}
    />
  )
}

function DialogContent({
  className,
  children,
  showCloseButton = true,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content> & { showCloseButton?: boolean }) {
  return (
    <DialogPortal>
      <DialogOverlay />
      <DialogPrimitive.Content
        data-slot="dialog-content"
        data-rm-surface="responsive"
        className={cn(
          'fixed z-50 flex flex-col border bg-background shadow-lg',
          // Phone: a sheet on the bottom edge, capped so what is underneath
          // stays visible, with the home-indicator inset padded inside it.
          'inset-x-0 bottom-0 max-h-[85dvh] rounded-t-2xl pb-[var(--safe-bottom)]',
          // Desktop: the app's centred dialog.
          'lg:inset-auto lg:top-1/2 lg:left-1/2 lg:bottom-auto lg:max-h-[85vh] lg:w-full lg:max-w-lg lg:-translate-x-1/2 lg:-translate-y-1/2 lg:rounded-lg lg:pb-0',
          className
        )}
        {...props}
      >
        <div className="flex shrink-0 justify-center pt-2 pb-1 lg:hidden" aria-hidden>
          <div className="h-1 w-10 rounded-full bg-border" />
        </div>
        {children}
        {showCloseButton && (
          <DialogPrimitive.Close
            data-slot="dialog-close"
            className="tap absolute top-2 right-2 inline-flex items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 active:bg-accent lg:top-3 lg:right-3 lg:size-8 lg:min-h-0 lg:min-w-0"
          >
            <XIcon className="size-5 lg:size-4" />
            <span className="sr-only">Close</span>
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Content>
    </DialogPortal>
  )
}

function DialogHeader({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="dialog-header"
      className={cn('flex shrink-0 flex-col gap-1 px-4 pt-2 pr-14 pb-3 lg:px-6 lg:pt-6', className)}
      {...props}
    />
  )
}

/** The scrolling middle. Anything that can get long belongs in one of these. */
function DialogBody({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div data-slot="dialog-body" className={cn('pane flex-1 px-4 pb-4 lg:px-6', className)} {...props} />
  )
}

/**
 * On a phone the primary action is the full-width button at the bottom, because
 * that is where the thumb is; on a desktop it returns to the right-hand end of a
 * row. `flex-col-reverse` is what puts the primary child first in the DOM and
 * last on the screen, so tab order and reading order still agree.
 */
function DialogFooter({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="dialog-footer"
      className={cn(
        'flex shrink-0 flex-col-reverse gap-2 border-t px-4 py-3 lg:flex-row lg:justify-end lg:border-t-0 lg:px-6 lg:pb-6',
        '[&>[data-slot=button]]:w-full lg:[&>[data-slot=button]]:w-auto',
        className
      )}
      {...props}
    />
  )
}

function DialogTitle({ className, ...props }: React.ComponentProps<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title
      data-slot="dialog-title"
      className={cn('text-base leading-tight font-semibold break-anywhere', className)}
      {...props}
    />
  )
}

function DialogDescription({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Description>) {
  return (
    <DialogPrimitive.Description
      data-slot="dialog-description"
      className={cn('text-sm text-muted-foreground break-anywhere', className)}
      {...props}
    />
  )
}

export {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
  DialogTrigger,
}
