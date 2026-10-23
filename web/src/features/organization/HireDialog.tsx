import { ArrowLeft, Plus, X } from 'lucide-react'
import { useMemo, useState, type FormEvent } from 'react'
import { ApiError, errorMessage, removeAgentSkill, useCreateAgent, useModelProviders, useRoleTemplates, useRuntimes, useSkills } from '@/api'
import type { Agent, Department, RoleTemplate } from '@/api/types'
import { Avatar } from '@/components/avatar'
import { Button, Chip, Dialog, EmptyState, ErrorState, Field, FormError, Input, LoadingState, Select, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { ROLE_FAMILY_LABEL } from '@/lib/roleFamily'
import {
  emptyAgentForm,
  mapAgentServerErrors,
  toCreateAgentBody,
  validateAgentForm,
  type AgentFormErrors,
  type AgentFormValues,
} from './agentForm'
import { AgentFormFields } from './AgentFormFields'
import { buildTemplateHire, hireableTemplates, prefillFromTemplate, type TemplatePrefill } from './hireTemplate'

/** `null` = nothing chosen yet; 'custom' = no template. */
type Choice = RoleTemplate | 'custom' | null

export function HireDialog({
  open,
  onOpenChange,
  companyId,
  agents,
  departments,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  companyId: string
  agents: Agent[]
  departments: Department[]
}) {
  const founder = agents.find((a) => a.kind === 'founder' && a.status === 'active')
  const templatesQ = useRoleTemplates(companyId)
  const skillsQ = useSkills(companyId)
  const providers = useModelProviders(companyId)
  const [choice, setChoice] = useState<Choice>(null)
  const [prefill, setPrefill] = useState<TemplatePrefill | null>(null)
  const [skills, setSkills] = useState<string[]>([])
  const [values, setValues] = useState<AgentFormValues>(() => emptyAgentForm({ manager_agent_id: founder?.id ?? '' }))
  const [errors, setErrors] = useState<AgentFormErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const create = useCreateAgent(companyId)
  const runtimes = useRuntimes()
  const toast = useToast()
  const { openPanel } = useCanvas()

  const templates = useMemo(() => hireableTemplates(templatesQ.data ?? []), [templatesQ.data])
  const library = skillsQ.data ?? []

  const patch = (p: Partial<AgentFormValues>) => {
    setValues((v) => ({ ...v, ...p }))
    setErrors((e) => {
      const next = { ...e }
      for (const k of Object.keys(p)) delete next[k as keyof AgentFormValues]
      return next
    })
  }

  function choose(c: Exclude<Choice, null>) {
    setFormError(null)
    setErrors({})
    setChoice(c)
    if (c === 'custom') {
      setPrefill(null)
      setSkills([])
      setValues(emptyAgentForm({ manager_agent_id: founder?.id ?? '' }))
      return
    }
    const p = prefillFromTemplate(c, { agents, departments, skills: skillsQ.isSuccess ? library : null })
    setPrefill(p)
    setSkills(p.defaultSkills)
    setValues(p.values)
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const clientErrors = validateAgentForm(values, { budgets: true })
    setErrors(clientErrors)
    if (Object.keys(clientErrors).length) return
    try {
      let agent: Agent
      let removeFailures: string[] = []
      if (choice && choice !== 'custom' && prefill) {
        const { body, removeSkills } = buildTemplateHire(values, choice, skills, prefill.defaultSkills)
        agent = await create.mutateAsync(body)
        // The server assigns a template's default skills on create; drop the ones the human deselected.
        const results = await Promise.allSettled(removeSkills.map((slug) => removeAgentSkill(companyId, agent.id, slug)))
        removeFailures = removeSkills.filter((_, i) => results[i]!.status === 'rejected')
      } else {
        agent = await create.mutateAsync(toCreateAgentBody(values))
      }
      toast.success(`${agent.name} proposed. Activate them from their profile.`)
      if (removeFailures.length) toast.error(`Could not remove ${removeFailures.join(', ')} from ${agent.name}. Remove it in their Skills tab.`)
      onOpenChange(false)
      openPanel({ kind: 'agent', id: agent.id })
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const mapped = mapAgentServerErrors(err.fieldErrors)
        setErrors(mapped)
        if (!Object.keys(mapped).length) setFormError(err.message)
      } else {
        setFormError(errorMessage(err))
      }
    }
  }

  const picking = choice === null
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      title={picking ? 'Propose hire: choose a role' : 'Propose hire'}
      description={picking ? 'A role template sets the title, instructions, tools and starting skills. You can edit everything before hiring.' : 'Creates a proposed agent. It does nothing until you activate it.'}
      footer={
        picking ? (
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
        ) : (
          <>
            <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={create.isPending}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" form="hire-form" loading={create.isPending}>
              Propose hire
            </Button>
          </>
        )
      }
    >
      {picking ? (
        skillsQ.isPending ? <LoadingState rows={4} label="Loading the skill library" /> : <TemplatePicker query={templatesQ} templates={templates} onChoose={choose} />
      ) : (
        <form id="hire-form" onSubmit={submit} noValidate className="space-y-4">
          <div className="flex items-center gap-3 rounded-md border border-line bg-raised px-3 py-2">
            <Avatar family={choice === 'custom' ? 'generic' : choice.family} size="md" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] font-medium text-fg">{choice === 'custom' ? 'Custom role' : choice.title}</div>
              <div className="truncate text-[12px] text-fg-3">{choice === 'custom' ? 'No template: you set everything.' : `${ROLE_FAMILY_LABEL[choice.family]} · template ${choice.slug}`}</div>
            </div>
            <Button size="sm" variant="ghost" icon={<ArrowLeft className="size-3.5" />} disabled={create.isPending} onClick={() => setChoice(null)}>
              Change role
            </Button>
          </div>
          <AgentFormFields
            showKind
            values={values}
            errors={errors}
            onChange={patch}
            departments={departments}
            agents={agents}
            runtimes={runtimes.data?.items}
            providers={providers.data}
            noDepartmentLabel={prefill?.departmentToCreate ? `Template default: ${prefill.departmentToCreate} (created)` : 'No department'}
            departmentHint={prefill?.departmentToCreate ? `The ${prefill.departmentToCreate} department does not exist yet and is created when you hire.` : undefined}
          />
          {choice !== 'custom' && prefill && <SkillsEditor selected={skills} onChange={setSkills} defaults={prefill.defaultSkills} unavailable={prefill.unavailableSkills} library={library} libraryLoading={skillsQ.isPending} />}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Monthly budget (USD)" error={errors.monthly_budget} hint="Leave blank for no limit.">
              {({ id, invalid, describedBy }) => (
                <Input id={id} inputMode="decimal" placeholder="e.g. 50" value={values.monthly_budget} invalid={invalid} aria-describedby={describedBy} onChange={(e) => patch({ monthly_budget: e.target.value })} />
              )}
            </Field>
            <Field label="Daily budget (USD)" error={errors.daily_budget} hint="Leave blank for no limit.">
              {({ id, invalid, describedBy }) => (
                <Input id={id} inputMode="decimal" placeholder="e.g. 5" value={values.daily_budget} invalid={invalid} aria-describedby={describedBy} onChange={(e) => patch({ daily_budget: e.target.value })} />
              )}
            </Field>
          </div>
          <FormError message={formError} />
        </form>
      )}
    </Dialog>
  )
}

