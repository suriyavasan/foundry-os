import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { Plug, Settings } from 'lucide-react'
import { useMe } from '@/api'
import { Avatar } from '@/components/avatar'
import { useCanvas } from './CanvasContext'

/** Owner identity (local single-owner mode) with shortcuts to settings and integrations. */
export function OwnerMenu({ companyId }: { companyId: string }) {
  const me = useMe()
  const { openPanel } = useCanvas()
  const role = me.data?.memberships.find((m) => m.company_id === companyId)?.role
  const name = me.data?.user.display_name ?? 'Owner'
  const item = 'flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-[13px] text-fg outline-none data-[highlighted]:bg-hover'
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger aria-label={`Account: ${name}`} className="flex h-8 items-center gap-2 rounded-md px-1 transition-colors hover:bg-hover sm:pr-2">
        <Avatar family="human" size="sm" />
        <span className="hidden max-w-28 truncate text-[12.5px] text-fg-2 md:inline">{name}</span>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="end" sideOffset={6} className="z-[60] min-w-56 rounded-md bg-surface p-1 shadow-pop">
          <div className="px-2 py-1.5">
            <div className="text-[13px] font-medium text-fg">{name}</div>
            <div className="text-[12px] text-fg-3">
              {me.data?.user.email ?? 'Local owner'}
              {role ? ` · ${role}` : ''}
            </div>
          </div>
          <DropdownMenu.Separator className="my-1 h-px bg-line" />
          <DropdownMenu.Item className={item} onSelect={() => openPanel({ kind: 'settings' })}>
            <Settings className="size-4 text-fg-2" aria-hidden /> Settings & policy
          </DropdownMenu.Item>
          <DropdownMenu.Item className={item} onSelect={() => openPanel({ kind: 'integrations' })}>
            <Plug className="size-4 text-fg-2" aria-hidden /> Integrations
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}
