import { useState } from 'react'
import { isNotDeployed, useCliRuntimes } from '@/api'
import { ApiError } from '@/api/client'
import { Badge, EmptyState, ErrorState, LoadingState, NotAvailable } from '@/components/ui'
import { CliInstall } from './CliInstall'

/**
 * "Coding tools on this computer": every coding CLI Foundry supports, whether it is installed, and an Install button for the
 * installation owner (others see status only).
 */
export function CodingTools({ canInstall }: { canInstall: boolean }) {
  const clis = useCliRuntimes()
  const [touched, setTouched] = useState<ReadonlySet<string>>(new Set())

  let body
  if (clis.isPending) body = <LoadingState rows={3} label="Looking for coding tools" />
  else if (clis.isError) {
    if (isNotDeployed(clis.error)) {
      body = <NotAvailable milestone="M10" title="Coding tools are not available on this server yet" description="Update the server to manage coding tools here." />
    } else if (clis.error instanceof ApiError && clis.error.status === 403) {
      body = <p className="text-[12.5px] text-fg-3">Only the installation owner can see and install coding tools.</p>
    } else {
      body = <ErrorState error={clis.error} onRetry={() => void clis.refetch()} retrying={clis.isFetching} />
    }
  } else if (clis.data.length === 0) {
    body = <EmptyState title="No coding tools known" description="This server did not list any coding tools." />
  } else {
    body = (
      <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
        {clis.data.map((cli) => (
          <li key={cli.id} aria-label={cli.display_name} className="space-y-2 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-[13px] font-medium text-fg">{cli.display_name}</p>
                {cli.installed && <p className="font-mono text-[11.5px] text-fg-3">{cli.version ?? 'version unknown'}</p>}
              </div>
              <Badge tone={cli.installed ? 'success' : 'neutral'}>{cli.installed ? 'Installed' : 'Not installed'}</Badge>
            </div>
            {(!cli.installed || touched.has(cli.id)) &&
              (canInstall ? (
                <CliInstall cliId={cli.id} name={cli.display_name} canEdit onStarted={() => setTouched((s) => new Set(s).add(cli.id))} />
              ) : (
                <p className="text-[12px] text-fg-3">Only the installation owner can install tools.</p>
              ))}
          </li>
        ))}
      </ul>
    )
  }

  return (
    <section aria-label="Coding tools on this computer" className="space-y-2">
      <div>
        <h3 className="text-[13px] font-semibold text-fg">Coding tools on this computer</h3>
        <p className="text-[12px] text-fg-3">The command-line AIs Foundry can use. Installing one asks you to confirm exactly what will run.</p>
      </div>
      {body}
    </section>
  )
}
