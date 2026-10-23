import clsx from 'clsx'
import { useMemo, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { errorMessage, seedCompanyIdea, useAuthSession, useCreateCompany, useSetup } from '@/api'
import { Button, Field, FormError, Input, Select, Textarea, useToast } from '@/components/ui'
import { LocalModelDetector } from '@/features/integrations/LocalModelDetector'
import {
  DISPLAY_NAME_MAX,
  describeSetupFailure,
  suggestUsername,
  validateDisplayName,
  validateEmail,
  validateUsername,
  type SetupFailure,
} from '@/lib/auth'
import { validateNewPassword } from '@/lib/password'
import { setLastCompanyId } from '@/lib/storage'
import { allTimezones, browserTimezone } from '@/lib/timezones'
import { AuthShell } from './AuthShell'
import { NewPasswordFields } from './PasswordFields'

type Step = 'account' | 'company' | 'model'
const STEPS: Array<{ id: Step; label: string }> = [
  { id: 'account', label: 'Account' },
  { id: 'company', label: 'Company' },
  { id: 'model', label: 'Local model' },
]

export const IDEA_MAX = 280
export const SETUP_TOKEN_FILE = 'data/owner.token'

function Progress({ step }: { step: Step }) {
  const at = STEPS.findIndex((s) => s.id === step)
  return (
    <div className="mb-5">
      <ol aria-label="Setup progress" className="flex gap-1.5">
        {STEPS.map((s, i) => (
          <li key={s.id} aria-current={i === at ? 'step' : undefined} className={clsx('h-1 flex-1 rounded-full', i <= at ? 'bg-accent' : 'bg-active')}>
            <span className="sr-only">
              {s.label}
              {i < at ? ' (done)' : i === at ? ' (current)' : ''}
            </span>
          </li>
        ))}
      </ol>
      <p className="mt-2 text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">
        Step {at + 1} of {STEPS.length} · {STEPS[at]!.label}
      </p>
    </div>
  )
}

/**
 * First run (docs/specs/m7 §1): a username and a password, a company with a one-line idea, an optional local
 * model, and then the canvas. Everything else is the AI Co-founder's job.
 */
export function SetupWizard({ setupTokenRequired, onDone }: { setupTokenRequired: boolean; onDone: () => void }) {
  const [step, setStep] = useState<Step>('account')
  const [companyId, setCompanyId] = useState<string | null>(null)
  const navigate = useNavigate()

  function finish() {
    if (companyId) {
      setLastCompanyId(companyId)
      navigate(`/c/${companyId}`, { replace: true })
    }
    onDone()
  }

  return (
    <AuthShell wide>
      <Progress step={step} />
      {step === 'account' && <AccountStep tokenRequired={setupTokenRequired} onDone={() => setStep('company')} />}
      {step === 'company' && (
        <CompanyStep
          onDone={(id) => {
            setCompanyId(id)
            setStep('model')
          }}
        />
      )}
      {step === 'model' && companyId && <ModelStep companyId={companyId} onFinish={finish} />}
    </AuthShell>
  )
}

/* ---------------------------------- step 1 ---------------------------------- */

function AccountStep({ tokenRequired, onDone }: { tokenRequired: boolean; onDone: () => void }) {
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
      <div>
        <h1 className="text-[20px] leading-7 font-semibold text-fg">Create your account</h1>
        <p className="mt-1 text-[13px] text-fg-3">A username and a password. Everything else is handled for you by your AI Co-founder.</p>
      </div>

      <Field label="Your name" required error={errors.display_name}>
        {({ id, invalid, describedBy }) => (
          <Input id={id} autoFocus autoComplete="name" maxLength={DISPLAY_NAME_MAX + 20} value={displayName} invalid={invalid} aria-describedby={describedBy} onChange={(e) => onDisplayName(e.target.value)} />
        )}
      </Field>
      <Field label="Username" required error={errors.username} hint="3 to 32 characters: lowercase letters, digits, dots, dashes, underscores.">
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
        {({ id, invalid, describedBy }) => (
          <Input id={id} type="email" autoComplete="email" value={email} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setEmail(e.target.value)} />
        )}
      </Field>

      {needsToken && (
        <div className="space-y-2 rounded-md border border-line bg-raised p-3">
          <Field
            label="Setup token"
            required
            error={errors.setup_token}
            hint={
              <>
                You are not on the computer that runs Company OS, so the server asks you to prove access. Copy the token printed in the terminal where the backend started, or from the file{' '}
                <code className="rounded-sm bg-canvas px-1 py-0.5 font-mono text-[11.5px] text-fg">{SETUP_TOKEN_FILE}</code> on that computer.
              </>
            }
          >
            {({ id, invalid, describedBy }) => (
              <Input id={id} autoComplete="off" autoCapitalize="none" spellCheck={false} value={token} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setToken(e.target.value)} className="font-mono" />
            )}
          </Field>
        </div>
      )}

      <FormError message={formError} />

      {alreadySetUp ? (
        <Button variant="primary" className="w-full" onClick={() => void session.refetch()} loading={session.isFetching}>
          Go to sign in
        </Button>
      ) : (
        <Button type="submit" variant="primary" className="w-full" loading={setup.isPending}>
          Create account
        </Button>
      )}
    </form>
  )
}

