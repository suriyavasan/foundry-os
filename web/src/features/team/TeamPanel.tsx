import { Mail, Pencil, Trash2, UserMinus } from 'lucide-react'
import { useMemo, useState } from 'react'
import { errorMessage, useAgents, useInvitations, useMe, useMembers, useRevokeInvitation } from '@/api'
import type { Invitation, Member } from '@/api/types'
import { MemberAvatar } from '@/components/avatar'
import { Badge, Boundary, Button, Card, CardHeader, ConfirmDialog, TBody, TD, TH, THead, TR, Table, useToast } from '@/components/ui'
import { managerName } from '@/features/canvas/MemberHoverCard'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useCompanyRole } from '@/features/canvas/hooks'
import { formatDateTime, formatRelative } from '@/lib/format'
import { INVITATION_STATUS_LABEL, INVITATION_STATUS_TONE, isRevocable } from '@/lib/invite'
import { canManageTeam, roleLabel } from '@/lib/people'
import { InviteDialog } from './InviteDialog'
import { EditMemberDialog, RemoveMemberDialog } from './MemberDialogs'

export function TeamPanel() {
  const { companyId } = useCanvas()
  const members = useMembers(companyId)
  const agents = useAgents(companyId)
  const me = useMe()
  const role = useCompanyRole(companyId)
  const manage = canManageTeam(role)
  const [inviting, setInviting] = useState(false)
  const [editing, setEditing] = useState<Member | null>(null)
  const [removing, setRemoving] = useState<Member | null>(null)
  const list = useMemo(() => [...(members.data ?? [])].sort((a, b) => a.display_name.localeCompare(b.display_name)), [members.data])

  return (
    <PanelFrame
      title="Team"
      subtitle="The people in this company, how they are organized, and who is invited"
      actions={
        manage && members.data ? (
          <Button size="sm" variant="primary" icon={<Mail className="size-3.5" />} onClick={() => setInviting(true)}>
            Invite
          </Button>
        ) : undefined
      }
    >
      <div className="space-y-4">
        <Card>
          <CardHeader title="Members" description="Humans appear in the organization chart next to the AI agents and can be given tasks." />
          <Boundary query={members} milestone="M7" compact={false} unavailableTitle="Teams are not built yet" unavailableText="Inviting people and giving them work arrives with accounts." isEmpty={(d) => d.length === 0} empty={{ title: 'Just you so far', description: 'Invite a person to give them tasks, reviews and approvals.' }}>
            {() => (
              <Table>
                <THead>
                  <TR>
                    <TH>Person</TH>
                    <TH>Role</TH>
                    <TH>Reports to</TH>
                    <TH>Status</TH>
                    {manage && (
                      <TH>
                        <span className="sr-only">Actions</span>
                      </TH>
                    )}
                  </TR>
                </THead>
                <TBody>
                  {list.map((m) => {
                    const self = m.user_id === me.data?.user.id
                    return (
                      <TR key={m.id}>
                        <TD>
                          <div className="flex items-center gap-2.5">
                            <MemberAvatar name={m.display_name} size="sm" />
                            <div className="min-w-0">
                              <div className="flex items-center gap-1.5 font-medium">
                                <span className="truncate">{m.display_name}</span>
                                {self && <Badge tone="accent">You</Badge>}
                              </div>
                              <div className="truncate text-[11.5px] text-fg-3">
                                @{m.username}
                                {m.title ? ` · ${m.title}` : ''}
                              </div>
                            </div>
                          </div>
                        </TD>
                        <TD>
                          <Badge tone={m.role === 'owner' ? 'accent' : 'neutral'}>{roleLabel(m.role)}</Badge>
                        </TD>
                        <TD className="text-fg-2">{managerName(m.manager_ref, agents.data ?? [], list) ?? (m.manager_ref ? 'Unknown' : 'The Founder')}</TD>
                        <TD>
                          <Badge tone={m.status === 'active' ? 'success' : 'neutral'}>{m.status === 'active' ? 'Active' : m.status === 'disabled' ? 'Disabled' : 'Removed'}</Badge>
                        </TD>
                        {manage && (
                          <TD className="text-right whitespace-nowrap">
                            <Button size="sm" variant="ghost" icon={<Pencil className="size-3.5" />} aria-label={`Edit ${m.display_name}`} onClick={() => setEditing(m)}>
                              Edit
                            </Button>
                            {!self && (
                              <Button size="sm" variant="ghost" icon={<UserMinus className="size-3.5" />} aria-label={`Remove ${m.display_name}`} onClick={() => setRemoving(m)}>
                                Remove
                              </Button>
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
        </Card>

        {members.data && <InvitationsCard companyId={companyId} manage={manage} />}
      </div>

      {inviting && <InviteDialog companyId={companyId} agents={agents.data ?? []} members={list} canInviteOwner={role === 'owner'} onClose={() => setInviting(false)} />}
      {editing && (
        <EditMemberDialog
          companyId={companyId}
          member={editing}
          agents={agents.data ?? []}
          members={list}
          isSelf={editing.user_id === me.data?.user.id}
          canInviteOwner={role === 'owner'}
          onClose={() => setEditing(null)}
        />
      )}
      {removing && <RemoveMemberDialog companyId={companyId} member={removing} onClose={() => setRemoving(null)} />}
    </PanelFrame>
  )
}

function InvitationsCard({ companyId, manage }: { companyId: string; manage: boolean }) {
  const q = useInvitations(companyId)
  const revoke = useRevokeInvitation(companyId)
  const toast = useToast()
  const [target, setTarget] = useState<Invitation | null>(null)
  return (
    <Card>
      <CardHeader title="Invitations" description="Links are single-use, tied to one email address, and expire after seven days." />
      <Boundary query={q} milestone="M7" isEmpty={(d) => d.length === 0} empty={{ title: 'No invitations', description: manage ? 'Use Invite to add a person.' : 'Owners and admins can invite people.' }} unavailableText="Invitations arrive with accounts.">
        {(items) => (
          <Table>
            <THead>
              <TR>
                <TH>Email</TH>
                <TH>Role</TH>
                <TH>Status</TH>
                <TH>Expires</TH>
                {manage && (
                  <TH>
                    <span className="sr-only">Actions</span>
                  </TH>
                )}
              </TR>
            </THead>
            <TBody>
              {[...items].sort((a, b) => b.created_at.localeCompare(a.created_at)).map((i) => (
                <TR key={i.id}>
                  <TD>
                    <div className="truncate">{i.email}</div>
                    <div className="text-[11.5px] text-fg-3">{i.title ?? 'No title'} · invited {formatRelative(i.created_at)}</div>
                  </TD>
                  <TD>{roleLabel(i.role)}</TD>
                  <TD>
                    <Badge tone={INVITATION_STATUS_TONE[i.status]}>{INVITATION_STATUS_LABEL[i.status]}</Badge>
                  </TD>
                  <TD className="text-fg-2">{i.status === 'pending' ? formatDateTime(i.expires_at) : '—'}</TD>
                  {manage && (
                    <TD className="text-right">
                      {isRevocable(i.status) && (
                        <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} aria-label={`Revoke invitation for ${i.email}`} onClick={() => setTarget(i)}>
                          Revoke
                        </Button>
                      )}
                    </TD>
                  )}
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Boundary>
      <ConfirmDialog
        open={!!target}
        onOpenChange={(o) => !o && setTarget(null)}
        title="Revoke this invitation?"
        description={
          <>
            The link sent to <strong className="font-medium text-fg">{target?.email}</strong> stops working immediately.
          </>
        }
        confirmLabel="Revoke invitation"
        loading={revoke.isPending}
        error={revoke.isError ? errorMessage(revoke.error) : null}
        onConfirm={() =>
          target &&
          revoke.mutate(target.id, {
            onSuccess: () => {
              toast.success('Invitation revoked')
              setTarget(null)
            },
          })
        }
      />
    </Card>
  )
}
