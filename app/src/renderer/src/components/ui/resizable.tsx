"use client"

import * as React from "react"
import { GripVerticalIcon } from "lucide-react"
import { Group, Panel, Separator } from "react-resizable-panels"

import { cn } from "@/lib/utils"

/**
 * react-resizable-panels v4 renamed everything — `Group` / `Panel` / `Separator`,
 * `orientation` instead of `direction` — and, more dangerously, changed how a
 * bare number is read: `defaultSize={20}` is now **20 pixels**, not 20 percent.
 * This wrapper restores the familiar shadcn names and the percentage reading, so
 * a panel written the way everyone remembers behaves the way everyone expects.
 * Pass an explicit unit ("240px", "20rem") when pixels are what you actually
 * want.
 */
type Size = number | string

function percent(size: Size | undefined): string | undefined {
  if (size === undefined) return undefined
  // A unitless string is already a percentage to the library; a number is not.
  return typeof size === "number" ? `${size}` : size
}

function ResizablePanelGroup({
  className,
  direction,
  orientation,
  ...props
}: React.ComponentProps<typeof Group> & {
  /** Alias for `orientation`, kept because every shadcn example uses it. */
  direction?: "horizontal" | "vertical"
}) {
  return (
    <Group
      data-slot="resizable-panel-group"
      orientation={orientation ?? direction ?? "horizontal"}
      className={cn("h-full w-full", className)}
      {...props}
    />
  )
}

function ResizablePanel({
  className,
  defaultSize,
  minSize,
  maxSize,
  collapsedSize,
  ...props
}: React.ComponentProps<typeof Panel>) {
  return (
    <Panel
      data-slot="resizable-panel"
      defaultSize={percent(defaultSize)}
      minSize={percent(minSize)}
      maxSize={percent(maxSize)}
      collapsedSize={percent(collapsedSize)}
      className={cn("overflow-hidden", className)}
      {...props}
    />
  )
}

function ResizableHandle({
  className,
  withHandle,
  children,
  ...props
}: React.ComponentProps<typeof Separator> & { withHandle?: boolean }) {
  return (
    <Separator
      data-slot="resizable-handle"
      className={cn(
        // The separator's aria-orientation is the opposite of the group's: a
        // row of panels is split by a vertical line.
        "relative bg-border transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-[orientation=horizontal]:h-px aria-[orientation=horizontal]:w-full aria-[orientation=vertical]:h-full aria-[orientation=vertical]:w-px data-[separator=active]:bg-ring data-[separator=hover]:bg-ring/60",
        className
      )}
      {...props}
    >
      {withHandle && (
        <div className="absolute top-1/2 left-1/2 z-10 flex h-4 w-3 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-xs border bg-border">
          <GripVerticalIcon className="size-2.5" />
        </div>
      )}
      {children}
    </Separator>
  )
}

export { ResizableHandle, ResizablePanel, ResizablePanelGroup }
