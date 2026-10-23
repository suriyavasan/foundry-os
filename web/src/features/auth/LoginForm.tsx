import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useLogin } from '@/api'
import type { AuthSession } from '@/api/types'
import { Button, Field, FormError, Input } from '@/components/ui'
import { describeLoginFailure, formatWait, isTotpCode, loginFailureText, loginNeedsSecondFactor, normalizeTotp, type LoginFailure } from '@/lib/auth'
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
}: {
  onSuccess: (session: AuthSession) => void | Promise<void>
  submitLabel?: string
  initialUsername?: string
}) {
  const login = useLogin()
  const [username, setUsername] = useState(initialUsername)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [secondFactor, setSecondFactor] = useState(false)
  const [fieldErrors, setFieldErrors] = useState<{ username?: string; password?: string; code?: string }>({})
  const [failure, setFailure] = useState<LoginFailure | null>(null)
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

  const message = failure ? loginFailureText(failure, wait) : null

  return (
    <form onSubmit={submit} noValidate className="space-y-4" aria-label={secondFactor ? 'Two-step verification' : 'Sign in'}>
      {secondFactor ? (
        <>
          <p className="text-[13px] text-fg-2">Open your authenticator app and enter the 6-digit code for Company OS.</p>
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
          <Field label="Username" required error={fieldErrors.username}>
            {({ id, invalid, describedBy }) => (
              <Input
                id={id}
                autoFocus={!initialUsername}
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                value={username}
                invalid={invalid}
                aria-describedby={describedBy}
                onChange={(e) => setUsername(e.target.value)}
              />
            )}
          </Field>
          <Field label="Password" required error={fieldErrors.password}>
            {({ id, invalid, describedBy }) => (
              <PasswordInput id={id} ref={passwordRef} autoFocus={!!initialUsername} autoComplete="current-password" value={password} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setPassword(e.target.value)} />
            )}
          </Field>
        </>
      )}

      <FormError message={message} />

      <div className="flex items-center gap-2">
        <Button type="submit" variant="primary" className="flex-1" loading={login.isPending} disabled={blocked}>
          {blocked ? `Try again in ${formatWait(wait)}` : secondFactor ? 'Verify' : submitLabel}
        </Button>
        {secondFactor && (
          <Button variant="ghost" onClick={backToCredentials} disabled={login.isPending}>
            Back
          </Button>
        )}
      </div>
    </form>
  )
}
