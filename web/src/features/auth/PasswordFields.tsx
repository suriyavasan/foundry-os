import clsx from 'clsx'
import { Eye, EyeOff } from 'lucide-react'
import { useState, type ComponentProps } from 'react'
import { Field, Input } from '@/components/ui'
import { checkPassword, type NewPasswordErrors, type PasswordCheck, type PasswordContext } from '@/lib/password'

/** A password input with a show/hide toggle. Never autofilled with a generated value; the toggle has a real name. */
export function PasswordInput({ className, ...rest }: Omit<ComponentProps<typeof Input>, 'type'>) {
  const [shown, setShown] = useState(false)
  return (
    <div className="relative">
      <Input {...rest} type={shown ? 'text' : 'password'} className={clsx('pr-9', className)} spellCheck={false} autoCapitalize="none" autoCorrect="off" />
      <button
        type="button"
        onClick={() => setShown((s) => !s)}
        aria-label={shown ? 'Hide password' : 'Show password'}
        aria-pressed={shown}
        className="absolute inset-y-0 right-0 flex w-8 items-center justify-center rounded-r-md text-fg-3 hover:text-fg"
      >
        {shown ? <EyeOff className="size-3.5" aria-hidden /> : <Eye className="size-3.5" aria-hidden />}
      </button>
    </div>
  )
}

const SEGMENT_TONE = ['bg-danger', 'bg-danger', 'bg-warning', 'bg-info', 'bg-success'] as const
const LABEL_TONE = ['text-danger', 'text-danger', 'text-warning', 'text-info', 'text-success'] as const

/** Strength bar + the specific reasons a password is not acceptable. Announces changes politely. */
export function PasswordMeter({ check }: { check: PasswordCheck }) {
  if (check.length === 0) return null
  const filled = Math.max(1, check.score)
  return (
    <div className="mt-2" data-testid="password-meter">
      <div role="meter" aria-label="Password strength" aria-valuemin={0} aria-valuemax={4} aria-valuenow={check.score} aria-valuetext={check.label} className="flex gap-1">
        {[1, 2, 3, 4].map((i) => (
          <span key={i} className={clsx('h-1 flex-1 rounded-full', i <= filled ? SEGMENT_TONE[check.score] : 'bg-active')} />
        ))}
      </div>
      <div aria-live="polite" className="mt-1.5 space-y-0.5 text-[12px]">
        <p className="text-fg-3">
          Strength: <span className={clsx('font-medium', LABEL_TONE[check.score])}>{check.label}</span>
        </p>
        {check.feedback.map((f) => (
          <p key={f} className="text-warning">
            {f}
          </p>
        ))}
      </div>
    </div>
  )
}

/** Password + confirmation with the strength meter. The parent owns the values and decides when to show errors. */
export function NewPasswordFields({
  password,
  confirm,
  onPassword,
  onConfirm,
  context,
  errors = {},
  label = 'Password',
  autoFocus,
}: {
  password: string
  confirm: string
  onPassword: (v: string) => void
  onConfirm: (v: string) => void
  context?: PasswordContext
  errors?: NewPasswordErrors
  label?: string
  autoFocus?: boolean
}) {
  const check = checkPassword(password, context)
  return (
    <>
      <div>
        <Field label={label} required error={errors.password} hint="At least 12 characters. A few unrelated words works well.">
          {({ id, invalid, describedBy }) => (
            <PasswordInput id={id} autoFocus={autoFocus} value={password} invalid={invalid} aria-describedby={describedBy} autoComplete="new-password" onChange={(e) => onPassword(e.target.value)} />
          )}
        </Field>
        <PasswordMeter check={check} />
      </div>
      <Field label="Confirm password" required error={errors.confirm}>
        {({ id, invalid, describedBy }) => (
          <PasswordInput id={id} value={confirm} invalid={invalid} aria-describedby={describedBy} autoComplete="new-password" onChange={(e) => onConfirm(e.target.value)} />
        )}
      </Field>
    </>
  )
}
