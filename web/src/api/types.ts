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
  /** M4: explicit employee type (set by role templates). The UI infers it only when absent (lib/roleFamily). */
  role_family?: RoleFamily | null
  /** M4: slug of the role template the agent was hired from. */
  role_template?: string | null
  /** M2: what the agent is doing right now. Absent on an M1 backend. */
  live?: AgentLive | null
}

export interface CreateAgentRequest {
  name: string
  role_title: string
  /** Optional since M4: a role template supplies it when omitted. */
  kind?: AgentKind
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
  /** M4: role template slug. Fields not sent take the template's defaults; its default skills are assigned. */
  role_template?: string | null
  /** M4: extra skills (library slugs) to assign on top of the template's defaults. */
  skill_slugs?: string[] | null
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

/* ======================================================================== *
 * Milestone 2 (docs/specs/m2-work-engine.md). Hand-written mirror of the spec.
 * Not yet in schema.d.ts (generated from the M1 backend); the integrator extends
 * contract.check.ts once the backend serves them.
 * ======================================================================== */

export type Priority = 'low' | 'normal' | 'high' | 'urgent'
export type CostKind = 'verified' | 'estimated' | 'unknown'
export type CreatedByType = 'human' | 'agent' | 'system'

/* ----------------------------- employee types ------------------------------ */

export type RoleFamily =
  | 'founder'
  | 'hr'
  | 'engineering'
  | 'design'
  | 'marketing'
  | 'sales'
  | 'customer_success'
  | 'research'
  | 'qa'
  | 'operations'
  | 'finance'
  | 'generic'

/* ------------------------------- live state -------------------------------- */

export type LiveState = 'idle' | 'working' | 'blocked' | 'paused' | 'offline'

export interface LiveTaskRef {
  id: Uuid
  title?: string | null
  status?: TaskStatus | null
}
export interface LiveRunRef {
  id: Uuid
  status?: RunStatus | null
  started_at?: Timestamp | null
}

/** `GET /agents/{id}/activity-now`, also embedded as `live` in agent list responses. */
export interface AgentLive {
  state: LiveState
  current_task: LiveTaskRef | null
  current_run: LiveRunRef | null
  summary: string | null
  since: Timestamp | null
}

/* --------------------------------- policy ---------------------------------- */

export type AutonomyMode = 'autonomous' | 'supervised'
export type ApprovalKind =
  | 'external_purchase'
  | 'budget_increase'
  | 'external_communication'
  | 'production_deploy'
  | 'sensitive_action'
  | 'hire'
  | 'other'

export interface CompanyPolicy {
  company_id?: Uuid
  autonomy_mode: AutonomyMode
  human_approval_required_for: ApprovalKind[]
  daily_budget_micros: number | null
  monthly_budget_micros: number | null
  max_concurrent_runs: number
  max_delegation_depth: number
  max_tasks_per_planning_cycle: number
  max_attempts_per_task: number
  max_revisions: number
  max_run_seconds: number
  max_headcount: number
  heartbeat_interval_seconds: number
  default_run_reservation_micros: number
  allowed_model_providers: string[]
  updated_at?: Timestamp
}

/** Only human owner/admin may PATCH. Agents get 403. */
export type UpdatePolicyRequest = Partial<Omit<CompanyPolicy, 'company_id' | 'updated_at'>>

/* ------------------------------ goals & projects --------------------------- */

export type GoalStatus = 'proposed' | 'active' | 'achieved' | 'abandoned'

export interface Goal {
  id: Uuid
  company_id: Uuid
  title: string
  description: string | null
  status: GoalStatus
  priority: Priority
  owner_agent_id: Uuid | null
  target_date: string | null
  created_by_type: CreatedByType
  created_by_id: Uuid | null
  created_at: Timestamp
  updated_at: Timestamp
}

export interface CreateGoalRequest {
  title: string
  description?: string | null
  status?: GoalStatus
  priority?: Priority
  owner_agent_id?: Uuid | null
  target_date?: string | null
}
export type UpdateGoalRequest = Partial<CreateGoalRequest>

export type ProjectStatus = 'planned' | 'active' | 'paused' | 'completed' | 'cancelled'

export interface Project {
  id: Uuid
  company_id: Uuid
  goal_id: Uuid | null
  name: string
  description: string | null
  status: ProjectStatus
  lead_agent_id: Uuid | null
  budget_micros: number | null
  created_by_type: CreatedByType
  created_by_id: Uuid | null
  created_at: Timestamp
  updated_at: Timestamp
}

export interface CreateProjectRequest {
  name: string
  description?: string | null
  goal_id?: Uuid | null
  status?: ProjectStatus
  lead_agent_id?: Uuid | null
  budget_micros?: number | null
}
export type UpdateProjectRequest = Partial<CreateProjectRequest>

/* ---------------------------------- tasks ---------------------------------- */

export type TaskKind = 'work' | 'review' | 'planning' | 'hiring' | 'routine'
export type TaskStatus =
  | 'backlog'
  | 'ready'
  | 'in_progress'
  | 'in_review'
  | 'revision_requested'
  | 'blocked'
  | 'done'
  | 'failed'
  | 'cancelled'

export interface TaskError {
  code: string
  message: string
  run_id?: Uuid | null
  /** Extra context, e.g. `limit` / `retry_after` for budget errors. */
  [key: string]: unknown
}

export interface Task {
  id: Uuid
  company_id: Uuid
  project_id: Uuid | null
  parent_task_id: Uuid | null
  title: string
  description: string | null
  acceptance_criteria: string | null
  kind: TaskKind
  status: TaskStatus
  priority: Priority
  assignee_agent_id: Uuid | null
  reviewer_agent_id: Uuid | null
  required_skills: string[]
  budget_micros: number | null
  attempt_count: number
  revision_count: number
  delegation_depth: number
  due_at: Timestamp | null
  started_at: Timestamp | null
  completed_at: Timestamp | null
  result_summary: string | null
  error: TaskError | null
  input_refs: string[]
  output_refs: string[]
  created_by_type: CreatedByType
  created_by_id: Uuid | null
  idempotency_key?: string | null
  created_at: Timestamp
  updated_at: Timestamp
  /**
   * ASSUMPTION: the spec lists dependency endpoints (POST/DELETE) but not how they are read back.
   * The UI reads `depends_on` (ids) from the task when present and degrades to "not reported".
   */
  depends_on?: Uuid[]
}

export interface CreateTaskRequest {
  title: string
  description?: string | null
  acceptance_criteria?: string | null
  kind?: TaskKind
  priority?: Priority
  project_id?: Uuid | null
  parent_task_id?: Uuid | null
  assignee_agent_id?: Uuid | null
  reviewer_agent_id?: Uuid | null
  budget_micros?: number | null
  idempotency_key?: string | null
}
export type UpdateTaskRequest = Partial<
  Pick<
    CreateTaskRequest,
    | 'title'
    | 'description'
    | 'acceptance_criteria'
    | 'priority'
    | 'project_id'
    | 'assignee_agent_id'
    | 'reviewer_agent_id'
    | 'budget_micros'
  >
>

export type TaskAction = 'cancel' | 'block' | 'unblock' | 'retry'

export interface TaskFilters {
  status?: TaskStatus
  project_id?: Uuid
  assignee_agent_id?: Uuid
  parent_task_id?: Uuid
  kind?: TaskKind
}

/* ----------------------------------- runs ---------------------------------- */

export type RunStatus = 'queued' | 'starting' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'timed_out'

export interface TaskRun {
  id: Uuid
  company_id: Uuid
  task_id: Uuid
  agent_id: Uuid
  attempt_number: number
  status: RunStatus
  runtime: string
  runtime_execution_id: string | null
  queued_at: Timestamp | null
  started_at: Timestamp | null
  finished_at: Timestamp | null
  heartbeat_at: Timestamp | null
  cancel_requested: boolean
  exit_reason: string | null
  error: { code: string; message: string; [k: string]: unknown } | null
  /** Null until the runtime reports usage. */
  input_tokens: number | null
  output_tokens: number | null
  cost_micros: number | null
  cost_kind: CostKind
  created_at?: Timestamp
}

export interface RunEvent {
  run_id?: Uuid
  seq: number
  type: string
  payload: Record<string, unknown>
  occurred_at: Timestamp
}

/* -------------------------------- artifacts -------------------------------- */

export type ArtifactKind = 'document' | 'code' | 'design' | 'report' | 'data' | 'link' | 'other'
export type ArtifactStatus = 'draft' | 'submitted' | 'accepted' | 'rejected'

export interface ArtifactVersion {
  artifact_id?: Uuid
  version: number
  size_bytes: number
  sha256: string
  created_by_type: CreatedByType
  created_by_id: Uuid | null
  note: string | null
  created_at: Timestamp
}

export interface Artifact {
  id: Uuid
  company_id: Uuid
  project_id: Uuid | null
  task_id: Uuid | null
  run_id: Uuid | null
  created_by_type: CreatedByType
  created_by_id: Uuid | null
  title: string
  kind: ArtifactKind
  mime_type: string
  status: ArtifactStatus
  current_version: number
  created_at: Timestamp
  updated_at: Timestamp
  /** `GET /artifacts/{id}` includes versions; list responses may omit them. */
  versions?: ArtifactVersion[]
}

export interface ArtifactFilters {
  project_id?: Uuid
  task_id?: Uuid
  kind?: ArtifactKind
  status?: ArtifactStatus
  q?: string
}

/* -------------------------------- approvals -------------------------------- */

export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'changes_requested' | 'expired'
export type ApproverType = 'human' | 'founder'
export type ApprovalDecision = 'approve' | 'reject' | 'request_changes'
export type Risk = 'low' | 'medium' | 'high'

export interface Approval {
  id: Uuid
  company_id: Uuid
  kind: ApprovalKind
  status: ApprovalStatus
  approver: ApproverType
  requested_by_agent_id: Uuid | null
  task_id: Uuid | null
  title: string
  reason: string | null
  amount_micros: number | null
  vendor: string | null
  recurrence: 'one_time' | 'monthly' | 'yearly' | null
  risk: Risk | null
  evidence?: unknown
  scope?: unknown
  scope_hash?: string
  decided_by_type: CreatedByType | null
  decided_by_id: Uuid | null
  decided_at: Timestamp | null
  decision_note: string | null
  consumed_at: Timestamp | null
  created_at: Timestamp
  updated_at: Timestamp
}

export interface ApprovalFilters {
  status?: ApprovalStatus
  approver?: ApproverType
}

/* ---------------------------- decisions & schedules ------------------------ */

export interface Decision {
  id: Uuid
  company_id: Uuid
  made_by_type: CreatedByType
  made_by_id: Uuid | null
  title: string
  rationale: string | null
  related_task_id: Uuid | null
  related_project_id: Uuid | null
  context?: unknown
  created_at: Timestamp
}

export type ScheduleKind = 'heartbeat' | 'routine'

export interface Schedule {
  id: Uuid
  company_id: Uuid
  name: string
  kind: ScheduleKind
  agent_id: Uuid
  interval_seconds: number | null
  cron: string | null
  timezone: string
  task_template?: {
    title?: string
    description?: string
    kind?: TaskKind
    priority?: Priority
    acceptance_criteria?: string
  } | null
  enabled: boolean
  next_run_at: Timestamp | null
  last_fired_at: Timestamp | null
  created_at: Timestamp
  updated_at: Timestamp
}

/* ----------------------------------- usage --------------------------------- */

export type UsageGroupBy = 'agent' | 'project' | 'model' | 'day'

/**
 * ASSUMPTION: the spec says `/usage` returns "aggregates (micros, tokens, by cost_kind)" without a
 * schema. The UI normalises (lib/usage.ts) any of: rows with `cost_kind`, rows with a `by_cost_kind`
 * map, or rows with `verified_micros|estimated_micros|unknown_micros`.
 */
export interface UsageRow {
  key: string
  label: string | null
  input_tokens: number
  output_tokens: number
  total_micros: number
  by_kind: Record<CostKind, number>
}
export interface UsageReport {
  rows: UsageRow[]
  totals: UsageRow
}

/* ------------------------------ company events ----------------------------- */

/** One row of the live-update outbox, delivered over SSE (`id:` is the cursor). */
export interface CompanyEvent {
  id: number
  type: string
  payload: Record<string, unknown>
  created_at?: Timestamp
}

/* ======================================================================== *
 * Milestone 4 (docs/api.md "Milestone 4", docs/specs/m4-skills-and-hr.md).
 * Mirrored against schema.d.ts in contract.check.ts.
 * ======================================================================== */

export type SkillSourceKind = 'hermes_bundled' | 'hermes_optional' | 'github' | 'company_authored' | 'uploaded'
export type SkillTrust = 'builtin' | 'trusted' | 'community'
export type ScanVerdict = 'safe' | 'caution' | 'dangerous' | 'unscanned'
export type SkillStatus = 'active' | 'quarantined' | 'archived'

export interface Skill {
  id: Uuid
  company_id: Uuid
  slug: string
  name: string
  description: string
  category: string | null
  tags: string[]
  version: string | null
  author: string | null
  license: string | null
  source_kind: SkillSourceKind
  source_ref: string
  blob_sha256: string
  trust: SkillTrust
  scan_verdict: ScanVerdict
  status: SkillStatus
  requires_toolsets: string[]
  /** Variable names only, never values. */
  required_env: string[]
  created_by_type: CreatedByType
  created_by_id: Uuid | null
  created_at: Timestamp
  updated_at: Timestamp
}

export interface SkillScanFinding {
  category?: string
  pattern?: string
  severity?: string
  file?: string
  line?: number | null
  description?: string
}

/**
 * The server types `scan_report` as a free-form object; the documented members are read defensively
 * (lib/skills.ts `readScanReport`).
 */
export interface SkillScanReport {
  verdict?: ScanVerdict
  scanner_version?: string
  summary?: string
  finding_count?: number
  findings?: SkillScanFinding[]
  [key: string]: unknown
}

export interface SkillFileInfo {
  path: string
  size_bytes: number
}

export interface SkillAssignedAgent {
  id: Uuid
  name: string
  role_title: string
  status: string
}

export interface SkillDetail extends Skill {
  scan_report: Record<string, unknown>
  frontmatter: Record<string, unknown>
  files: SkillFileInfo[]
  assigned_agents: SkillAssignedAgent[]
}

export interface SkillFileContent {
  path: string
  content: string
  truncated: boolean
}

export interface SkillFilters {
  status?: SkillStatus
  source_kind?: SkillSourceKind
  tag?: string
  q?: string
}

export type CatalogSource = 'all' | 'library' | 'hermes' | 'github'

export interface CatalogItem {
  slug: string
  name: string
  description: string
  tags: string[]
  license: string | null
  source: 'library' | 'hermes' | 'github'
  source_kind: string | null
  source_ref: string
  item_type?: 'skill' | 'repository'
  installed: boolean
  skill_id?: Uuid | null
  status?: string | null
  stars?: number | null
}

export interface InstallSkillRequest {
  source_ref: string
  slug?: string | null
}

export interface InstallSkillResult {
  skill: Skill
  outcome: 'installed' | 'updated' | 'unchanged' | (string & {})
  quarantined: boolean
  scan: Record<string, unknown>
}

export interface AgentSkill {
  skill: Skill
  assigned_by_type: CreatedByType
  assigned_by_id: Uuid | null
  assigned_at: Timestamp
}

export interface AssignSkillsRequest {
  skill_ids?: Uuid[]
  slugs?: string[]
}

export interface RoleTemplate {
  slug: string
  family: RoleFamily
  title: string
  description: string
  default_kind: AgentKind
  default_department: string | null
  instructions: string
  default_toolsets: string[]
  default_skill_slugs: string[]
  required_skill_tags: string[]
  builtin: boolean
  id?: Uuid | null
  created_by_type?: string | null
  created_by_id?: Uuid | null
}

/* ======================================================================== *
 * Milestone 3b (docs/specs/m3-company-agent-runtime.md "M3b"): model providers and credentials.
 * ASSUMPTION: these endpoints are specified in prose only and were not served when this was written.
 * Paths: /companies/{cid}/model-providers[/detect|/{id}/test], /companies/{cid}/credentials.
 * Every response is normalised defensively (lib/providers.ts) and a 404 renders "not built yet".
 * ======================================================================== */

export type ProviderKind = 'ollama' | 'openrouter' | 'anthropic' | 'openai' | 'openai_compatible'

export interface ModelProvider {
  id: Uuid
  name: string
  kind: ProviderKind | (string & {})
  base_url: string | null
  credential_id: Uuid | null
  default_model: string | null
  context_length: number | null
  is_local: boolean
  enabled: boolean
  /** Micro-USD per 1M tokens. null = unknown (never shown as $0). */
  input_price_per_mtok_micros: number | null
  output_price_per_mtok_micros: number | null
  created_at?: Timestamp
  updated_at?: Timestamp
}

export interface CreateModelProviderRequest {
  name: string
  kind: ProviderKind
  base_url?: string | null
  credential_id?: Uuid | null
  default_model: string
  context_length?: number
  is_local?: boolean | null
  enabled?: boolean
  input_price_per_mtok_micros?: number | null
  output_price_per_mtok_micros?: number | null
  /** Make this the company's default model provider. */
  make_default?: boolean
}
export type UpdateModelProviderRequest = Partial<Pick<CreateModelProviderRequest, 'enabled' | 'default_model' | 'context_length'>>

/** Credential metadata. The secret value is write-only: no response ever carries it. */
export interface Credential {
  id: Uuid
  name: string
  kind: string
  created_at?: Timestamp
  created_by_type?: string | null
  revoked_at?: Timestamp | null
}

export type CredentialKind = 'api_key' | 'token' | 'other'
export interface CreateCredentialRequest {
  name: string
  kind?: CredentialKind
  /** Write-only. Sent once, never read back, never logged. */
  value: string
}

/** Normalised `GET /model-providers/detect` (lib/providers.ts `normalizeDetect`). */
export interface DetectedModel {
  name: string
  size_bytes: number | null
  context_length: number | null
}
export interface DetectResult {
  reachable: boolean
  base_url: string | null
  models: DetectedModel[]
  message: string | null
}

/** Normalised `POST /model-providers/{id}/test`. */
export interface ProviderTestResult {
  ok: boolean
  message: string | null
  /** Server-supplied warnings, e.g. the served context window is smaller than configured. */
  warnings: string[]
  latency_ms: number | null
  model: string | null
}

/* ======================================================================== *
 * Milestone 5 (docs/specs/m5-autonomous-company.md §2 and §7): the Founder's notes, the AI
 * Co-founder summary, company memory and alerts.
 * ASSUMPTION: the M5 backend is not built yet. Paths and fields below follow the spec literally and
 * fill the gaps conservatively; they are re-checked against schema.d.ts in contract.check.ts as soon
 * as the generated schema contains the matching schema names. A 404 renders "not built yet".
 *   POST/GET /companies/{cid}/notes            GET /companies/{cid}/notes/{id} (+ replies)
 *   POST /companies/{cid}/notes/{id}/replies   PATCH /companies/{cid}/notes/{id}  {pinned?, status?}
 *   GET /companies/{cid}/cofounder/summary     POST /companies/{cid}/cofounder/wake
 *   GET/POST /companies/{cid}/memory           POST /companies/{cid}/memory/{id}/verify|reject
 *   GET /companies/{cid}/alerts                POST /companies/{cid}/alerts/{id}/resolve
 * Internal values keep the old name: the AI Co-founder is the agent of kind `founder`.
 * ======================================================================== */

export type NoteStatus = 'new' | 'triaged' | 'archived'
export type NoteCategory = 'directive' | 'goal' | 'requirement' | 'idea' | 'feedback' | 'question' | 'context' | 'noise'

/** A record the AI Co-founder created or changed while handling a note. `ref_id` is validated server-side. */
export interface NoteAction {
  type: string
  ref_id: Uuid
  /** Optional display name of the record (otherwise the UI resolves it from cached lists). */
  label?: string | null
}

export interface NoteTriage {
  category: NoteCategory
  summary: string
  actions: NoteAction[]
  /** 0..1, or null when the model did not report one. */
  confidence: number | null
}

export type NoteAuthorType = 'human' | 'agent'

export interface NoteReply {
  id: Uuid
  note_id: Uuid
  author_type: NoteAuthorType
  author_id: Uuid | null
  body: string
  created_at: Timestamp
}

export interface FounderNote {
  id: Uuid
  company_id: Uuid
  author_user_id: Uuid | null
  /** Markdown, at most 10 KB. */
  body: string
  /** Artifact ids. */
  attachments: Uuid[]
  status: NoteStatus
  triage: NoteTriage | null
  triaged_by_run_id: Uuid | null
  pinned: boolean
  created_at: Timestamp
  updated_at: Timestamp
  /** List rows carry the newest reply (for the preview) and the reply count. */
  last_reply?: NoteReply | null
  reply_count?: number
}

export interface NoteDetail extends FounderNote {
  replies: NoteReply[]
}

export interface CreateNoteRequest {
  body: string
  attachments?: Uuid[]
}
export interface UpdateNoteRequest {
  pinned?: boolean
  status?: 'archived' | 'new' | 'triaged'
}
export interface CreateNoteReplyRequest {
  body: string
}

/** A note as held in the client cache: optimistic rows exist only until the server confirms them. */
export type ClientNote = FounderNote & { pending?: boolean }

/* ----------------------------------- alerts --------------------------------- */

export type AlertSeverity = 'info' | 'warning' | 'critical'
export type AlertStatus = 'open' | 'resolved'

export interface Alert {
  id: Uuid
  company_id: Uuid
  severity: AlertSeverity
  /** Machine kind, e.g. `budget_threshold`, `run_failed`. */
  kind: string
  title: string
  body: string | null
  status: AlertStatus
  /** The record the alert is about: `task`, `project`, `goal`, `agent`, `approval`, `run`, `artifact`, `note`, `schedule`. */
  related_type: string | null
  related_id: Uuid | null
  created_at: Timestamp
  resolved_at?: Timestamp | null
  resolved_by_type?: CreatedByType | null
  resolved_by_id?: Uuid | null
}

/* ----------------------------------- memory --------------------------------- */

export type MemoryKind = 'fact' | 'decision' | 'requirement' | 'lesson' | 'research'
export type MemoryStatus = 'proposed' | 'verified' | 'rejected' | 'superseded'

export interface MemorySource {
  /** `note`, `task`, `run`, `artifact`, `agent` or `human`. */
  type: string
  id: Uuid | null
  /** A run lives under its task: the task panel opens with `?run=`. */
  task_id?: Uuid | null
  label?: string | null
}

export interface MemoryEntry {
  id: Uuid
  company_id: Uuid
  kind: MemoryKind
  title: string | null
  body: string
  status: MemoryStatus
  source: MemorySource | null
  tags: string[]
  proposed_by_type: CreatedByType | null
  proposed_by_id: Uuid | null
  verified_by_type: CreatedByType | null
  verified_by_id: Uuid | null
  verified_at: Timestamp | null
  rejection_reason?: string | null
  superseded_by_id?: Uuid | null
  created_at: Timestamp
  updated_at: Timestamp
}

/** A human-created entry is verified straight away (server side). */
export interface CreateMemoryRequest {
  kind: MemoryKind
  title?: string | null
  body: string
  tags?: string[]
}

/* ------------------------------ AI Co-founder summary ----------------------- */

export interface CofounderPlanTask {
  id: Uuid
  title: string
  status: TaskStatus
  assignee_agent_id: Uuid | null
}

export interface CofounderPlanProject {
  id: Uuid
  goal_id: Uuid | null
  name: string
  status: ProjectStatus
  lead_agent_id: Uuid | null
  tasks_done: number
  tasks_total: number
  /** Open (not done / cancelled) tasks, newest plan first. */
  open_tasks: CofounderPlanTask[]
}

export interface CofounderGoal {
  id: Uuid
  title: string
  status: GoalStatus
  priority: Priority
  description?: string | null
}

export interface CofounderBlocker {
  task_id: Uuid
  title: string
  reason: string | null
  agent_id: Uuid | null
  since: Timestamp | null
}

export interface CofounderLastRun {
  run_id: Uuid
  task_id: Uuid
  status: RunStatus
  started_at: Timestamp | null
  finished_at: Timestamp | null
  summary?: string | null
}

export interface CofounderSummary {
  /** The agent of kind `founder`, null when none is active. */
  agent_id: Uuid | null
  goals: CofounderGoal[]
  plan: {
    /** The AI Co-founder's own short "what I am doing and why". */
    summary: string | null
    updated_at: Timestamp | null
    projects: CofounderPlanProject[]
  }
  decisions: Decision[]
  blockers: CofounderBlocker[]
  alerts: Alert[]
  last_run: CofounderLastRun | null
  /** true while a coalesced wake task is already open. */
  wake_pending: boolean
  unprocessed_notes: number
}

export interface CofounderWakeResult {
  /** `queued` when a new planning task was created, `coalesced` when one was already open. */
  status: 'queued' | 'coalesced' | (string & {})
  task_id: Uuid | null
}
