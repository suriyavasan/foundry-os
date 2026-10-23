import { Navigate, useLocation, useParams } from 'react-router'
import { legacyPanelFor, withPanel } from '@/lib/panels'
import { NotFound } from './NotFound'

/** Old sidebar URLs (/c/:id/tasks, /c/:id/organization/agents/:agentId ...) land on the canvas with the matching panel open. */
export function LegacyRedirect() {
  const { companyId, '*': rest = '' } = useParams()
  const { search } = useLocation()
  const panel = legacyPanelFor(rest)
  if (panel === undefined) {
    return <NotFound backTo={`/c/${companyId}`} backLabel="Back to the canvas" />
  }
  return <Navigate to={{ pathname: `/c/${companyId}`, search: withPanel(search, panel) }} replace />
}
