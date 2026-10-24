import { lazy, Suspense } from 'react'
import { LoadingState } from '@/components/ui'
import { serializePanel } from '@/lib/panels'
import { useCanvas } from './CanvasContext'

// Each panel is its own chunk: the canvas loads first, a panel's code only when it is opened.
const AccountPanel = lazy(() => import('@/features/account/AccountPanel').then((m) => ({ default: m.AccountPanel })))
const MyWorkPanel = lazy(() => import('@/features/mywork/MyWorkPanel').then((m) => ({ default: m.MyWorkPanel })))
const TeamPanel = lazy(() => import('@/features/team/TeamPanel').then((m) => ({ default: m.TeamPanel })))
const ActivityPanel = lazy(() => import('@/features/panels/ActivityPanel').then((m) => ({ default: m.ActivityPanel })))
const AgentPanel = lazy(() => import('@/features/panels/AgentPanel').then((m) => ({ default: m.AgentPanel })))
const ApprovalsPanel = lazy(() => import('@/features/panels/ApprovalsPanel').then((m) => ({ default: m.ApprovalsPanel })))
const ArtifactPanel = lazy(() => import('@/features/panels/ArtifactPanel').then((m) => ({ default: m.ArtifactPanel })))
const ArtifactsPanel = lazy(() => import('@/features/panels/ArtifactsPanel').then((m) => ({ default: m.ArtifactsPanel })))
const FinancePanel = lazy(() => import('@/features/panels/FinancePanel').then((m) => ({ default: m.FinancePanel })))
const GoalsPanel = lazy(() => import('@/features/panels/GoalsPanel').then((m) => ({ default: m.GoalsPanel })))
const OrgPanel = lazy(() => import('@/features/panels/OrgPanel').then((m) => ({ default: m.OrgPanel })))
const RoutinesPanel = lazy(() => import('@/features/panels/RoutinesPanel').then((m) => ({ default: m.RoutinesPanel })))
const TaskPanel = lazy(() => import('@/features/panels/TaskPanel').then((m) => ({ default: m.TaskPanel })))
const TasksPanel = lazy(() => import('@/features/panels/TasksPanel').then((m) => ({ default: m.TasksPanel })))
const CofounderPanel = lazy(() => import('@/features/cofounder/CofounderPanel').then((m) => ({ default: m.CofounderPanel })))
const MemoryPanel = lazy(() => import('@/features/memory/MemoryPanel').then((m) => ({ default: m.MemoryPanel })))
const NotesPanel = lazy(() => import('@/features/notes/NotesPanel').then((m) => ({ default: m.NotesPanel })))
const NoteThreadPanel = lazy(() => import('@/features/notes/NoteThreadPanel').then((m) => ({ default: m.NoteThreadPanel })))
const GuardrailsPanel = lazy(() => import('@/features/guardrails/GuardrailsPanel').then((m) => ({ default: m.GuardrailsPanel })))
const IntegrationsPanel = lazy(() => import('@/features/integrations/IntegrationsPanel').then((m) => ({ default: m.IntegrationsPanel })))
const SkillDetailPanel = lazy(() => import('@/features/skills/SkillDetailPanel').then((m) => ({ default: m.SkillDetailPanel })))
const SkillsPanel = lazy(() => import('@/features/skills/SkillsPanel').then((m) => ({ default: m.SkillsPanel })))
const ReachabilityPanel = lazy(() => import('@/features/reachability/ReachabilityPanel').then((m) => ({ default: m.ReachabilityPanel })))
const AiPanel = lazy(() => import('@/features/ai/AiPanel').then((m) => ({ default: m.AiPanel })))
const ReflectionsPanel = lazy(() => import('@/features/reflections/ReflectionsPanel').then((m) => ({ default: m.ReflectionsPanel })))
const ReflectionThreadPanel = lazy(() => import('@/features/reflections/ReflectionThreadPanel').then((m) => ({ default: m.ReflectionThreadPanel })))
const StandardsPanel = lazy(() => import('@/features/reflections/StandardsPanel').then((m) => ({ default: m.StandardsPanel })))
const CommandCenterPanel = lazy(() => import('@/features/commandcenter/CommandCenterPanel').then((m) => ({ default: m.CommandCenterPanel })))
const SettingsPanel = lazy(() => import('@/features/settings/SettingsPanel').then((m) => ({ default: m.SettingsPanel })))

/** Renders the drawer for the panel named in the URL. Remounts when the panel changes. */
export function PanelHost() {
  const { panel } = useCanvas()
  if (!panel) return null
  return (
    <Suspense fallback={<PanelLoading />}>
      <PanelSwitch panel={panel} />
    </Suspense>
  )
}

function PanelLoading() {
  return (
    <div className="fixed inset-y-0 right-0 z-40 w-[min(100vw,920px)] border-l border-line bg-canvas p-6 shadow-pop">
      <LoadingState label="Loading panel" rows={4} />
    </div>
  )
}

function PanelSwitch({ panel }: { panel: NonNullable<ReturnType<typeof useCanvas>['panel']> }) {
  const key = serializePanel(panel) + (panel.kind === 'task' ? `|${panel.run ?? ''}` : '')
  switch (panel.kind) {
    case 'org':
      return <OrgPanel key={key} />
    case 'tasks':
      return <TasksPanel key={key} />
    case 'activity':
      return <ActivityPanel key={key} />
    case 'artifacts':
      return <ArtifactsPanel key={key} />
    case 'approvals':
      return <ApprovalsPanel key={key} />
    case 'finance':
      return <FinancePanel key={key} />
    case 'settings':
      return <SettingsPanel key={key} />
    case 'integrations':
      return <IntegrationsPanel key={key} />
    case 'goals':
      return <GoalsPanel key={key} />
    case 'routines':
      return <RoutinesPanel key={key} />
    case 'skills':
      return <SkillsPanel key={key} />
    case 'notes':
      return <NotesPanel key={key} />
    case 'memory':
      return <MemoryPanel key={key} />
    case 'cofounder':
      return <CofounderPanel key={key} />
    case 'guardrails':
      return <GuardrailsPanel key={key} />
    case 'account':
      return <AccountPanel key={key} />
    case 'team':
      return <TeamPanel key={key} />
    case 'my-work':
      return <MyWorkPanel key={key} />
    case 'reachability':
      return <ReachabilityPanel key={key} />
    case 'ai':
      return <AiPanel key={key} />
    case 'reflections':
      return <ReflectionsPanel key={key} />
    case 'standards':
      return <StandardsPanel key={key} />
    case 'command-center':
      return <CommandCenterPanel key={key} />
    case 'reflection':
      return <ReflectionThreadPanel key={key} reflectionId={panel.id} />
    case 'note':
      return <NoteThreadPanel key={key} noteId={panel.id} />
    case 'skill':
      return <SkillDetailPanel key={key} skillId={panel.id} />
    case 'agent':
      return <AgentPanel key={key} agentId={panel.id} />
    case 'task':
      return <TaskPanel key={key} taskId={panel.id} runId={panel.run} />
    case 'artifact':
      return <ArtifactPanel key={key} artifactId={panel.id} />
  }
}
