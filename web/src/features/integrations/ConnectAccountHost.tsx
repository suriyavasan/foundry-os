import { useCanvas } from '@/features/canvas/CanvasContext'
import { ConnectAccountDialog } from './ConnectAccountDialog'

/** Renders the "Connect an account" dialog wherever it was asked for: palette, checklist, Integrations. */
export function ConnectAccountHost() {
  const { companyId, connectAccount, closeConnectAccount } = useCanvas()
  if (!connectAccount) return null
  return <ConnectAccountDialog key={connectAccount.provider ?? 'any'} companyId={companyId} initialProvider={connectAccount.provider} onClose={closeConnectAccount} />
}
