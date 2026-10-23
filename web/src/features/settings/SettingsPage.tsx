import { AlertTriangle, Moon, Sun } from 'lucide-react'
import { useMemo, useState, type FormEvent } from 'react'
import { ApiError, errorMessage, useCompany, useHealth, useUpdateCompany } from '@/api'
import type { Company } from '@/api/types'
import { StringListEditor } from '@/components/forms/StringListEditor'
import { useTheme } from '@/components/layout/ThemeProvider'
import { Page } from '@/components/layout/Page'
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  ConfirmDialog,
  DefinitionList,
  Dot,
  ErrorState,
  Field,
  FormError,
  Input,
  LoadingState,
  Select,
  Textarea,
  useToast,
} from '@/components/ui'
import { useCompanyId } from '@/features/shell/useCompanyId'
import {
  LIMITS,
  mapServerFieldErrors,
  validateOnboarding,
  type OnboardingErrors,
} from '@/features/onboarding/validation'
import { allTimezones } from '@/lib/timezones'

export function SettingsPage() {
  const companyId = useCompanyId()
  const company = useCompany(companyId)

  return (
    <Page title="Settings" description="Company profile, appearance and install details.">
      {company.isPending ? (
        <Card>
          <LoadingState rows={6} label="Loading settings" />
        </Card>
      ) : company.isError ? (
        <Card>
          <ErrorState error={company.error} onRetry={() => company.refetch()} retrying={company.isFetching} />
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="space-y-4 lg:col-span-2">
            <ProfileForm key={company.data.updated_at} company={company.data} />
            <StatusCard company={company.data} />
          </div>
          <div className="space-y-4">
            <AppearanceCard />
            <AboutCard />
          </div>
        </div>
      )}
    </Page>
  )
}

function sameList(a: string[], b: string[]) {
  return a.length === b.length && a.every((x, i) => x === b[i])
}

