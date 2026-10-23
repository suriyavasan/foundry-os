import { Plus, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { errorMessage, useAgentSkills, useAssignSkills, useRemoveAgentSkill, useSkills } from '@/api'
import type { Agent, Skill } from '@/api/types'
import { Badge, Boundary, Button, Card, CardHeader, Dialog, EmptyState, FormError, IconButton, Input, LoadingState, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { filterSkills, NO_FILTERS, sortSkills } from '@/lib/skills'
import { SkillStatusBadge, VerdictBadge } from './SkillBadges'

/** The agent's assigned skills, with remove and an "Add skills" picker over the active library. */
export function AgentSkillsTab({ agent }: { agent: Agent }) {
  const { companyId, openPanel } = useCanvas()
  const q = useAgentSkills(companyId, agent.id)
  const remove = useRemoveAgentSkill(companyId, agent.id)
  const toast = useToast()
  const [picker, setPicker] = useState(false)
  const [removing, setRemoving] = useState<string | null>(null)
  const closed = agent.status === 'retired' || agent.status === 'rejected'

  async function removeSkill(skill: Skill) {
    setRemoving(skill.id)
    try {
      await remove.mutateAsync(skill.id)
      toast.success(`${skill.name} removed from ${agent.name}`)
    } catch (e) {
      toast.error(errorMessage(e, 'Could not remove the skill'))
    } finally {
      setRemoving(null)
    }
  }

  return (
    <>
      <Card>
        <CardHeader
          title="Assigned skills"
          description="These are written into the agent’s runtime before each run. Only active skills are ever given to it."
          actions={
            <Button size="sm" variant="primary" icon={<Plus className="size-3.5" />} disabled={closed} onClick={() => setPicker(true)}>
              Add skills
            </Button>
          }
        />
        <Boundary
          query={q}
          milestone="M4"
          compact={false}
          unavailableTitle="Skills are not built yet"
          isEmpty={(d) => d.length === 0}
          empty={{
            title: 'No skills assigned',
            description: closed ? 'This agent is no longer active.' : 'Without skills this agent relies on its instructions alone. Add skills from the library.',
          }}
        >
          {(items) => (
            <ul className="divide-y divide-line">
              {items.map(({ skill, assigned_by_type, assigned_at }) => (
                <li key={skill.id} className="flex items-start gap-3 px-4 py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <button type="button" className="font-medium text-fg hover:underline" onClick={() => openPanel({ kind: 'skill', id: skill.id })}>
                        {skill.name}
                      </button>
                      <VerdictBadge verdict={skill.scan_verdict} bare />
                      {skill.status !== 'active' && <SkillStatusBadge status={skill.status} bare />}
                    </div>
                    <p className="line-clamp-2 text-[12.5px] text-fg-3">{skill.description}</p>
                    <p className="mt-0.5 text-[11.5px] text-fg-3">
                      Added by {assigned_by_type === 'human' ? 'you' : assigned_by_type === 'agent' ? 'an agent (HR)' : 'the system'} · {new Date(assigned_at).toLocaleDateString()}
                    </p>
                  </div>
                  <IconButton label={`Remove ${skill.name}`} disabled={closed || removing === skill.id} onClick={() => void removeSkill(skill)}>
                    <X className="size-4" aria-hidden />
                  </IconButton>
                </li>
              ))}
            </ul>
          )}
        </Boundary>
      </Card>
      {picker && <SkillPicker agent={agent} assigned={new Set((q.data ?? []).map((a) => a.skill.id))} onClose={() => setPicker(false)} />}
    </>
  )
}

function SkillPicker({ agent, assigned, onClose }: { agent: Agent; assigned: Set<string>; onClose: () => void }) {
  const { companyId } = useCanvas()
  const library = useSkills(companyId)
  const assign = useAssignSkills(companyId, agent.id)
  const toast = useToast()
  const [q, setQ] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)

  const options = useMemo(
    () => sortSkills(filterSkills((library.data ?? []).filter((s) => s.status === 'active' && !assigned.has(s.id)), { ...NO_FILTERS, q })),
    [library.data, assigned, q],
  )
  const toggle = (id: string) =>
    setPicked((p) => {
      const next = new Set(p)
      if (!next.delete(id)) next.add(id)
      return next
    })

  async function submit() {
    setError(null)
    try {
      await assign.mutateAsync({ skill_ids: [...picked] })
      toast.success(`${picked.size} skill${picked.size === 1 ? '' : 's'} added to ${agent.name}`)
      onClose()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !assign.isPending && onClose()}
      size="md"
      title={`Add skills to ${agent.name}`}
      description="Active library skills that this agent does not have yet. Quarantined and archived skills cannot be assigned."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={assign.isPending}>
            Cancel
          </Button>
          <Button variant="primary" disabled={picked.size === 0} loading={assign.isPending} onClick={submit}>
            Add {picked.size || ''} skill{picked.size === 1 ? '' : 's'}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Input aria-label="Search skills" placeholder="Search skills" value={q} onChange={(e) => setQ(e.target.value)} />
        {library.isPending ? (
          <LoadingState rows={5} label="Loading skills" />
        ) : library.isError ? (
          <FormError message={errorMessage(library.error)} />
        ) : options.length === 0 ? (
          <EmptyState compact title={q ? 'No skills match' : 'Nothing left to add'} description={q ? 'Try different words.' : 'Every active skill is already assigned to this agent.'} />
        ) : (
          <ul className="max-h-80 divide-y divide-line overflow-y-auto rounded-md border border-line" aria-label="Available skills">
            {options.map((s) => (
              <li key={s.id}>
                <label className="flex cursor-pointer items-start gap-2.5 px-3 py-2 hover:bg-hover">
                  <input type="checkbox" className="mt-1" checked={picked.has(s.id)} onChange={() => toggle(s.id)} />
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-1.5 text-[13px] font-medium text-fg">
                      {s.name}
                      {s.scan_verdict === 'caution' && <Badge tone="warning">Caution</Badge>}
                      {s.scan_verdict === 'dangerous' && <Badge tone="danger">Dangerous</Badge>}
                    </span>
                    <span className="line-clamp-2 block text-[12px] text-fg-3">{s.description}</span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}
        <FormError message={error} />
      </div>
    </Dialog>
  )
}
