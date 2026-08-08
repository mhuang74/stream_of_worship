"use client"

import * as React from "react"
import { Popover as PopoverPrimitive } from "@base-ui/react/popover"

import { cn } from "@/lib/utils"

function Popover({
  ...props
}: PopoverPrimitive.Root.Props) {
  return <PopoverPrimitive.Root {...props} />
}

function PopoverTrigger({
  asChild,
  children,
  ...props
}: PopoverPrimitive.Trigger.Props & { asChild?: boolean }) {
  return (
    <PopoverPrimitive.Trigger
      data-slot="popover-trigger"
      {...(asChild ? { render: children as React.ReactElement } : { children })}
      {...props}
    />
  )
}

function PopoverContent({
  className,
  align = "center",
  side,
  sideOffset = 4,
  collisionAvoidance,
  positionerClassName = "z-50",
  ...props
}: PopoverPrimitive.Popup.Props &
  Pick<
    PopoverPrimitive.Positioner.Props,
    "align" | "sideOffset" | "side" | "collisionAvoidance"
  > & {
    positionerClassName?: string;
  }) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Positioner
        align={align}
        side={side}
        sideOffset={sideOffset}
        collisionAvoidance={collisionAvoidance}
        className={positionerClassName}
      >
        <PopoverPrimitive.Popup
          data-slot="popover-content"
          className={cn(
            "bg-popover text-popover-foreground data-[starting-style]:data-[state=open]:animate-in data-[starting-style]:data-[state=open]:fade-in-0 data-[starting-style]:data-[state=open]:zoom-in-95 data-[ending-style]:data-[state=closed]:animate-out data-[ending-style]:data-[state=closed]:fade-out-0 data-[ending-style]:data-[state=closed]:zoom-out-95 relative max-h-(--available-height) origin-(--transform-origin) overflow-y-auto overflow-x-hidden rounded-md border p-4 shadow-md outline-none",
            className
          )}
          {...props}
        />
      </PopoverPrimitive.Positioner>
    </PopoverPrimitive.Portal>
  )
}

function PopoverClose({
  ...props
}: PopoverPrimitive.Close.Props) {
  return <PopoverPrimitive.Close data-slot="popover-close" {...props} />
}

export { Popover, PopoverTrigger, PopoverContent, PopoverClose }
