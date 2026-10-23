import { useAgents, useDepartments } from '@/api'
import { DepartmentDialog } from '@/features/organization/DepartmentDialog'
import { HireDialog } from '@/features/organization/HireDialog'
import { CreateTaskDialog, GoalDialog, ProjectDialog } from '@/features/work/CreateDialogs'
import { useCanvas } from './CanvasContext'

/** Mounts whichever create dialog was requested (from a widget, a panel or the command palette). */
export function CreateHost() {
  const { companyId, creating, closeCreate } = useCanvas()
  if (!creating) return null
  switch (creating.kind) {
    case 'task':
      return <CreateTaskDialog companyId={companyId} preset={creating.preset} onClose={closeCreate} />
    case 'goal':
      return <GoalDialog companyId={companyId} onClose={closeCreate} />
    case 'project':
      return <ProjectDialog companyId={companyId} preset={creating.preset} onClose={closeCreate} />
    case 'hire':
    case 'department':
      return <OrgDialogs kind={creating.kind} />
  }
}

function OrgDialogs({ kind }: { kind: 'hire' | 'department' }) {
  const { companyId, closeCreate } = useCanvas()
  const agents = useAgents(companyId)
  const departments = useDepartments(companyId)
  if (!agents.data || !departments.data) return null
  return kind === 'hire' ? (
    <HireDialog open onOpenChange={(o) => !o && closeCreate()} companyId={companyId} agents={agents.data} departments={departments.data} />
  ) : (
    <DepartmentDialog open onOpenChange={(o) => !o && closeCreate()} companyId={companyId} agents={agents.data} />
  )
}
