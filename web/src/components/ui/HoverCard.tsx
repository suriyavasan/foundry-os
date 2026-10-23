import * as RadixHoverCard from '@radix-ui/react-hover-card'
import clsx from 'clsx'
import type { ComponentProps } from 'react'

export const HoverCard = RadixHoverCard.Root
export const HoverCardTrigger = RadixHoverCard.Trigger

export function HoverCardContent({ className, sideOffset = 8, ...rest }: ComponentProps<typeof RadixHoverCard.Content>) {
  return (
    <RadixHoverCard.Portal>
      <RadixHoverCard.Content
        sideOffset={sideOffset}
        collisionPadding={12}
        className={clsx(
          'z-[60] w-80 max-w-[calc(100vw-1.5rem)] rounded-md border border-line-strong bg-surface p-3 text-[13px] shadow-pop',
          'data-[state=open]:animate-[cos-fade-in_120ms_ease-out]',
          className,
        )}
        {...rest}
      />
    </RadixHoverCard.Portal>
  )
}
