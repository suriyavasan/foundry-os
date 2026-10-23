import { Check, Copy, Mail } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { ApiError, errorMessage, useCreateInvitation } from '@/api'
import type { Agent, InvitationCreated, Member, MemberRole } from '@/api/types'
import { Button, Dialog, Field, FormError, Input, Select, useToast } from '@/components/ui'
import { validateEmail } from '@/lib/auth'
import { INVITE_VALID_DAYS, inviteOutcome } from '@/lib/invite'
import { MEMBER_ROLES, ROLE_HELP, managerOptions, roleLabel } from '@/lib/people'

export function ManagerSelect({ id, value, agents, members, excludeMemberId, onChange }: { id?: string; value: string; agents: Agent[]; members: Member[]; excludeMemberId?: string; onChange: (v: string) => void }) {
  const options = managerOptions(agents, members, { memberId: excludeMemberId })
  const a = options.filter((o) => o.group === 'agents')
  const m = options.filter((o) => o.group === 'members')
  return (
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">The Founder (you)</option>
      {a.length > 0 && (
        <optgroup label="AI agents">
          {a.map((o) => (
            <option key={o.ref} value={o.ref}>
              {o.label}
            </option>
          ))}
        </optgroup>
      )}
      {m.length > 0 && (
        <optgroup label="Team members">
          {m.map((o) => (
            <option key={o.ref} value={o.ref}>
              {o.label}
            </option>
          ))}
        </optgroup>
      )}
    </Select>
  )
}

/** Invite by email. The result says plainly whether an email went out or a link has to be shared by hand. */
export function InviteDialog({ companyId, agents, members, canInviteOwner, onClose }: { companyId: string; agents: Agent[]; members: Member[]; canInviteOwner: boolean; onClose: () => void }) {
  const create = useCreateInvitation(companyId)
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<MemberRole>('member')
  const [title, setTitle] = useState('')
  const [manager, setManager] = useState('')
  const [errors, setErrors] = useState<{ email?: string }>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [created, setCreated] = useState<InvitationCreated | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const emailError = validateEmail(email, { required: true })
    setErrors({ email: emailError ?? undefined })
    if (emailError) return
    try {
      setCreated(
        await create.mutateAsync({ email: email.trim(), role, ...(title.trim() ? { title: title.trim() } : null), ...(manager ? { manager_ref: manager } : null) }),
      )
    } catch (err) {
      if (err instanceof ApiError && err.status === 422 && err.fieldErrors.email) setErrors({ email: err.fieldErrors.email })
      else setFormError(errorMessage(err, 'Could not create the invitation'))
    }
  }

  if (created) return <InviteResult created={created} onClose={onClose} />

  const roles = MEMBER_ROLES.filter((r) => r !== 'owner' || canInviteOwner)
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      title="Invite a person"
      description={`They get a single-use link that works for ${INVITE_VALID_DAYS} days and is tied to this email address.`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={create.isPending}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="invite-form" icon={<Mail className="size-3.5" />} loading={create.isPending}>
            Create invitation
          </Button>
        </>
      }
    >
      <form id="invite-form" onSubmit={submit} noValidate className="space-y-4">
        <Field label="Email" required error={errors.email}>
          {({ id, invalid, describedBy }) => <Input id={id} type="email" autoFocus autoComplete="off" value={email} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setEmail(e.target.value)} />}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Role" hint={ROLE_HELP[role]}>
            {({ id, describedBy }) => (
              <Select id={id} aria-describedby={describedBy} value={role} onChange={(e) => setRole(e.target.value as MemberRole)}>
                {roles.map((r) => (
                  <option key={r} value={r}>
                    {roleLabel(r)}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Title" hint="Optional, e.g. Head of Design.">
            {({ id }) => <Input id={id} maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} />}
          </Field>
        </div>
        <Field label="Reports to" hint="Where they appear in the organization chart.">
          {({ id }) => <ManagerSelect id={id} value={manager} agents={agents} members={members} onChange={setManager} />}
        </Field>
        <FormError message={formError} />
      </form>
    </Dialog>
  )
}

function InviteResult({ created, onClose }: { created: InvitationCreated; onClose: () => void }) {
  const outcome = inviteOutcome(created, window.location.origin)
  const [copied, setCopied] = useState(false)
  const toast = useToast()
  async function copy(link: string) {
    try {
      await navigator.clipboard.writeText(link)
      setCopied(true)
    } catch {
      toast.error('Could not copy. Select the link and copy it by hand.')
    }
  }
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={outcome.kind === 'email' ? 'Invitation sent' : 'Invitation created'} footer={<Button variant="primary" onClick={onClose}>Done</Button>}>
      <div className="space-y-3" data-testid="invite-result" data-delivery={created.delivery}>
        <p className="text-[13px] text-fg">{outcome.text}</p>
        {outcome.kind === 'link' && (
          <>
            <div className="flex items-center gap-2">
              <input readOnly aria-label="Invitation link" value={outcome.link} onFocus={(e) => e.currentTarget.select()} className="h-8 min-w-0 flex-1 rounded-md border border-line-strong bg-canvas px-2.5 font-mono text-[12px] text-fg" />
              <Button size="sm" icon={copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />} onClick={() => void copy(outcome.link)}>
                {copied ? 'Copied' : 'Copy link'}
              </Button>
            </div>
            <p className="text-[12px] text-fg-3">
              It works once, for {created.email} only, and expires in {INVITE_VALID_DAYS} days. It is shown only now; if you lose it, revoke the invitation and create a new one.
            </p>
          </>
        )}
        {outcome.kind === 'email' && <p className="text-[12px] text-fg-3">The link inside works once and expires in {INVITE_VALID_DAYS} days. You can revoke it below at any time.</p>}
      </div>
    </Dialog>
  )
}
