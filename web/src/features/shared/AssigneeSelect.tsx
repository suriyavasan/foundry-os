import type { Agent, Member } from '@/api/types'
import { Select } from '@/components/ui'
import { isWorkingMember, memberRef } from '@/lib/people'

/**
 * The assignee picker. Agents are offered as they always were (value = agent id); human members (M7) follow in
 * their own group (value = `member:<id>`). Without members it renders exactly the agents-only list.
 */
export function AssigneeSelect({
  id,
  value,
  invalid,
  agents,
  members,
  onChange,
  unassignedLabel = 'Unassigned',
  'aria-label': ariaLabel,
}: {
  id?: string
  value: string
  invalid?: boolean
  agents: Agent[]
  members: Member[]
  onChange: (value: string) => void
  unassignedLabel?: string
  'aria-label'?: string
}) {
  const people = members.filter(isWorkingMember).sort((a, b) => a.display_name.localeCompare(b.display_name))
  const agentOptions = agents.map((a) => (
    <option key={a.id} value={a.id}>
      {a.name} · {a.role_title}
    </option>
  ))
  return (
    <Select id={id} aria-label={ariaLabel} value={value} invalid={invalid} onChange={(e) => onChange(e.target.value)}>
      <option value="">{unassignedLabel}</option>
      {people.length === 0 ? (
        agentOptions
      ) : (
        <>
          <optgroup label="AI agents">{agentOptions}</optgroup>
          <optgroup label="Team members">
            {people.map((m) => (
              <option key={m.id} value={memberRef(m.id)}>
                {m.display_name} · {m.title ?? m.role} (human)
              </option>
            ))}
          </optgroup>
        </>
      )}
    </Select>
  )
}
