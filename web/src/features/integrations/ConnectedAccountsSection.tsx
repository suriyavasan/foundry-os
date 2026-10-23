import { Lock, Plus, ShieldCheck, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { errorMessage, useConnectedAccounts, useRevokeConnectedAccount, useTestConnectedAccount } from '@/api'
import type { ConnectedAccount } from '@/api/types'
import { Badge, Boundary, Button, Card, CardHeader, Chip, ConfirmDialog, TBody, TD, TH, THead, TR, Table, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useCompanyRole } from '@/features/canvas/hooks'
import { ACCOUNT_STATUS_LABEL, ACCOUNT_STATUS_TONE, BOUNDARY_COPY, describeCheck, providerDef, sortAccounts } from '@/lib/connectedAccounts'
import { formatDateTime, formatRelative } from '@/lib/format'

/** Connected accounts: metadata only. The tokens live encrypted on the server and are never shown. */
export function ConnectedAccountsSection({ companyId }: { companyId: string }) {
  const q = useConnectedAccounts(companyId)
  const test = useTestConnectedAccount(companyId)
  const revoke = useRevokeConnectedAccount(companyId)
  const { openConnectAccount } = useCanvas()
  const owner = useCompanyRole(companyId) === 'owner'
  const toast = useToast()
  const [results, setResults] = useState<Record<string, { ok: boolean; text: string }>>({})
  const [testing, setTesting] = useState<string | null>(null)
  const [revoking, setRevoking] = useState<ConnectedAccount | null>(null)

  function runTest(a: ConnectedAccount) {
    setTesting(a.id)
    test.mutate(a.id, {
      onSuccess: (check) => setResults((r) => ({ ...r, [a.id]: describeCheck(check) })),
      onError: (e) => setResults((r) => ({ ...r, [a.id]: { ok: false, text: errorMessage(e) } })),
      onSettled: () => setTesting(null),
    })
  }

  return (
    <Card id="connected-accounts" aria-label="Connected accounts">
      <CardHeader
        title="Connected accounts"
        description={
          <span className="inline-flex items-start gap-1">
            <Lock className="mt-0.5 size-3 shrink-0" aria-hidden /> <span>Tokens are stored encrypted, are write-only, and never reach the browser or the agents.</span>
          </span>
        }
        actions={
          owner && q.data ? (
            <Button size="sm" variant="primary" icon={<Plus className="size-3.5" />} onClick={() => openConnectAccount()}>
              Connect an account
            </Button>
          ) : undefined
        }
      />
      <p className="flex items-start gap-2 border-b border-line bg-raised px-4 py-2 text-[12.5px] text-fg-2">
        <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-accent-fg" aria-hidden />
        <span>{BOUNDARY_COPY}</span>
      </p>
      <Boundary
        query={q}
        milestone="M8"
        compact={false}
        unavailableTitle="Connected accounts are not built yet"
        unavailableText="Connecting Cloudflare, GitHub, Vercel and email providers arrives with reachability."
        isEmpty={(d) => d.length === 0}
        empty={{ title: 'No accounts connected', description: 'Connect Cloudflare, GitHub, an email provider and the rest once; the AI Co-founder operates them within your guardrails.' }}
      >
        {(items) => (
          <Table aria-label="Connected accounts list">
            <THead>
              <TR>
                <TH>Provider</TH>
                <TH>Account</TH>
                <TH>Status</TH>
                <TH>Scopes</TH>
                {owner && (
                  <TH>
                    <span className="sr-only">Actions</span>
                  </TH>
                )}
              </TR>
            </THead>
            <TBody>
              {sortAccounts(items).map((a) => {
                const def = providerDef(a.provider)
                const r = results[a.id]
                const revoked = a.status === 'revoked'
                return (
                  <TR key={a.id} className="align-top">
                    <TD className="align-top">
                      <div className="font-medium">{def?.label ?? a.provider}</div>
                      {def && <div className="text-[12px] text-fg-3">{def.purpose}</div>}
                    </TD>
                    <TD className="align-top">
                      <div>{a.label}</div>
                      {a.metadata.account_name && <div className="text-[12px] text-fg-3">{a.metadata.account_name}</div>}
                    </TD>
                    <TD className="align-top">
                      <Badge tone={ACCOUNT_STATUS_TONE[a.status] ?? 'neutral'}>{ACCOUNT_STATUS_LABEL[a.status] ?? a.status}</Badge>
                      <div className="mt-1 text-[12px] text-fg-3" title={formatDateTime(a.last_checked_at)}>
                        {a.last_checked_at ? `Checked ${formatRelative(a.last_checked_at)}` : 'Never checked'}
                      </div>
                      {r && (
                        <div role="status" className={r.ok ? 'mt-1 text-[12px] text-success' : 'mt-1 text-[12px] text-danger'}>
                          {r.ok ? 'Test passed. ' : 'Test failed. '}
                          <span className="break-words text-fg-2">{r.text}</span>
                        </div>
                      )}
                    </TD>
                    <TD className="max-w-xs align-top">
                      <div className="flex flex-wrap gap-1">
                        {a.scopes.length === 0 ? <span className="text-fg-3">None reported</span> : a.scopes.map((s) => <Chip key={s}>{s}</Chip>)}
                      </div>
                    </TD>
                    {owner && (
                      <TD className="align-top">
                        {!revoked && (
                          <div className="flex justify-end gap-1.5">
                            <Button size="sm" aria-label={`Test ${a.label}`} loading={testing === a.id} onClick={() => runTest(a)}>
                              Test
                            </Button>
                            <Button size="sm" variant="ghost" aria-label={`Revoke ${a.label}`} icon={<Trash2 className="size-3.5" />} onClick={() => setRevoking(a)}>
                              Revoke
                            </Button>
                          </div>
                        )}
                      </TD>
                    )}
                  </TR>
                )
              })}
            </TBody>
          </Table>
        )}
      </Boundary>
      <ConfirmDialog
        open={revoking !== null}
        onOpenChange={(o) => !o && !revoke.isPending && setRevoking(null)}
        title={`Revoke ${revoking?.label ?? 'account'}?`}
        confirmLabel="Revoke"
        loading={revoke.isPending}
        error={revoke.isError ? errorMessage(revoke.error) : null}
        description="Company OS stops using this connection, so the AI Co-founder can no longer act through it. The account itself is not touched; to cut off access completely, also revoke the token on the provider’s site."
        onConfirm={() => revoking && revoke.mutate(revoking.id, { onSuccess: () => { toast.success(`${revoking.label} revoked`); setRevoking(null) } })}
      />
    </Card>
  )
}
