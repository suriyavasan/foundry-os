import { useParams } from 'react-router'

/** companyId from the URL; only valid beneath /c/:companyId. */
export function useCompanyId(): string {
  const { companyId } = useParams()
  if (!companyId) throw new Error('useCompanyId used outside /c/:companyId')
  return companyId
}
