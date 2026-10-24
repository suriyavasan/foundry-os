import { lazy, Suspense } from 'react'
import { useAgents, useDepartments } from '@/api'
import { useCanvas } from './CanvasContext'

// Dialogs load on first use (their forms are not needed to paint the canvas).
const DepartmentDialog = lazy(() => import('@/features/organization/DepartmentDialog').then((m) => ({ default: m.DepartmentDialog })))
const HireDialog = lazy(() => import('@/features/organization/HireDialog').then((m) => ({ default: m.HireDialog })))
const CreateTaskDialog = lazy(() => import('@/features/work/CreateDialogs').then((m) => ({ default: m.CreateTaskDialog })))
const GoalDialog = lazy(() => import('@/features/work/CreateDialogs').then((m) => ({ default: m.GoalDialog })))
const ProjectDialog = lazy(() => import('@/features/work/CreateDialogs').then((m) => ({ default: m.ProjectDialog })))

/** Mounts whichever create dialog was requested (from a widget, a panel or the command palette). */
export function CreateHost() {
  const { creating } = useCanvas()
  if (!creating) return null
  return (
    <Suspense fallback={null}>
      <CreateSwitch />
    </Suspense>
  )
}

function CreateSwitch() {
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
