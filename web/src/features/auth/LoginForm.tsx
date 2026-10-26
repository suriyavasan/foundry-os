import { Info, KeyRound } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { qk, useLogin, usePasskeyLogin } from '@/api'
import type { AuthSession } from '@/api/types'
import { Button, Field, FormError, Input } from '@/components/ui'
import { describeLoginFailure, formatWait, isTotpCode, loginNeedsSecondFactor, normalizeTotp } from '@/lib/auth'
import { conditionalMediationAvailable, describePasskeyFailure, passkeyEnvironment, passkeyEnvironmentMessage, passkeyFailureText, type PasskeyFailure } from '@/lib/webauthn'
import { PasswordInput } from './PasswordFields'

/**
 * Username + password, then (only when the account has two-step verification) the authenticator code.
 * Failures are generic on purpose: the form never says whether the username exists. Rate limits and lockouts
 * disable the button and count down when the server says how long.
 */
export function LoginForm({
  onSuccess,
  submitLabel = 'Sign in',
  initialUsername = '',
  passkeys = false,
}: {
  onSuccess: (session: AuthSession) => void | Promise<void>
  submitLabel?: string
  initialUsername?: string
  /** Offer "Sign in with a passkey" (and passkey autofill) where the backend and browser support it. */
  passkeys?: boolean
}) {
  const login = useLogin()
  const passkeyLogin = usePasskeyLogin()
  const autofillLogin = usePasskeyLogin()
  // Read, not subscribed: the gate has already loaded the session to show this form, and a second observer would refetch it.
  const sessionData = useQueryClient().getQueryData<AuthSession>(qk.authSession)
  const [username, setUsername] = useState(initialUsername)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [secondFactor, setSecondFactor] = useState(false)
  const [fieldErrors, setFieldErrors] = useState<{ username?: string; password?: string; code?: string }>({})
  const [failure, setFailure] = useState<PasskeyFailure | null>(null)
  const [failures, setFailures] = useState(0)
  const [wait, setWait] = useState(0)
  const passwordRef = useRef<HTMLInputElement>(null)
  const codeRef = useRef<HTMLInputElement>(null)

  // Count a server-given wait down once a second.
  useEffect(() => {
    if (wait <= 0) return
    const t = setTimeout(() => setWait((w) => w - 1), 1000)
    return () => clearTimeout(t)
  }, [wait])

  // After a refusal, put the cursor where the person has to type next.
  useEffect(() => {
    if (failures === 0) return
    ;(secondFactor ? codeRef.current : passwordRef.current)?.focus()
  }, [failures, secondFactor])

  const blocked = wait > 0

  // Passkeys: the backend must speak them (a 404 later hides them again), the origin must allow them, the browser must have them.
  const [passkeysMissing, setPasskeysMissing] = useState(false)
  const [autofill, setAutofill] = useState(false)
  const [rearm, setRearm] = useState(0)
  const autofillControl = useRef<AbortController | null>(null)
  const env = passkeyEnvironment(sessionData)
  const passkeyUi = passkeys && !passkeysMissing && !secondFactor
  const showPasskey = passkeyUi && env === 'ready'
  const envMessage = passkeyUi ? passkeyEnvironmentMessage(env) : null

  useEffect(() => {
    if (!showPasskey) return
    let live = true
    void conditionalMediationAvailable().then((ok) => {
      if (live) setAutofill(ok)
    })
    return () => {
      live = false
    }
  }, [showPasskey])

  // Conditional UI: the browser offers saved passkeys inside the username field; picking one signs in with no clicks.
  const { mutateAsync: autofillSignIn } = autofillLogin
  useEffect(() => {
    if (!showPasskey || !autofill) return
    const control = new AbortController()
    autofillControl.current = control
    autofillSignIn({ conditional: true, signal: control.signal })
      .then(async (session) => {
        if (session.state === 'authenticated') await onSuccess(session)
      })
      .catch((err) => {
        if (control.signal.aborted) return
        const f = describePasskeyFailure(err)
        if (f.kind === 'missing') setPasskeysMissing(true)
        else if (f.kind === 'rate_limited' || f.kind === 'locked' || f.kind === 'network') {
          setFailure(f)
          setWait(f.retryAfterSeconds ? Math.ceil(f.retryAfterSeconds) : 0)
        }
      })
    return () => control.abort()
    // onSuccess is called, not depended on: re-arming the autofill request must not follow a parent re-render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showPasskey, autofill, rearm, autofillSignIn])

  async function signInWithPasskey() {
    if (blocked) return
    autofillControl.current?.abort()
    setFailure(null)
    setFieldErrors({})
    try {
      const session = await passkeyLogin.mutateAsync({ ...(username.trim() ? { username: username.trim() } : null) })
      if (session.state === 'authenticated') {
        setPassword('')
        await onSuccess(session)
      }
    } catch (err) {
      const f = describePasskeyFailure(err)
      if (f.kind === 'missing') {
        setPasskeysMissing(true)
        return
      }
      setFailure(f)
      setWait(f.retryAfterSeconds ? Math.ceil(f.retryAfterSeconds) : 0)
      if (f.kind !== 'rate_limited' && f.kind !== 'locked') setRearm((n) => n + 1)
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (blocked) return
    const errors: typeof fieldErrors = {}
    if (!secondFactor) {
      if (!username.trim()) errors.username = 'Enter your username'
      if (!password) errors.password = 'Enter your password'
    } else if (!isTotpCode(code)) {
      errors.code = 'Enter the 6-digit code from your authenticator app'
    }
    setFieldErrors(errors)
    if (Object.keys(errors).length) return
    setFailure(null)
    try {
      const session = await login.mutateAsync({ username: username.trim(), password, ...(secondFactor ? { totp_code: normalizeTotp(code) } : null) })
      if (loginNeedsSecondFactor(session)) {
        setSecondFactor(true)
        return
      }
      setPassword('')
      setCode('')
      await onSuccess(session)
    } catch (err) {
      const f = describeLoginFailure(err, { secondFactor })
      if (f.kind === 'two_factor_required') {
        setSecondFactor(true)
        return
      }
      setFailure(f)
      setWait(f.retryAfterSeconds ? Math.ceil(f.retryAfterSeconds) : 0)
      if (secondFactor) setCode('')
      else if (f.kind !== 'network') setPassword('')
      setFailures((n) => n + 1)
    }
  }

  function backToCredentials() {
    setSecondFactor(false)
    setCode('')
    setFailure(null)
    setFieldErrors({})
  }

  const message = failure ? passkeyFailureText(failure, wait) : null

  return (
    <form onSubmit={submit} noValidate className="space-y-4" aria-label={secondFactor ? 'Two-step verification' : 'Sign in'}>
      {secondFactor ? (
        <>
          <p className="text-[13px] text-fg-2">Open your authenticator app and enter the 6-digit code for Foundry AI OS.</p>
          <Field label="Authentication code" required error={fieldErrors.code}>
            {({ id, invalid, describedBy }) => (
              <Input
                id={id}
                ref={codeRef}
                autoFocus
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={7}
                placeholder="123456"
                value={code}
                invalid={invalid}
                aria-describedby={describedBy}
                onChange={(e) => setCode(e.target.value)}
                className="tracking-[0.3em]"
              />
            )}
          </Field>
        </>
      ) : (
        <>
          <Field label="Username" required error={fieldErrors.username} hint={showPasskey ? 'Optional if you sign in with a passkey.' : undefined}>
            {({ id, invalid, describedBy }) => (
              <Input
                id={id}
                autoFocus={!initialUsername}
                autoComplete={showPasskey && autofill ? 'username webauthn' : 'username'}
                autoCapitalize="none"
                spellCheck={false}
                value={username}
                invalid={invalid}
                aria-describedby={describedBy}
                onChange={(e) => setUsername(e.target.value)}
              />
            )}
          </Field>
          {showPasskey && (
            <>
              <Button
                variant="primary"
                className="w-full"
                icon={<KeyRound className="size-3.5" />}
                loading={passkeyLogin.isPending}
                disabled={blocked || login.isPending}
                onClick={() => void signInWithPasskey()}
              >
                {blocked ? `Try again in ${formatWait(wait)}` : 'Sign in with a passkey'}
              </Button>
              {passkeyLogin.isPending && (
                <p role="status" className="text-[12px] text-fg-3">
                  Follow the prompt from your browser or device.
                </p>
              )}
              <div className="flex items-center gap-3 text-[11.5px] text-fg-3" aria-hidden>
                <span className="h-px flex-1 bg-line" />
                or use your password
                <span className="h-px flex-1 bg-line" />
              </div>
            </>
          )}
          <Field label="Password" required error={fieldErrors.password}>
            {({ id, invalid, describedBy }) => (
              <PasswordInput id={id} ref={passwordRef} autoFocus={!!initialUsername} autoComplete="current-password" value={password} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setPassword(e.target.value)} />
            )}
          </Field>
        </>
      )}

      <FormError message={message} />

      <div className="flex items-center gap-2">
        <Button type="submit" variant={showPasskey ? 'secondary' : 'primary'} className="flex-1" loading={login.isPending} disabled={blocked}>
          {blocked ? `Try again in ${formatWait(wait)}` : secondFactor ? 'Verify' : submitLabel}
        </Button>
        {secondFactor && (
          <Button variant="ghost" onClick={backToCredentials} disabled={login.isPending}>
            Back
          </Button>
        )}
      </div>
      {envMessage && (
        <p role="note" className="flex gap-2 rounded-md border border-line bg-raised p-2.5 text-[12px] text-fg-2">
          <Info className="mt-0.5 size-3.5 shrink-0 text-fg-3" aria-hidden />
          <span>{envMessage}</span>
        </p>
      )}
    </form>
  )
}