function ProfileForm({ company }: { company: Company }) {
  const update = useUpdateCompany(company.id)
  const toast = useToast()
  const timezones = useMemo(() => {
    const z = allTimezones()
    return z.includes(company.timezone) ? z : [company.timezone, ...z]
  }, [company.timezone])

  const [name, setName] = useState(company.name)
  const [mission, setMission] = useState(company.mission ?? '')
  const [vision, setVision] = useState(company.vision ?? '')
  const [goal, setGoal] = useState(company.strategic_goal ?? '')
  const [constraints, setConstraints] = useState(company.constraints)
  const [timezone, setTimezone] = useState(company.timezone)
  const [errors, setErrors] = useState<OnboardingErrors>({})
  const [formError, setFormError] = useState<string | null>(null)

  const dirty =
    name !== company.name ||
    mission !== (company.mission ?? '') ||
    vision !== (company.vision ?? '') ||
    goal !== (company.strategic_goal ?? '') ||
    timezone !== company.timezone ||
    !sameList(constraints, company.constraints)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const ce = validateOnboarding({
      name,
      mission,
      vision,
      strategic_goal: goal,
      constraints,
      timezone,
      founder_name: 'x',
      founder_model_provider: '',
      founder_model_name: '',
    })
    setErrors(ce)
    if (Object.keys(ce).length) return

    const body: Record<string, unknown> = {}
    if (name.trim() !== company.name) body.name = name.trim()
    if (mission.trim() !== (company.mission ?? '')) body.mission = mission.trim() || null
    if (vision.trim() !== (company.vision ?? '')) body.vision = vision.trim() || null
    if (goal.trim() !== (company.strategic_goal ?? '')) body.strategic_goal = goal.trim() || null
    if (timezone !== company.timezone) body.timezone = timezone
    if (!sameList(constraints, company.constraints)) body.constraints = constraints.map((c) => c.trim()).filter(Boolean)

    try {
      await update.mutateAsync(body)
      toast.success('Company profile saved')
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const mapped = mapServerFieldErrors(err.fieldErrors)
        setErrors(mapped)
        if (!Object.keys(mapped).length) setFormError(err.message)
      } else {
        setFormError(errorMessage(err))
      }
    }
  }

  return (
    <Card>
      <CardHeader title="Company profile" />
      <form onSubmit={submit} noValidate>
        <CardBody className="space-y-4">
          <Field label="Company name" required error={errors.name}>
            {({ id, invalid, describedBy }) => (
              <Input id={id} value={name} invalid={invalid} aria-describedby={describedBy} maxLength={LIMITS.companyName + 20} onChange={(e) => setName(e.target.value)} />
            )}
          </Field>
          <Field label="Mission" error={errors.mission}>
            {({ id, invalid, describedBy }) => (
              <Textarea id={id} rows={2} value={mission} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setMission(e.target.value)} />
            )}
          </Field>
          <Field label="Vision" error={errors.vision}>
            {({ id, invalid, describedBy }) => (
              <Textarea id={id} rows={2} value={vision} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setVision(e.target.value)} />
            )}
          </Field>
          <Field label="Strategic goal" error={errors.strategic_goal}>
            {({ id, invalid, describedBy }) => (
              <Textarea id={id} rows={2} value={goal} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setGoal(e.target.value)} />
            )}
          </Field>
          <Field label="Constraints" error={errors.constraints}>
            {({ id }) => (
              <div id={id}>
                <StringListEditor values={constraints} onChange={setConstraints} inputLabel="New constraint" placeholder="Add a constraint" emptyText="No constraints." />
              </div>
            )}
          </Field>
          <Field label="Timezone" error={errors.timezone}>
            {({ id, invalid }) => (
              <Select id={id} value={timezone} invalid={invalid} onChange={(e) => setTimezone(e.target.value)}>
                {timezones.map((tz) => (
                  <option key={tz} value={tz}>
                    {tz}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <FormError message={formError} />
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              disabled={!dirty || update.isPending}
              onClick={() => {
                setName(company.name)
                setMission(company.mission ?? '')
                setVision(company.vision ?? '')
                setGoal(company.strategic_goal ?? '')
                setConstraints(company.constraints)
                setTimezone(company.timezone)
                setErrors({})
                setFormError(null)
              }}
            >
              Reset
            </Button>
            <Button type="submit" variant="primary" loading={update.isPending} disabled={!dirty}>
              Save changes
            </Button>
          </div>
        </CardBody>
      </form>
    </Card>
  )
}

function StatusCard({ company }: { company: Company }) {
  const update = useUpdateCompany(company.id)
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pausing = company.status === 'active'

  async function confirm() {
    setError(null)
    try {
      await update.mutateAsync({ status: pausing ? 'paused' : 'active' })
      toast.success(pausing ? 'Company paused' : 'Company resumed')
      setOpen(false)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <Card>
      <CardHeader title="Company status" />
      <CardBody className="flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-1">
          <Badge tone={pausing ? 'success' : 'warning'}>
            <Dot tone={pausing ? 'success' : 'warning'} />
            {pausing ? 'Active' : 'Paused'}
          </Badge>
          <p className="text-[12.5px] text-fg-3">
            {pausing
              ? 'Pausing marks the whole company as paused. Execution features arriving in later milestones will respect it.'
              : 'This company is paused. Resume it to mark it active again.'}
          </p>
        </div>
        <Button variant={pausing ? 'danger' : 'primary'} onClick={() => { setError(null); setOpen(true) }}>
          {pausing ? 'Pause company' : 'Resume company'}
        </Button>
      </CardBody>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={pausing ? `Pause ${company.name}?` : `Resume ${company.name}?`}
        confirmLabel={pausing ? 'Pause company' : 'Resume company'}
        destructive={pausing}
        loading={update.isPending}
        error={error}
        onConfirm={() => void confirm()}
        description={
          pausing
            ? 'The company status becomes "paused". Nothing is deleted, and you can resume it at any time.'
            : 'The company status becomes "active" again.'
        }
      />
    </Card>
  )
}

function AppearanceCard() {
  const { theme, setTheme } = useTheme()
  return (
    <Card>
      <CardHeader title="Appearance" description="Saved in this browser." />
      <CardBody>
        <div role="radiogroup" aria-label="Theme" className="grid grid-cols-2 gap-2">
          {(
            [
              ['dark', 'Dark', Moon],
              ['light', 'Light', Sun],
            ] as const
          ).map(([value, label, Icon]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={theme === value}
              onClick={() => setTheme(value)}
              className={
                'flex h-9 items-center justify-center gap-2 rounded-md border text-[13px] font-medium ' +
                (theme === value ? 'border-accent bg-accent-subtle text-accent-fg' : 'border-line-strong bg-raised text-fg-2 hover:bg-hover')
              }
            >
              <Icon className="size-4" aria-hidden />
              {label}
            </button>
          ))}
        </div>
      </CardBody>
    </Card>
  )
}

function AboutCard() {
  const health = useHealth()
  return (
    <Card>
      <CardHeader title="About this install" />
      {health.isPending ? (
        <LoadingState rows={3} label="Loading install info" />
      ) : health.isError ? (
        <ErrorState error={health.error} onRetry={() => health.refetch()} retrying={health.isFetching} />
      ) : (
        <CardBody className="space-y-4">
          {health.data.auth_mode === 'local' && (
            <div role="alert" className="flex items-start gap-2 rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-[12.5px] text-fg">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
              <p>
                <strong className="font-semibold">Local auth mode.</strong> Every request is treated as the local owner with no
                login. Only run this on localhost; never expose it to a network.
              </p>
            </div>
          )}
          <DefinitionList
            items={[
              { label: 'Version', value: <span className="font-mono">{health.data.version}</span> },
              { label: 'Auth mode', value: <span className="font-mono">{health.data.auth_mode}</span> },
              { label: 'API status', value: health.data.status },
              { label: 'Database', value: health.data.database },
            ]}
          />
        </CardBody>
      )}
    </Card>
  )
}