function TemplatePicker({ query, templates, onChoose }: { query: ReturnType<typeof useRoleTemplates>; templates: RoleTemplate[]; onChoose: (c: Exclude<Choice, null>) => void }) {
  if (query.isPending) return <LoadingState rows={4} label="Loading role templates" />
  const missing = query.isError && query.error instanceof ApiError && (query.error.status === 404 || query.error.status === 405)
  return (
    <div className="space-y-3">
      {query.isError && !missing && <ErrorState compact error={query.error} title="Could not load role templates" onRetry={() => void query.refetch()} retrying={query.isFetching} />}
      {missing && <p className="rounded-md border border-line bg-raised px-3 py-2 text-[12.5px] text-fg-3">Role templates are not built yet on this backend. You can still hire a custom role.</p>}
      {query.isSuccess && templates.length === 0 && <EmptyState compact title="No role templates" description="Hire a custom role instead." />}
      <ul className="grid gap-2 sm:grid-cols-2" aria-label="Role templates">
        {templates.map((t) => (
          <li key={t.slug}>
            <button
              type="button"
              onClick={() => onChoose(t)}
              className="flex h-full w-full items-start gap-3 rounded-md border border-line bg-surface p-3 text-left transition-colors hover:border-line-strong hover:bg-hover focus-visible:border-accent"
            >
              <Avatar family={t.family} size="lg" />
              <span className="min-w-0 flex-1">
                <span className="flex items-baseline gap-2">
                  <span className="truncate text-[13.5px] font-semibold text-fg">{t.title}</span>
                  <span className="shrink-0 text-[11.5px] text-fg-3">{ROLE_FAMILY_LABEL[t.family]}</span>
                </span>
                <span className="mt-0.5 line-clamp-3 block text-[12.5px] text-fg-2">{t.description}</span>
                <span className="mt-1.5 flex flex-wrap items-center gap-1">
                  {t.default_skill_slugs.slice(0, 3).map((s) => (
                    <Chip key={s}>{s}</Chip>
                  ))}
                  {t.default_skill_slugs.length > 3 && <span className="text-[11.5px] text-fg-3">+{t.default_skill_slugs.length - 3}</span>}
                  {t.default_skill_slugs.length === 0 && <span className="text-[11.5px] text-fg-3">No default skills</span>}
                </span>
              </span>
            </button>
          </li>
        ))}
        <li>
          <button
            type="button"
            onClick={() => onChoose('custom')}
            className="flex h-full w-full items-start gap-3 rounded-md border border-dashed border-line-strong bg-surface p-3 text-left transition-colors hover:bg-hover focus-visible:border-accent"
          >
            <Avatar family="generic" size="lg" />
            <span className="min-w-0 flex-1">
              <span className="block text-[13.5px] font-semibold text-fg">Custom role</span>
              <span className="mt-0.5 block text-[12.5px] text-fg-2">Start from a blank form. No template defaults and no starting skills.</span>
            </span>
          </button>
        </li>
      </ul>
    </div>
  )
}

