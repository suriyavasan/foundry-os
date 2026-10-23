import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { useAcceptInvitation, useAuthSession, useLogout } from '@/api'
import type { InvitationAccept } from '@/api/types'
import { Button, ErrorState, Field, FormError, Input, LoadingState, NotAvailable, Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui'
import { DISPLAY_NAME_MAX, authStatusOf, suggestUsername, validateDisplayName, validateUsername } from '@/lib/auth'
import { describeInviteFailure, looksLikeInviteToken, type InviteFailure } from '@/lib/invite'
import { validateNewPassword } from '@/lib/password'
import { setLastCompanyId } from '@/lib/storage'
import { AuthShell } from './AuthShell'
import { LoginForm } from './LoginForm'
import { NewPasswordFields } from './PasswordFields'

const INVALID_LINK = 'This invitation link is not valid. Ask the person who invited you for a new one.'

/**
 * `/invite/:token`, outside the auth gate. Signed in: confirm and join. Otherwise: create an account (username and
 * password) or sign in to an existing one, then join. The token travels only in the URL path of the accept call.
 */
export function InvitePage() {
  const { token } = useParams()
  const session = useAuthSession()
  const status = authStatusOf({ isPending: session.isPending, data: session.data, error: session.error })

  if (!looksLikeInviteToken(token)) {
    return (
      <AuthShell>
        <h1 className="text-[18px] font-semibold text-fg">Invitation not valid</h1>
        <p className="mt-2 text-[13px] text-fg-2">{INVALID_LINK}</p>
      </AuthShell>
    )
  }
  return (
    <AuthShell wide>
      {status.kind === 'loading' ? (
        <LoadingState label="Checking your invitation" rows={3} />
      ) : status.kind === 'error' ? (
        <ErrorState error={status.error} onRetry={() => void session.refetch()} retrying={session.isFetching} />
      ) : status.kind === 'legacy' ? (
        <NotAvailable milestone="M7" title="Invitations are not built yet" description="This backend has no accounts, so it cannot accept invitations." />
      ) : (
        <AcceptInvite token={token} signedInAs={status.kind === 'authenticated' ? status.session.user : null} setupRequired={status.kind === 'setup_required'} />
      )}
    </AuthShell>
  )
}

function AcceptInvite({ token, signedInAs, setupRequired }: { token: string; signedInAs: { display_name: string; username: string } | null; setupRequired: boolean }) {
  const accept = useAcceptInvitation(token)
  const navigate = useNavigate()
  const logout = useLogout()
  const [failure, setFailure] = useState<InviteFailure | null>(null)

  async function join(body: InvitationAccept) {
    setFailure(null)
    try {
      const joined = await accept.mutateAsync(body)
      if (joined.company_id) {
        setLastCompanyId(joined.company_id)
        navigate(`/c/${joined.company_id}`, { replace: true })
      } else {
        navigate('/', { replace: true })
      }
      return true
    } catch (err) {
      setFailure(describeInviteFailure(err))
      return false
    }
  }

  if (failure?.kind === 'invalid') {
    return (
      <div role="alert">
        <h1 className="text-[18px] font-semibold text-fg">Invitation not valid</h1>
        <p className="mt-2 text-[13px] text-fg-2">{failure.message}</p>
        <Link to="/" className="mt-4 inline-flex h-8 items-center rounded-md border border-line-strong bg-raised px-3 text-[13px] font-medium text-fg hover:bg-hover">
          Go to Company OS
        </Link>
      </div>
    )
  }

  if (setupRequired) {
    return (
      <div>
        <h1 className="text-[18px] font-semibold text-fg">This install is not set up yet</h1>
        <p className="mt-2 text-[13px] text-fg-2">Someone has to create the owner account first. Open Company OS from the computer that runs it, then use this link again.</p>
        <Link to="/" className="mt-4 inline-flex h-8 items-center rounded-md border border-line-strong bg-raised px-3 text-[13px] font-medium text-fg hover:bg-hover">
          Open setup
        </Link>
      </div>
    )
  }

  if (signedInAs) {
    return (
      <div className="space-y-4">
        <div>
          <h1 className="text-[20px] leading-7 font-semibold text-fg">Join the company</h1>
          <p className="mt-1 text-[13px] text-fg-3">
            You are signed in as <strong className="font-medium text-fg">{signedInAs.display_name}</strong> (@{signedInAs.username}). Accepting adds this account to the company that invited you.
          </p>
        </div>
        <FormError message={failure?.message} />
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" loading={accept.isPending} onClick={() => void join({})}>
            Accept invitation
          </Button>
          <Button variant="ghost" loading={logout.isPending} onClick={() => logout.mutate()}>
            Use a different account
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div>
      <h1 className="text-[20px] leading-7 font-semibold text-fg">You have been invited</h1>
      <p className="mt-1 text-[13px] text-fg-3">Create your account to join, or sign in if you already have one on this install.</p>
      <Tabs defaultValue="create" className="mt-4">
        <TabsList aria-label="Account">
          <TabsTrigger value="create">Create account</TabsTrigger>
          <TabsTrigger value="signin">I have an account</TabsTrigger>
        </TabsList>
        <TabsContent value="create">
          <CreateAccountForm pending={accept.isPending} failure={failure} onSubmit={join} />
        </TabsContent>
        <TabsContent value="signin">
          <div className="space-y-3">
            <LoginForm submitLabel="Sign in and join" onSuccess={async () => void (await join({}))} />
            <FormError message={failure && failure.kind !== 'validation' ? failure.message : null} />
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}

function CreateAccountForm({ pending, failure, onSubmit }: { pending: boolean; failure: InviteFailure | null; onSubmit: (body: InvitationAccept) => Promise<boolean> }) {
  const [displayName, setDisplayName] = useState('')
  const [username, setUsername] = useState('')
  const [touched, setTouched] = useState(false)
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [errors, setErrors] = useState<Record<string, string | undefined>>({})

  async function submit(e: FormEvent) {
    e.preventDefault()
    const pw = validateNewPassword(password, confirm, { username, displayName })
    const next = {
      display_name: validateDisplayName(displayName) ?? undefined,
      username: validateUsername(username) ?? undefined,
      password: pw.password,
      confirm: pw.confirm,
    }
    setErrors(next)
    if (Object.values(next).some(Boolean)) return
    const ok = await onSubmit({ username, display_name: displayName.trim(), password })
    if (ok) {
      setPassword('')
      setConfirm('')
    }
  }
  const server = failure?.fields ?? {}
  return (
    <form onSubmit={submit} noValidate className="space-y-4" aria-label="Create your account">
      <Field label="Your name" required error={errors.display_name ?? server.display_name}>
        {({ id, invalid, describedBy }) => (
          <Input
            id={id}
            autoFocus
            autoComplete="name"
            maxLength={DISPLAY_NAME_MAX + 20}
            value={displayName}
            invalid={invalid}
            aria-describedby={describedBy}
            onChange={(e) => {
              setDisplayName(e.target.value)
              if (!touched) setUsername(suggestUsername(e.target.value))
            }}
          />
        )}
      </Field>
      <Field label="Username" required error={errors.username ?? server.username} hint="3 to 32 characters: lowercase letters, digits, dots, dashes, underscores.">
        {({ id, invalid, describedBy }) => (
          <Input
            id={id}
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            value={username}
            invalid={invalid}
            aria-describedby={describedBy}
            onChange={(e) => {
              setTouched(true)
              setUsername(e.target.value)
            }}
          />
        )}
      </Field>
      <NewPasswordFields password={password} confirm={confirm} onPassword={setPassword} onConfirm={setConfirm} context={{ username, displayName }} errors={{ password: errors.password ?? server.password, confirm: errors.confirm }} />
      <FormError message={failure && failure.kind !== 'validation' ? failure.message : failure?.kind === 'validation' ? failure.message || null : null} />
      <Button type="submit" variant="primary" className="w-full" loading={pending}>
        Create account and join
      </Button>
    </form>
  )
}
