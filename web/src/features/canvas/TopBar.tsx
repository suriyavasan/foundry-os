import { Command, Moon, Sun, Target } from 'lucide-react'
import type { Company } from '@/api/types'
import { useTheme } from '@/components/layout/ThemeProvider'
import { CompanySwitcher } from '@/components/layout/CompanySwitcher'
import { Badge, Dot, IconButton, Tooltip } from '@/components/ui'
import { EmergencyBanner } from '@/features/guardrails/EmergencyStop'
import { NotificationBell } from '@/features/notifications/NotificationBell'
import { ConnectionIndicator } from './ConnectionIndicator'
import { useCanvas } from './CanvasContext'
import { OwnerMenu } from './OwnerMenu'
import { PauseControl } from './PauseControl'

export function isMac(): boolean {
  return typeof navigator !== 'undefined' && /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent)
}

/** Single compact row: identity, goal, live state, command palette, kill switch, theme, owner. */
export function TopBar({ company }: { company: Company }) {
  const { openPanel, setPaletteOpen } = useCanvas()
  const { theme, toggle } = useTheme()
  const active = company.status === 'active'
  const mod = isMac() ? '⌘' : 'Ctrl'

  return (
    <header className="sticky top-0 z-30 bg-surface">
      <EmergencyBanner />
      <div className="flex h-(--topbar-height) items-center gap-2 border-b border-line px-3">
        <CompanySwitcher companyId={company.id} companyName={company.name} />
        <Badge tone={active ? 'success' : 'warning'} className="hidden sm:inline-flex" aria-label={`Company status: ${active ? 'active' : 'paused'}`}>
          <Dot tone={active ? 'success' : 'warning'} />
          {active ? 'Active' : 'Paused'}
        </Badge>
        <button
          type="button"
          onClick={() => openPanel({ kind: 'goals' })}
          className="group hidden h-8 min-w-0 flex-1 items-center gap-1.5 rounded-md px-2 text-left transition-colors hover:bg-hover md:flex"
          title={company.strategic_goal ?? 'No strategic goal set. Open goals.'}
        >
          <Target className="size-3.5 shrink-0 text-fg-3" aria-hidden />
          <span className="truncate text-[12.5px] text-fg-2 group-hover:text-fg">{company.strategic_goal ?? <span className="text-fg-3">No strategic goal set</span>}</span>
        </button>
        <div className="flex-1 md:hidden" />

        <ConnectionIndicator />

        <button
          type="button"
          onClick={() => setPaletteOpen(true)}
          aria-label="Open command palette"
          aria-keyshortcuts="Meta+K Control+K"
          className="inline-flex h-8 items-center gap-2 rounded-md border border-line-strong bg-raised px-2 text-[12.5px] text-fg-3 transition-colors hover:bg-hover hover:text-fg"
        >
          <Command className="size-3.5" aria-hidden />
          <span className="hidden lg:inline">Search or jump to…</span>
          <kbd className="hidden rounded-sm border border-line-strong px-1 text-[11px] text-fg-3 sm:inline">{mod} K</kbd>
        </button>

        <PauseControl company={company} />

        <Tooltip content={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}>
          <IconButton label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'} onClick={toggle}>
            {theme === 'dark' ? <Sun className="size-4" aria-hidden /> : <Moon className="size-4" aria-hidden />}
          </IconButton>
        </Tooltip>
        <NotificationBell />
        <OwnerMenu companyId={company.id} />
      </div>
    </header>
  )
}
