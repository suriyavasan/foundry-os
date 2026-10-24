import { useState, type FormEvent } from 'react'
import { useAuthSession, useSetup } from '@/api'
import { Button, Field, FormError, Input } from '@/components/ui'
import { NewPasswordFields } from '@/features/auth/PasswordFields'
import { DISPLAY_NAME_MAX, describeSetupFailure, suggestUsername, validateDisplayName, validateEmail, validateUsername, type SetupFailure } from '@/lib/auth'
import { validateNewPassword } from '@/lib/password'
import { StepHeading } from '../StepFrame'

export const SETUP_TOKEN_FILE = 'data/owner.token'

/**
 * Step 2: the owner account. Same rules as the first-run wizard of M7: username suggested from the name, a password checked
 * against the person's own details, the setup token only when the server asks for it (not on the machine that runs the
 * server), and a clear way forward when someone else finished setup first.
 */
export function AccountStep({ tokenRequired, onDone }: { tokenRequired: boolean; onDone: () => void }) {
  const setup = useSetup()
  const session = useAuthSession()
  const [displayName, setDisplayName] = useState('')
  const [username, setUsername] = useState('')
  const [usernameTouched, setUsernameTouched] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [token, setToken] = useState('')
  const [needsToken, setNeedsToken] = useState(tokenRequired)
  const [errors, setErrors] = useState<Record<string, string | undefined>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [alreadySetUp, setAlreadySetUp] = useState(false)

  function onDisplayName(v: string) {
    setDisplayName(v)
    if (!usernameTouched) setUsername(suggestUsername(v))
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const pw = validateNewPassword(password, confirm, { username, displayName, email })
    const next: Record<string, string | undefined> = {
      display_name: validateDisplayName(displayName) ?? undefined,
      username: validateUsername(username) ?? undefined,
      email: validateEmail(email) ?? undefined,
      password: pw.password,
      confirm: pw.confirm,
      setup_token: needsToken && !token.trim() ? 'Enter the setup token' : undefined,
    }
    setErrors(next)
    if (Object.values(next).some(Boolean)) return
    try {
      await setup.mutateAsync({
        username,
        display_name: displayName.trim(),
        password,
        ...(email.trim() ? { email: email.trim() } : null),
        ...(needsToken ? { setup_token: token.trim() } : null),
      })
      setPassword('')
      setConfirm('')
      setToken('')
      onDone()
    } catch (err) {
      const f: SetupFailure = describeSetupFailure(err)
      setErrors({ ...f.fields })
      setFormError(f.form)
      if (f.needsToken) setNeedsToken(true)
      if (f.alreadySetUp) setAlreadySetUp(true)
    }
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4" aria-label="Create your account">
      <StepHeading eyebrow="Your account" title="Create your account">
        A username and a password. Everything else is handled for you by your AI Co-founder.
      </StepHeading>

      <Field label="Your name" required error={errors.display_name}>
        {({ id, invalid, describedBy }) => (
          <Input id={id} autoFocus autoComplete="name" className="h-10" maxLength={DISPLAY_NAME_MAX + 20} value={displayName} invalid={invalid} aria-describedby={describedBy} onChange={(e) => onDisplayName(e.target.value)} />
        )}
      </Field>
      <Field label="Username" required error={errors.username} hint="3 to 32 characters: lowercase letters, digits, dots, dashes, underscores.">
        {({ id, invalid, describedBy }) => (
          <Input
            id={id}
            className="h-10"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            value={username}
            invalid={invalid}
            aria-describedby={describedBy}
            onChange={(e) => {
              setUsernameTouched(true)
              setUsername(e.target.value)
            }}
          />
        )}
      </Field>

      <NewPasswordFields
        password={password}
        confirm={confirm}
        onPassword={setPassword}
        onConfirm={setConfirm}
        context={{ username, displayName, email }}
        errors={{ password: errors.password, confirm: errors.confirm }}
      />

      <Field label="Email" error={errors.email} hint="Optional. Used for invitations and account recovery.">
        {({ id, invalid, describedBy }) => <Input id={id} type="email" className="h-10" autoComplete="email" value={email} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setEmail(e.target.value)} />}
      </Field>

      {needsToken && (
        <div className="space-y-2 rounded-md border border-line bg-raised p-3">
          <Field
            label="Setup token"
            required
            error={errors.setup_token}
            hint={
              <>
                You are not on the computer that runs Foundry AI OS, so the server asks you to prove access. Copy the token printed in the terminal where the backend started, or from the file{' '}
                <code className="rounded-sm bg-canvas px-1 py-0.5 font-mono text-[11.5px] text-fg">{SETUP_TOKEN_FILE}</code> on that computer.
              </>
            }
          >
            {({ id, invalid, describedBy }) => (
              <Input id={id} autoComplete="off" autoCapitalize="none" spellCheck={false} value={token} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setToken(e.target.value)} className="h-10 font-mono" />
            )}
          </Field>
        </div>
      )}

      <FormError message={formError} />

      {alreadySetUp ? (
        <Button variant="primary" className="h-10 w-full" onClick={() => void session.refetch()} loading={session.isFetching}>
          Go to sign in
        </Button>
      ) : (
        <Button type="submit" variant="primary" className="h-10 w-full" loading={setup.isPending}>
          Create account
        </Button>
      )}
    </form>
  )
}
