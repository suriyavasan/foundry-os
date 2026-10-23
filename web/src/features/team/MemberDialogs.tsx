import { useState, type FormEvent } from 'react'
import { errorMessage, useRemoveMember, useUpdateMember } from '@/api'
import type { Agent, Member, MemberRole } from '@/api/types'
import { Button, ConfirmDialog, Dialog, Field, FormError, Input, Select, useToast } from '@/components/ui'
import { MEMBER_ROLES, ROLE_HELP, roleLabel } from '@/lib/people'
import { ManagerSelect } from './InviteDialog'

export function EditMemberDialog({ companyId, member, agents, members, isSelf, canInviteOwner, onClose }: { companyId: string; member: Member; agents: Agent[]; members: Member[]; isSelf: boolean; canInviteOwner: boolean; onClose: () => void }) {
  const update = useUpdateMember(companyId)
  const toast = useToast()
  const [role, setRole] = useState<MemberRole>(member.role)
  const [title, setTitle] = useState(member.title ?? '')
  const [manager, setManager] = useState(member.manager_ref ?? '')
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    const patch = {
      ...(role !== member.role ? { role } : null),
      ...((title.trim() || null) !== member.title ? { title: title.trim() || null } : null),
      ...((manager || null) !== member.manager_ref ? { manager_ref: manager || null } : null),
    }
    if (Object.keys(patch).length === 0) {
      onClose()
      return
    }
    try {
      await update.mutateAsync({ id: member.id, ...patch })
      toast.success(`${member.display_name} updated`)
      onClose()
    } catch (err) {
      setError(errorMessage(err, 'Could not update this person'))
    }
  }
  const roles = MEMBER_ROLES.filter((r) => r !== 'owner' || canInviteOwner || member.role === 'owner')
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !update.isPending && onClose()}
      size="sm"
      title={`Edit ${member.display_name}`}
      description={`@${member.username}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={update.isPending}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="edit-member-form" loading={update.isPending}>
            Save
          </Button>
        </>
      }
    >
      <form id="edit-member-form" onSubmit={submit} noValidate className="space-y-4">
        <Field label="Role" hint={isSelf ? 'You cannot change your own role.' : ROLE_HELP[role]}>
          {({ id, describedBy }) => (
            <Select id={id} aria-describedby={describedBy} value={role} disabled={isSelf} onChange={(e) => setRole(e.target.value as MemberRole)}>
              {roles.map((r) => (
                <option key={r} value={r}>
                  {roleLabel(r)}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Title">{({ id }) => <Input id={id} maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} />}</Field>
        <Field label="Reports to">{({ id }) => <ManagerSelect id={id} value={manager} agents={agents} members={members} excludeMemberId={member.id} onChange={setManager} />}</Field>
        <FormError message={error} />
      </form>
    </Dialog>
  )
}

export function RemoveMemberDialog({ companyId, member, onClose }: { companyId: string; member: Member; onClose: () => void }) {
  const remove = useRemoveMember(companyId)
  const toast = useToast()
  return (
    <ConfirmDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Remove ${member.display_name}?`}
      description={
        <>
          They lose access to this company. Their open tasks are reassigned and their history stays in the audit log. Their account is not deleted, and they can be invited again.
        </>
      }
      confirmLabel="Remove from company"
      loading={remove.isPending}
      error={remove.isError ? errorMessage(remove.error) : null}
      onConfirm={() =>
        remove.mutate(member.id, {
          onSuccess: () => {
            toast.success(`${member.display_name} was removed`)
            onClose()
          },
        })
      }
    />
  )
}