function SkillsEditor({
  selected,
  onChange,
  defaults,
  unavailable,
  library,
  libraryLoading,
}: {
  selected: string[]
  onChange: (s: string[]) => void
  defaults: string[]
  unavailable: string[]
  library: Array<{ slug: string; name: string; status: string }>
  libraryLoading: boolean
}) {
  const addable = library.filter((s) => s.status === 'active' && !selected.includes(s.slug)).sort((a, b) => a.name.localeCompare(b.name))
  return (
    <fieldset className="space-y-2">
      <legend className="text-[12px] font-medium text-fg-2">Skills</legend>
      {libraryLoading ? (
        <p className="text-[12.5px] text-fg-3">Loading the library…</p>
      ) : (
        <>
          {selected.length === 0 ? (
            <p className="text-[12.5px] text-fg-3">No skills selected.</p>
          ) : (
            <ul className="flex flex-wrap gap-1.5" aria-label="Selected skills">
              {selected.map((slug) => (
                <li key={slug} className="inline-flex items-center gap-1 rounded-sm border border-line bg-raised py-0.5 pr-0.5 pl-1.5 font-mono text-[11.5px] text-fg">
                  {slug}
                  {!defaults.includes(slug) && <span className="font-sans text-[10.5px] text-fg-3">added</span>}
                  <button type="button" aria-label={`Remove ${slug}`} className="inline-flex size-5 items-center justify-center rounded-sm text-fg-3 hover:bg-hover hover:text-fg" onClick={() => onChange(selected.filter((s) => s !== slug))}>
                    <X className="size-3" aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="flex items-center gap-2">
            <Select aria-label="Add a skill" className="max-w-72" value="" onChange={(e) => e.target.value && onChange([...selected, e.target.value])} disabled={addable.length === 0}>
              <option value="">{addable.length === 0 ? 'No more active skills' : 'Add a skill…'}</option>
              {addable.map((s) => (
                <option key={s.slug} value={s.slug}>
                  {s.name}
                </option>
              ))}
            </Select>
            <Plus className="size-3.5 text-fg-3" aria-hidden />
          </div>
          {unavailable.length > 0 && (
            <p className="text-[12px] text-warning">
              Not in your library, so not assigned: {unavailable.join(', ')}. HR can install them later.
            </p>
          )}
        </>
      )}
    </fieldset>
  )
}
