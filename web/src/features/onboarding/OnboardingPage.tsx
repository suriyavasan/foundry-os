import { useMemo, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { ApiError, errorMessage, useCompanies, useCreateCompany } from '@/api'
import { StringListEditor } from '@/components/forms/StringListEditor'
import { Button, Card, CardBody, Field, FormError, Input, Select, Textarea, useToast } from '@/components/ui'
import { setLastCompanyId } from '@/lib/storage'
import { allTimezones, browserTimezone } from '@/lib/timezones'
import {
  LIMITS,
  mapServerFieldErrors,
  toCreateCompanyBody,
  validateOnboarding,
  type OnboardingErrors,
  type OnboardingValues,
} from './validation'

function initialValues(): OnboardingValues {
  return {
    name: '',
    mission: '',
    vision: '',
    strategic_goal: '',
    constraints: [],
    timezone: browserTimezone(),
    founder_name: 'Atlas',
    founder_model_provider: '',
    founder_model_name: '',
  }
}

export function OnboardingPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const companies = useCompanies()
  const create = useCreateCompany()
  const timezones = useMemo(() => allTimezones(), [])
  const [values, setValues] = useState<OnboardingValues>(initialValues)
  const [errors, setErrors] = useState<OnboardingErrors>({})
  const [formError, setFormError] = useState<string | null>(null)

  const set = <K extends keyof OnboardingValues>(key: K, value: OnboardingValues[K]) => {
    setValues((v) => ({ ...v, [key]: value }))
    if (errors[key]) setErrors((e) => ({ ...e, [key]: undefined }))
  }

  const hasCompanies = (companies.data?.length ?? 0) > 0

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const clientErrors = validateOnboarding(values)
    setErrors(clientErrors)
    if (Object.keys(clientErrors).length > 0) return
    try {
      const company = await create.mutateAsync(toCreateCompanyBody(values))
      setLastCompanyId(company.id)
      toast.success(`Created ${company.name}`)
      navigate(`/c/${company.id}`, { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.code === 'validation_error') {
        const mapped = mapServerFieldErrors(err.fieldErrors)
        setErrors(mapped)
        if (Object.keys(mapped).length === 0) setFormError(err.message)
      } else {
        setFormError(errorMessage(err))
      }
    }
  }

  return (
    <div className="min-h-screen bg-canvas px-4 py-10">
      <div className="mx-auto max-w-2xl">
        <div className="mb-6">
          <p className="text-[11px] font-semibold tracking-[0.12em] text-fg-3 uppercase">Company OS</p>
          <h1 className="mt-1 text-[22px] font-semibold tracking-tight text-fg">Create your company</h1>
          <p className="mt-1 max-w-xl text-[13px] text-fg-3">
            You are the Founder. Setting up a company also creates its AI Co-founder, who manages every other agent and reports to you.
          </p>
        </div>

        <form onSubmit={onSubmit} noValidate className="space-y-4">
          <Card>
            <CardBody className="space-y-4">
              <h2 className="text-[13px] font-semibold">Company</h2>
              <Field label="Company name" required error={errors.name}>
                {({ id, invalid, describedBy }) => (
                  <Input
                    id={id}
                    autoFocus
                    value={values.name}
                    invalid={invalid}
                    aria-describedby={describedBy}
                    maxLength={LIMITS.companyName + 20}
                    onChange={(e) => set('name', e.target.value)}
                    placeholder="Acme Labs"
                  />
                )}
              </Field>
              <Field label="Mission" error={errors.mission} hint="Why the company exists.">
                {({ id, invalid, describedBy }) => (
                  <Textarea id={id} rows={2} value={values.mission} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set('mission', e.target.value)} />
                )}
              </Field>
              <Field label="Vision" error={errors.vision} hint="Where it is going.">
                {({ id, invalid, describedBy }) => (
                  <Textarea id={id} rows={2} value={values.vision} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set('vision', e.target.value)} />
                )}
              </Field>
              <Field label="Strategic goal" error={errors.strategic_goal} hint="The goal the AI Co-founder will plan against.">
                {({ id, invalid, describedBy }) => (
                  <Textarea id={id} rows={2} value={values.strategic_goal} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set('strategic_goal', e.target.value)} />
                )}
              </Field>
              <Field label="Constraints" error={errors.constraints} hint="Hard boundaries every agent must respect (budget, legal, brand…).">
                {({ id }) => (
                  <div id={id}>
                    <StringListEditor
                      values={values.constraints}
                      onChange={(c) => set('constraints', c)}
                      inputLabel="New constraint"
                      placeholder="e.g. No spend over $50/month without approval"
                      addLabel="Add"
                      emptyText="No constraints yet."
                    />
                  </div>
                )}
              </Field>
              <Field label="Timezone" required error={errors.timezone} hint="Used for reporting periods and schedules.">
                {({ id, invalid, describedBy }) => (
                  <Select id={id} value={values.timezone} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set('timezone', e.target.value)}>
                    {timezones.map((tz) => (
                      <option key={tz} value={tz}>
                        {tz}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
            </CardBody>
          </Card>

          <Card>
            <CardBody className="space-y-4">
              <div>
                <h2 className="text-[13px] font-semibold">AI Co-founder</h2>
                <p className="mt-0.5 text-[12px] text-fg-3">
                  Starts active, with no runtime assigned. You can assign a runtime from its profile later.
                </p>
              </div>
              <Field label="AI Co-founder name" error={errors.founder_name}>
                {({ id, invalid, describedBy }) => (
                  <Input id={id} value={values.founder_name} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set('founder_name', e.target.value)} />
                )}
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Model provider" error={errors.founder_model_provider} hint="Optional, e.g. anthropic">
                  {({ id, invalid, describedBy }) => (
                    <Input id={id} value={values.founder_model_provider} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set('founder_model_provider', e.target.value)} />
                  )}
                </Field>
                <Field label="Model name" error={errors.founder_model_name} hint="Optional">
                  {({ id, invalid, describedBy }) => (
                    <Input id={id} value={values.founder_model_name} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set('founder_model_name', e.target.value)} />
                  )}
                </Field>
              </div>
            </CardBody>
          </Card>

          <FormError message={formError} />

          <div className="flex items-center justify-end gap-2">
            {hasCompanies && (
              <Link to="/" className="inline-flex h-8 items-center rounded-md px-3 text-[13px] font-medium text-fg-2 hover:bg-hover hover:text-fg">
                Cancel
              </Link>
            )}
            <Button type="submit" variant="primary" loading={create.isPending}>
              Create company
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
