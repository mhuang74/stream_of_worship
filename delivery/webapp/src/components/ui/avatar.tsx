import { mergeProps } from "@base-ui/react/merge-props"
import { useRender } from "@base-ui/react/use-render"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const avatarVariants = cva(
  "relative flex shrink-0 overflow-hidden rounded-full",
  {
    variants: {
      size: {
        default: "size-10",
        sm: "size-8",
        lg: "size-12",
      },
    },
    defaultVariants: {
      size: "default",
    },
  }
)

function Avatar({
  className,
  size,
  render,
  ...props
}: useRender.ComponentProps<"span"> & VariantProps<typeof avatarVariants>) {
  return useRender({
    defaultTagName: "span",
    props: mergeProps<"span">(
      {
        className: cn(avatarVariants({ size }), className),
      },
      props
    ),
    render,
    state: {
      slot: "avatar",
    },
  })
}

function AvatarFallback({
  className,
  render,
  ...props
}: useRender.ComponentProps<"span">) {
  return useRender({
    defaultTagName: "span",
    props: mergeProps<"span">(
      {
        className: cn(
          "flex size-full items-center justify-center rounded-full bg-muted",
          className
        ),
      },
      props
    ),
    render,
    state: {
      slot: "avatar-fallback",
    },
  })
}

export { Avatar, AvatarFallback, avatarVariants }
