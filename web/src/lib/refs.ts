import { ID_RE, type Panel } from './panels'

/**
 * Records the AI Co-founder and the alert system point at (`{type, id}` pairs) and the drawer panel
 * that shows them. Goals and projects share the Goals & plan panel; unknown types have no link, so
 * the UI shows plain text instead of a dead button.
 */
const LABEL: Record<string, string> = {
  goal: 'Goal',
  project: 'Project',
  task: 'Task',
  schedule: 'Schedule',
  memory: 'Memory',
  artifact: 'Artifact',
  agent: 'Agent',
  hire: 'Hire',
  approval: 'Approval',
  decision: 'Decision',
  note: 'Note',
  run: 'Run',
}

export function refTypeLabel(type: string): string {
  return LABEL[type] ?? type.replace(/[_-]+/g, ' ').replace(/^./, (c) => c.toUpperCase())
}

export function panelForRef(type: string | null | undefined, id: string | null | undefined): Panel | null {
  if (!type) return null
  switch (type) {
    case 'goal':
    case 'project':
      return { kind: 'goals' }
    case 'schedule':
    case 'routine':
      return { kind: 'routines' }
    case 'memory':
      return { kind: 'memory' }
    case 'approval':
      return { kind: 'approvals' }
    case 'decision':
      return { kind: 'activity' }
    case 'hire':
      return id && ID_RE.test(id) ? { kind: 'agent', id } : { kind: 'org' }
  }
  if (!id || !ID_RE.test(id)) return null
  switch (type) {
    case 'task':
      return { kind: 'task', id }
    case 'artifact':
      return { kind: 'artifact', id }
    case 'agent':
      return { kind: 'agent', id }
    case 'note':
      return { kind: 'note', id }
    default:
      return null
  }
}
