import type { ActivityFilters, AgentFilters } from './types'

/** Query keys. Everything company-scoped lives under ['company', cid, ...]. */
export const qk = {
  me: ['me'] as const,
  health: ['health'] as const,
  runtimes: ['runtimes'] as const,
  companies: ['companies'] as const,
  company: (cid: string) => ['company', cid] as const,
  companyDetail: (cid: string) => ['company', cid, 'detail'] as const,
  overview: (cid: string) => ['company', cid, 'overview'] as const,
  agents: (cid: string) => ['company', cid, 'agents'] as const,
  agentList: (cid: string, filters: AgentFilters = {}) => ['company', cid, 'agents', 'list', filters] as const,
  agent: (cid: string, id: string) => ['company', cid, 'agents', 'detail', id] as const,
  departments: (cid: string) => ['company', cid, 'departments'] as const,
  activity: (cid: string, filters: ActivityFilters = {}) => ['company', cid, 'activity', filters] as const,
  activityAll: (cid: string) => ['company', cid, 'activity'] as const,
}
