import clsx from 'clsx'
import { Moon, Sun } from 'lucide-react'
import { NavLink } from 'react-router'
import { useMe } from '@/api'
import { IconButton } from '@/components/ui'
import { CompanySwitcher } from './CompanySwitcher'
import { NAV_ITEMS } from './nav'
import { useTheme } from './ThemeProvider'

export function Sidebar({ companyId, companyName, onNavigate }: { companyId: string; companyName: string; onNavigate?: () => void }) {
  const { theme, toggle } = useTheme()
  const me = useMe()
  const owner = me.data?.user

  return (
    <div className="flex h-full flex-col bg-surface">
      <div className="flex items-center gap-2 px-3 pt-3 pb-2">
        <div className="mb-1 flex items-center gap-2 px-1 text-[11px] font-semibold tracking-[0.12em] text-fg-3 uppercase">
          Company OS
        </div>
      </div>
      <div className="px-3 pb-3">
        <CompanySwitcher companyId={companyId} companyName={companyName} onNavigate={onNavigate} />
      </div>
      <nav aria-label="Primary" className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        <ul className="space-y-0.5">
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
            <li key={to}>
              <NavLink
                to={`/c/${companyId}/${to}`}
                onClick={onNavigate}
                className={({ isActive }) =>
                  clsx(
                    'flex h-8 items-center gap-2.5 rounded-md px-2.5 text-[13px] font-medium transition-colors',
                    isActive ? 'bg-active text-fg' : 'text-fg-2 hover:bg-hover hover:text-fg',
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <Icon className={clsx('size-4 shrink-0', isActive ? 'text-accent-fg' : 'text-fg-3')} aria-hidden />
                    <span className="truncate">{label}</span>
                  </>
                )}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <div className="flex items-center gap-2 border-t border-line px-3 py-2.5">
        <span
          aria-hidden
          className="flex size-7 shrink-0 items-center justify-center rounded-full bg-active text-[12px] font-medium text-fg-2"
        >
          {(owner?.display_name ?? '?').charAt(0).toUpperCase()}
        </span>
        <div className="min-w-0 flex-1 leading-tight">
          <div className="truncate text-[12.5px] font-medium text-fg">{owner?.display_name ?? (me.isError ? 'Unknown user' : '…')}</div>
          <div className="truncate text-[11.5px] text-fg-3">{owner?.email ?? 'local owner'}</div>
        </div>
        <IconButton label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'} onClick={toggle}>
          {theme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </IconButton>
      </div>
    </div>
  )
}
