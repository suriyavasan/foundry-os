import { AccountPanel } from '@/features/account/AccountPanel'
import { MyWorkPanel } from '@/features/mywork/MyWorkPanel'
import { TeamPanel } from '@/features/team/TeamPanel'
import { ActivityPanel } from '@/features/panels/ActivityPanel'
import { AgentPanel } from '@/features/panels/AgentPanel'
import { ApprovalsPanel } from '@/features/panels/ApprovalsPanel'
import { ArtifactPanel } from '@/features/panels/ArtifactPanel'
import { ArtifactsPanel } from '@/features/panels/ArtifactsPanel'
import { FinancePanel } from '@/features/panels/FinancePanel'
import { GoalsPanel } from '@/features/panels/GoalsPanel'
import { OrgPanel } from '@/features/panels/OrgPanel'
import { RoutinesPanel } from '@/features/panels/RoutinesPanel'
import { TaskPanel } from '@/features/panels/TaskPanel'
import { TasksPanel } from '@/features/panels/TasksPanel'
import { CofounderPanel } from '@/features/cofounder/CofounderPanel'
import { MemoryPanel } from '@/features/memory/MemoryPanel'
import { NotesPanel } from '@/features/notes/NotesPanel'
import { NoteThreadPanel } from '@/features/notes/NoteThreadPanel'
import { GuardrailsPanel } from '@/features/guardrails/GuardrailsPanel'
import { IntegrationsPanel } from '@/features/integrations/IntegrationsPanel'
import { SkillDetailPanel } from '@/features/skills/SkillDetailPanel'
import { SkillsPanel } from '@/features/skills/SkillsPanel'
import { SettingsPanel } from '@/features/settings/SettingsPanel'
import { serializePanel } from '@/lib/panels'
import { useCanvas } from './CanvasContext'

/** Renders the drawer for the panel named in the URL. Remounts when the panel changes. */
export function PanelHost() {
  const { panel } = useCanvas()
  if (!panel) return null
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
