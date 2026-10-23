import { Lock } from 'lucide-react'
import { useCredentials } from '@/api'
import { Badge, Boundary, Card, CardHeader, TBody, TD, TH, THead, TR, Table } from '@/components/ui'
import { formatDateTime, formatRelative } from '@/lib/format'

/** Credential metadata only. The API never returns a secret value and this view has nowhere to show one. */
export function CredentialsSection({ companyId }: { companyId: string }) {
  const q = useCredentials(companyId)
  return (
    <Card>
      <CardHeader
        title="Credentials"
        description={
          <span className="inline-flex items-center gap-1">
            <Lock className="size-3" aria-hidden /> Stored encrypted on the server. Values are write-only and never shown.
          </span>
        }
      />
      <Boundary
        query={q}
        milestone="M3b"
        compact={false}
        unavailableTitle="The credential store is not built yet"
        unavailableText="Provider API keys will be stored encrypted once the credential store lands."
        isEmpty={(d) => d.length === 0}
        empty={{ title: 'No credentials', description: 'A credential is created when you add a provider that needs an API key.' }}
      >
        {(items) => (
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Kind</TH>
                <TH>Status</TH>
                <TH>Added</TH>
              </TR>
            </THead>
            <TBody>
              {items.map((c) => (
                <TR key={c.id}>
                  <TD className="font-medium">{c.name}</TD>
                  <TD className="text-fg-2">{c.kind}</TD>
                  <TD>{c.revoked_at ? <Badge tone="neutral">Revoked</Badge> : <Badge tone="success">Active</Badge>}</TD>
                  <TD className="text-fg-3" title={formatDateTime(c.created_at)}>
                    {c.created_at ? formatRelative(c.created_at) : '—'}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Boundary>
    </Card>
  )
}
