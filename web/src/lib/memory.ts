import type { MemoryEntry, MemoryKind, MemorySource, MemoryStatus } from '@/api/types'
import type { Tone } from '@/components/ui/Badge'
import type { Panel } from './panels'
import { ID_RE } from './panels'
import { panelForRef } from './refs'

export const MEMORY_KINDS: MemoryKind[] = ['fact', 'decision', 'requirement', 'lesson', 'research']
export const MEMORY_KIND_LABEL: Record<MemoryKind, string> = { fact: 'Fact', decision: 'Decision', requirement: 'Requirement', lesson: 'Lesson', research: 'Research' }
export const MEMORY_KIND_HELP: Record<MemoryKind, string> = {
  fact: 'Something true about the company or its world.',
  decision: 'A call that was made, with the reason.',
  requirement: 'A constraint or spec every agent must respect.',
  lesson: 'Learned from a failure or a revision; helps similar work next time.',
  research: 'Findings gathered by an agent.',
}

export const MEMORY_STATUSES: MemoryStatus[] = ['proposed', 'verified', 'rejected', 'superseded']
export const MEMORY_STATUS_LABEL: Record<MemoryStatus, string> = { proposed: 'Proposed', verified: 'Verified', rejected: 'Rejected', superseded: 'Superseded' }
export const MEMORY_STATUS_TONE: Record<MemoryStatus, Tone> = { proposed: 'warning', verified: 'success', rejected: 'danger', superseded: 'neutral' }

export interface MemoryFilters {
  kind: MemoryKind | 'all'
  status: MemoryStatus | 'all'
  q: string
}
export const DEFAULT_MEMORY_FILTERS: MemoryFilters = { kind: 'all', status: 'all', q: '' }

/** Needs a human or the AI Co-founder to decide, then (and only then) agents see it. */
export const isDecidable = (e: Pick<MemoryEntry, 'status'>) => e.status === 'proposed'

export function filterMemory(entries: MemoryEntry[], f: MemoryFilters): MemoryEntry[] {
  const q = f.q.trim().toLowerCase()
  return entries
    .filter((e) => {
      if (f.kind !== 'all' && e.kind !== f.kind) return false
      if (f.status !== 'all' && e.status !== f.status) return false
      if (q && ![e.title ?? '', e.body, ...(e.tags ?? [])].join('\n').toLowerCase().includes(q)) return false
      return true
    })
    .sort((a, b) => (a.status === 'proposed' ? 0 : 1) - (b.status === 'proposed' ? 0 : 1) || (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0))
}

export function countByKind(entries: MemoryEntry[]): Record<MemoryKind | 'all', number> {
  const out: Record<MemoryKind | 'all', number> = { all: entries.length, fact: 0, decision: 0, requirement: 0, lesson: 0, research: 0 }
  for (const e of entries) if (e.kind in out) out[e.kind] += 1
  return out
}

export interface Provenance {
  text: string
  panel: Panel | null
}

/** "From note", "From task" ... with a link when the source has a view. `null` = recorded without a source. */
export function provenanceOf(source: MemorySource | null | undefined): Provenance | null {
  if (!source) return null
  const name = source.label?.trim()
  switch (source.type) {
    case 'note':
      return { text: name || 'Founder’s note', panel: panelForRef('note', source.id) }
    case 'task':
      return { text: name || 'Task', panel: panelForRef('task', source.id) }
    case 'artifact':
      return { text: name || 'Artifact', panel: panelForRef('artifact', source.id) }
    case 'run': {
      const task = source.task_id
      const panel: Panel | null = task && ID_RE.test(task) ? (source.id && ID_RE.test(source.id) ? { kind: 'task', id: task, run: source.id } : { kind: 'task', id: task }) : null
      return { text: name || 'Run', panel }
    }
    case 'agent':
      return { text: name || 'Agent', panel: panelForRef('agent', source.id) }
    case 'human':
      return { text: name || 'The Founder', panel: null }
    default:
      return { text: name || source.type, panel: null }
  }
}
