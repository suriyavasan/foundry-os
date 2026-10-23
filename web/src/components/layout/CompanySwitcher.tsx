import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { Check, ChevronsUpDown, Plus } from 'lucide-react'
import { useNavigate } from 'react-router'
import { useCompanies } from '@/api'

export function CompanyAvatar({ name }: { name: string }) {
  return (
    <span
      aria-hidden
      className="flex size-6 shrink-0 items-center justify-center rounded-sm bg-accent-subtle text-[12px] font-semibold text-accent-fg"
    >
      {name.trim().charAt(0).toUpperCase() || '?'}
    </span>
  )
}

export function CompanySwitcher({ companyId, companyName, onNavigate }: { companyId: string; companyName: string; onNavigate?: () => void }) {
  const navigate = useNavigate()
  const companies = useCompanies()

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        className="flex h-8 max-w-[220px] items-center gap-2 rounded-md border border-line bg-raised px-2 text-left transition-colors hover:bg-hover"
        aria-label="Switch company"
      >
        <CompanyAvatar name={companyName} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium text-fg">{companyName}</span>
        </span>
        <ChevronsUpDown className="size-3.5 shrink-0 text-fg-3" aria-hidden />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          sideOffset={4}
          className="z-[60] max-h-80 min-w-60 overflow-y-auto rounded-md bg-surface p-1 shadow-pop"
        >
          <DropdownMenu.Label className="px-2 py-1 text-[11px] font-medium tracking-wide text-fg-3 uppercase">
            Companies
          </DropdownMenu.Label>
          {companies.isPending && <div className="px-2 py-1.5 text-[12.5px] text-fg-3">Loading…</div>}
          {companies.isError && <div className="px-2 py-1.5 text-[12.5px] text-danger">Could not load companies</div>}
          {companies.data?.map((c) => (
            <DropdownMenu.Item
              key={c.id}
              onSelect={() => {
                if (c.id !== companyId) navigate(`/c/${c.id}`)
                onNavigate?.()
              }}
              className="flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-[13px] text-fg outline-none data-[highlighted]:bg-hover"
            >
              <CompanyAvatar name={c.name} />
              <span className="min-w-0 flex-1 truncate">{c.name}</span>
              {c.id === companyId && <Check className="size-3.5 text-accent-fg" aria-label="Current" />}
            </DropdownMenu.Item>
          ))}
          <DropdownMenu.Separator className="my-1 h-px bg-line" />
          <DropdownMenu.Item
            onSelect={() => {
              navigate('/onboarding')
              onNavigate?.()
            }}
            className="flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-[13px] text-fg outline-none data-[highlighted]:bg-hover"
          >
            <Plus className="size-4 text-fg-2" aria-hidden />
            Create company
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}
