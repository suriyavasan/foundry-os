import type { Agent, AgentKind } from '@/api/types'

/**
 * One vocabulary for the whole UI (docs/specs/m5 "Terminology").
 * The human is the **Founder**. The AI agent of kind `founder` is the **AI Co-founder**.
 * API values keep the old name (`kind: 'founder'`, `approver: 'founder'`); only labels change here.
 */
export const FOUNDER_LABEL = 'Founder'
export const AI_COFOUNDER_LABEL = 'AI Co-founder'
/** How the signed-in human is named wherever "you" is shown with a role. */
export const YOU_FOUNDER_LABEL = `You · ${FOUNDER_LABEL}`

export const AGENT_KIND_LABEL: Record<AgentKind, string> = {
  founder: AI_COFOUNDER_LABEL,
  lead: 'Lead',
  specialist: 'Specialist',
  reviewer: 'Reviewer',
}

export function isCofounder(agent: Pick<Agent, 'kind'> | null | undefined): boolean {
  return agent?.kind === 'founder'
}

/** The role line shown under an agent's name: the AI Co-founder is always labelled as such. */
export function displayRole(agent: Pick<Agent, 'kind' | 'role_title'>): string {
  return agent.kind === 'founder' ? AI_COFOUNDER_LABEL : agent.role_title
}

/** The active AI Co-founder of a company, if any. */
export function findCofounder(agents: Agent[]): Agent | undefined {
  return agents.find((a) => a.kind === 'founder' && a.status === 'active') ?? agents.find((a) => a.kind === 'founder' && a.status !== 'retired' && a.status !== 'rejected')
}
