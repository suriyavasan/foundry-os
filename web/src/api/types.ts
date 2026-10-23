/**
 * Hand-written mirror of docs/api.md (Milestone 1 contract).
 * Replace/augment with `pnpm gen:api` (src/api/schema.d.ts) once the backend is running.
 */

export type Uuid = string
/** ISO-8601 UTC timestamp. */
export type Timestamp = string

export interface Page<T> {
  items: T[]
  next_cursor: string | null
}

/* ---------------------------------- errors --------------------------------- */

export type ApiErrorCode =
  | 'bad_request'
  | 'unauthenticated'
  | 'forbidden'
  | 'not_found'
  | 'conflict'
  | 'invalid_transition'
  | 'validation_error'
  | 'internal_error'
  // client-side only
  | 'network_error'
  | 'invalid_response'

export interface ValidationDetail {
  loc: Array<string | number>
  msg: string
}

export interface ErrorEnvelope {
  error: { code: string; message: string; details: unknown }
}

/* ----------------------------- health & identity ---------------------------- */

export interface Health {
  status: string
  database: string
  version: string
  auth_mode: string
}

export interface User {
  id: Uuid
  email: string
  display_name: string
}

export interface Membership {
  company_id: Uuid
  company_name: string
  role: 'owner' | 'admin' | 'viewer' | (string & {})
}

export interface Me {
  user: User
  memberships: Membership[]
}

/* --------------------------------- company --------------------------------- */

export type CompanyStatus = 'active' | 'paused'

export interface Company {
  id: Uuid
  name: string
  slug: string
  mission: string | null
  vision: string | null
  strategic_goal: string | null
  constraints: string[]
  timezone: string
  status: CompanyStatus
  created_at: Timestamp
  updated_at: Timestamp
}

export interface CreateFounderRequest {
  name?: string
  model_provider?: string | null
  model_name?: string | null
  runtime?: string
}

export interface CreateCompanyRequest {
  name: string
  mission?: string | null
  vision?: string | null
  strategic_goal?: string | null
  constraints?: string[]
  timezone?: string
  founder?: CreateFounderRequest
}

export type UpdateCompanyRequest = Partial<
  Pick<Company, 'name' | 'mission' | 'vision' | 'strategic_goal' | 'constraints' | 'timezone' | 'status'>
>

/* ---------------------------------- agents --------------------------------- */

export type AgentKind = 'founder' | 'lead' | 'specialist' | 'reviewer'
export type AgentStatus = 'proposed' | 'active' | 'paused' | 'retiring' | 'retired' | 'rejected'
export type AgentLifecycleAction = 'activate' | 'reject' | 'pause' | 'resume' | 'retire'

export interface Agent {
  id: Uuid
  company_id: Uuid
  name: string
  role_title: string
  kind: AgentKind
  status: AgentStatus
  department_id: Uuid | null
  manager_agent_id: Uuid | null
  reports_to_human: boolean
  responsibilities: string | null
  instructions: string | null
  model_provider: string | null
  model_name: string | null
  runtime: string
  runtime_config: Record<string, unknown>
  tool_permissions: string[]
  monthly_budget_micros: number | null
  daily_budget_micros: number | null
  created_at: Timestamp
  updated_at: Timestamp
  status_changed_at: Timestamp
}

export interface CreateAgentRequest {
  name: string
  role_title: string
  kind: AgentKind
  department_id?: Uuid | null
  manager_agent_id?: Uuid | null
  reports_to_human?: boolean
  responsibilities?: string | null
  instructions?: string | null
  model_provider?: string | null
  model_name?: string | null
  runtime?: string
  runtime_config?: Record<string, unknown>
  tool_permissions?: string[]
  monthly_budget_micros?: number | null
  daily_budget_micros?: number | null
}

/** Editable fields only: status, kind and company_id are not editable. */
export type UpdateAgentRequest = Partial<
  Pick<
    Agent,
    | 'name'
    | 'role_title'
    | 'department_id'
    | 'manager_agent_id'
    | 'responsibilities'
    | 'instructions'
    | 'model_provider'
    | 'model_name'
    | 'runtime'
    | 'runtime_config'
    | 'tool_permissions'
    | 'monthly_budget_micros'
    | 'daily_budget_micros'
  >
>

export interface AgentFilters {
  status?: AgentStatus
  department_id?: Uuid
}

/* ------------------------------- departments ------------------------------- */

export interface Department {
  id: Uuid
  company_id: Uuid
  name: string
  description: string | null
  lead_agent_id: Uuid | null
  agent_count: number
  created_at: Timestamp
  updated_at: Timestamp
}

export interface CreateDepartmentRequest {
  name: string
  description?: string | null
  lead_agent_id?: Uuid | null
}

export type UpdateDepartmentRequest = Partial<CreateDepartmentRequest>

/* --------------------------------- activity -------------------------------- */

export type ActorType = 'human' | 'agent' | 'system'

export interface AuditEvent {
  id: Uuid
  company_id: Uuid
  actor_type: ActorType
  actor_id: Uuid | null
  actor_label: string
  action: string
  target_type: string
  target_id: Uuid | null
  summary: string
  data: Record<string, unknown>
  created_at: Timestamp
}

export interface ActivityFilters {
  target_type?: string
  target_id?: Uuid
}

/* -------------------------------- overview --------------------------------- */

export interface SubsystemUnavailable {
  available: false
  milestone: string
}
/** Once a subsystem ships, the API returns `available: true` plus its own payload. */
export interface SubsystemAvailable {
  available: true
  [key: string]: unknown
}
export type Subsystem = SubsystemUnavailable | SubsystemAvailable

export interface Overview {
  company: Company
  founder: Agent | null
  agents: {
    total: number
    by_status: Partial<Record<AgentStatus, number>>
  }
  departments: { total: number }
  tasks: Subsystem
  spending: Subsystem
  approvals: Subsystem
  artifacts: Subsystem
  routines: Subsystem
  recent_activity: AuditEvent[]
}

/* -------------------------------- runtimes --------------------------------- */

export type RuntimeHealthStatus = 'ok' | 'degraded' | 'unavailable' | 'not_configured'

export interface RuntimeHealth {
  status: RuntimeHealthStatus
  detail: string
  checked_at: Timestamp
}

export interface RuntimeInfo {
  id: string
  display_name: string
  kind: string
  capabilities: string[]
  health: RuntimeHealth
  config_hint: string | null
}

export interface RuntimeList {
  items: RuntimeInfo[]
}
