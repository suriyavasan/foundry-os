import * as RadixDialog from '@radix-ui/react-dialog'
import { Menu } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { IconButton, drawerOverlay } from '@/components/ui'
import { Sidebar } from './Sidebar'

export function AppShell({ companyId, companyName, children }: { companyId: string; companyName: string; children: ReactNode }) {
  const [drawerOpen, setDrawerOpen] = useState(false)

  return (
    <div className="min-h-screen">
      <aside
        className="fixed inset-y-0 left-0 z-30 hidden w-(--sidebar-width) border-r border-line md:block"
        aria-label="Sidebar"
      >
        <Sidebar companyId={companyId} companyName={companyName} />
      </aside>

      <div className="sticky top-0 z-20 flex h-12 items-center gap-2 border-b border-line bg-surface px-3 md:hidden">
        <RadixDialog.Root open={drawerOpen} onOpenChange={setDrawerOpen}>
          <RadixDialog.Trigger asChild>
            <IconButton label="Open navigation">
              <Menu className="size-4.5" />
            </IconButton>
          </RadixDialog.Trigger>
          <RadixDialog.Portal>
            <RadixDialog.Overlay className={drawerOverlay} />
            <RadixDialog.Content className="fixed inset-y-0 left-0 z-50 w-72 max-w-[85vw] animate-[cos-slide-in-left_160ms_ease-out] border-r border-line shadow-pop">
              <RadixDialog.Title className="sr-only">Navigation</RadixDialog.Title>
              <RadixDialog.Description className="sr-only">Primary navigation and company switcher</RadixDialog.Description>
              <Sidebar companyId={companyId} companyName={companyName} onNavigate={() => setDrawerOpen(false)} />
            </RadixDialog.Content>
          </RadixDialog.Portal>
        </RadixDialog.Root>
        <span className="truncate text-[13px] font-semibold text-fg">{companyName}</span>
      </div>

      <div className="md:pl-(--sidebar-width)">
        <main id="main" className="min-w-0">
          {children}
        </main>
      </div>
    </div>
  )
}
