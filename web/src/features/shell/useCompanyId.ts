import { useParams } from 'react-router'

/** companyId from the URL; only valid beneath /c/:companyId. */
export function useCompanyId(): string {
  const { companyId } = useParams()
  if (!companyId) throw new Error('useCompanyId used outside /c/:companyId')
  return companyId
}

export function useAgentId(): string {
  const { agentId } = useParams()
  if (!agentId) throw new Error('useAgentId used outside .../agents/:agentId')
  return agentId
}
