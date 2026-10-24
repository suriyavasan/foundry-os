import { useMemo, useState, type FormEvent } from 'react'
import { errorMessage, useCreateCompany } from '@/api'
import { Button, Field, FormError, Input, Select } from '@/components/ui'
import { allTimezones, browserTimezone } from '@/lib/timezones'
import { VanishInput } from '../fx/VanishInput'
import { StepHeading } from '../StepFrame'

export const IDEA_MAX = 280

const IDEA_PLACEHOLDERS = [
  'A newsletter that explains one research paper a day',
  'A store that sells handmade candles online',
  'An agency that builds websites for local restaurants',
  'A tool that turns meeting notes into tasks',
]

/**
 * Step 3: the company's name, the one-line idea and the timezone. The company exists after this step; the idea is kept and
 * becomes the first Founder's note once the AI works (the server refuses notes before that), see OnboardingFlow.
 */
export function CompanyStep({ onDone }: { onDone: (companyId: string, idea: string) => void }) {
  const create = useCreateCompany()
  const zones = useMemo(() => allTimezones(), [])
  const [name, setName] = useState('')
  const [idea, setIdea] = useState('')
  const [timezone, setTimezone] = useState(browserTimezone)
  const [errors, setErrors] = useState<{ name?: string; idea?: string }>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [vanishing, setVanishing] = useState(false)

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
    setVanishing(idea.trim().length > 0)
    try {
      const company = await create.mutateAsync({ name: n, timezone, founder: { name: 'Atlas' } })
      onDone(company.id, idea.trim())
    } catch (err) {
      setVanishing(false)
      setFormError(errorMessage(err, 'Could not create the company'))
    }
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4" aria-label="Create your company">
      <StepHeading eyebrow="Your company" title="Name your company">
        One line is enough. Your AI Co-founder reads it as your first note and turns it into goals, a team and a plan.
      </StepHeading>
      <Field label="Company name" required error={errors.name}>
        {({ id, invalid, describedBy }) => <Input id={id} autoFocus className="h-10" value={name} invalid={invalid} aria-describedby={describedBy} placeholder="Acme Labs" onChange={(e) => setName(e.target.value)} />}
      </Field>
      <Field label="What is it going to do?" error={errors.idea} hint="Your one-line idea. Optional, and you can write more any time.">
        {({ id, invalid, describedBy }) => (
          <VanishInput id={id} value={idea} vanishing={vanishing} aria-invalid={invalid || undefined} aria-describedby={describedBy} placeholders={IDEA_PLACEHOLDERS} onChange={(e) => setIdea(e.target.value)} />
        )}
      </Field>
      <Field label="Timezone" hint="Used for schedules and reporting periods.">
        {({ id }) => (
          <Select id={id} className="h-10" value={timezone} onChange={(e) => setTimezone(e.target.value)}>
            {zones.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </Select>
        )}
      </Field>
      <FormError message={formError} />
      <Button type="submit" variant="primary" className="h-10 w-full" loading={create.isPending}>
        Create company
      </Button>
    </form>
  )
}
