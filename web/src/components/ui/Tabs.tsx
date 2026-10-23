import * as RadixTabs from '@radix-ui/react-tabs'
import clsx from 'clsx'
import type { ComponentProps } from 'react'

export const Tabs = RadixTabs.Root

export function TabsList({ className, ...rest }: ComponentProps<typeof RadixTabs.List>) {
  return <RadixTabs.List className={clsx('flex gap-1 overflow-x-auto border-b border-line', className)} {...rest} />
}

export function TabsTrigger({ className, ...rest }: ComponentProps<typeof RadixTabs.Trigger>) {
  return (
    <RadixTabs.Trigger
      className={clsx(
        '-mb-px h-9 border-b-2 border-transparent px-3 text-[13px] font-medium whitespace-nowrap text-fg-3 transition-colors',
        'hover:text-fg data-[state=active]:border-accent data-[state=active]:text-fg',
        className,
      )}
      {...rest}
    />
  )
}

export function TabsContent({ className, ...rest }: ComponentProps<typeof RadixTabs.Content>) {
  return <RadixTabs.Content className={clsx('pt-4 focus-visible:outline-none', className)} {...rest} />
}