/* ---------------------------------- step 2 ---------------------------------- */

function CompanyStep({ onDone }: { onDone: (companyId: string) => void }) {
  const create = useCreateCompany()
  const toast = useToast()
  const zones = useMemo(() => allTimezones(), [])
  const [name, setName] = useState('')
  const [idea, setIdea] = useState('')
  const [timezone, setTimezone] = useState(browserTimezone)
  const [errors, setErrors] = useState<{ name?: string; idea?: string }>({})
  const [formError, setFormError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const n = name.trim()
    const next: typeof errors = {}
    if (!n) next.name = 'Give your company a name'
    else if (n.length > 120) next.name = 'Use at most 120 characters'
    if (idea.trim().length > IDEA_MAX) next.idea = `Keep it to one line (${IDEA_MAX} characters)`
    setErrors(next)
    if (Object.keys(next).length) return
    try {
      const company = await create.mutateAsync({ name: n, timezone, founder: { name: 'Atlas' } })
      const where = await seedCompanyIdea(company.id, idea)
      if (where === 'failed') toast.error('The company was created, but your idea could not be saved. Add it as a note from the canvas.')
      onDone(company.id)
    } catch (err) {
      setFormError(errorMessage(err, 'Could not create the company'))
    }
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4" aria-label="Create your company">
      <div>
        <h1 className="text-[20px] leading-7 font-semibold text-fg">Name your company</h1>
        <p className="mt-1 text-[13px] text-fg-3">One line is enough. Your AI Co-founder reads it as your first note and turns it into goals, a team and a plan.</p>
      </div>
      <Field label="Company name" required error={errors.name}>
        {({ id, invalid, describedBy }) => <Input id={id} autoFocus value={name} invalid={invalid} aria-describedby={describedBy} placeholder="Acme Labs" onChange={(e) => setName(e.target.value)} />}
      </Field>
      <Field label="What is it going to do?" error={errors.idea} hint="Your one-line idea. Optional, and you can write more any time.">
        {({ id, invalid, describedBy }) => (
          <Textarea id={id} rows={2} value={idea} invalid={invalid} aria-describedby={describedBy} placeholder="A newsletter that explains one research paper a day" onChange={(e) => setIdea(e.target.value)} />
        )}
      </Field>
      <Field label="Timezone" hint="Used for schedules and reporting periods.">
        {({ id }) => (
          <Select id={id} value={timezone} onChange={(e) => setTimezone(e.target.value)}>
            {zones.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </Select>
        )}
      </Field>
      <FormError message={formError} />
      <Button type="submit" variant="primary" className="w-full" loading={create.isPending}>
        Create company
      </Button>
    </form>
  )
}

/* ---------------------------------- step 3 ---------------------------------- */

function ModelStep({ companyId, onFinish }: { companyId: string; onFinish: () => void }) {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-[20px] leading-7 font-semibold text-fg">Connect a local model</h1>
        <p className="mt-1 text-[13px] text-fg-3">
          Your agents need a language model to think with. A local model runs on this computer, is free and keeps everything private. If you use Ollama, we can find it.
        </p>
      </div>
      <LocalModelDetector companyId={companyId} />
      <p className="text-[12px] text-fg-3">Nothing is detected or registered unless you press the button. You can add cloud providers later in Integrations.</p>
      <div className="flex items-center justify-end gap-2 border-t border-line pt-4">
        <Button variant="ghost" onClick={onFinish}>
          Skip for now
        </Button>
        <Button variant="primary" onClick={onFinish}>
          Open my company
        </Button>
      </div>
    </div>
  )
}
