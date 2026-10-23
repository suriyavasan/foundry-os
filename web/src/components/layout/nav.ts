import {
  Activity,
  FolderKanban,
  LayoutDashboard,
  ListChecks,
  Network,
  Package,
  Plug,
  Settings,
  ShieldCheck,
  Wallet,
  type LucideIcon,
} from 'lucide-react'

export interface NavItem {
  to: string // path segment under /c/:companyId
  label: string
  icon: LucideIcon
}

export const NAV_ITEMS: NavItem[] = [
  { to: 'command-center', label: 'Command Center', icon: LayoutDashboard },
  { to: 'organization', label: 'Organization', icon: Network },
  { to: 'projects', label: 'Projects', icon: FolderKanban },
  { to: 'tasks', label: 'Tasks', icon: ListChecks },
  { to: 'artifacts', label: 'Artifacts', icon: Package },
  { to: 'activity', label: 'Activity & Decisions', icon: Activity },
  { to: 'finance', label: 'Finance & Usage', icon: Wallet },
  { to: 'approvals', label: 'Approvals', icon: ShieldCheck },
  { to: 'integrations', label: 'Integrations', icon: Plug },
  { to: 'settings', label: 'Settings', icon: Settings },
]

export const AREA_SEGMENTS = NAV_ITEMS.map((n) => n.to)
