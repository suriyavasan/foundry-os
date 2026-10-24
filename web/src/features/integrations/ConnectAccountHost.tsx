import { lazy, Suspense } from 'react'
import { useCanvas } from '@/features/canvas/CanvasContext'

const ConnectAccountDialog = lazy(() => import('./ConnectAccountDialog').then((m) => ({ default: m.ConnectAccountDialog })))

/** Renders the "Connect an account" dialog wherever it was asked for: palette, checklist, Integrations. */
export function ConnectAccountHost() {
  const { companyId, connectAccount, closeConnectAccount } = useCanvas()
  if (!connectAccount) return null
  return (
    <Suspense fallback={null}>
      <ConnectAccountDialog key={connectAccount.provider ?? 'any'} companyId={companyId} initialProvider={connectAccount.provider} onClose={closeConnectAccount} />
    </Suspense>
  )
}
