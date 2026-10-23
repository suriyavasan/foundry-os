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
  /** M7 (assumed): the login name. Absent on a local-owner backend. */
  username?: string
}

export interface Membership {
  company_id: Uuid
  company_name: string
  role: 'owner' | 'admin' | 'member' | 'viewer' | (string & {})
  /** M7 (assumed): the caller's member record in this company (`member:<id>` is the ref other records use). */
  member_id?: Uuid
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
  /** M7 (assumed): `agent:<id>` | `member:<id>`; supersedes `manager_agent_id` once agents can report to humans. */
  manager_ref?: string | null
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
  /**
   * Milestone 6 fields (docs/specs/m6 section 6). All optional: a backend without M6 does not send them and the
   * Settings form then hides them instead of inventing defaults. USD fields are micros.
   */
  max_agent_monthly_budget_micros?: number | null
  hire_default_monthly_budget_micros?: number | null
  /** Percentages of a budget at which an alert is raised, e.g. [50, 80, 100]. */
  budget_alert_thresholds?: number[]
  model_routing?: ModelRouting
  allow_paid_fallback?: boolean
  auto_spend_limit_micros?: number
  stall_seconds?: number
}

export type ModelRouting = 'free_first' | 'pinned' | 'quality_first'

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
  /** M7 (assumed): `agent:<id>` | `member:<id>`. A human assignee has `assignee_agent_id: null`. */
  assignee_ref?: string | null
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
  /** M7 (assumed): set to `member:<id>` to assign a human; leave out for agents. */
  assignee_ref?: string | null
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
    | 'assignee_ref'
    | 'reviewer_agent_id'
    | 'budget_micros'
  >
>

export type TaskAction = 'cancel' | 'block' | 'unblock' | 'retry'

export interface TaskFilters {
  status?: TaskStatus
  project_id?: Uuid
  assignee_agent_id?: Uuid
  /** M7 (assumed): `agent:<id>` | `member:<id>`. */
  assignee_ref?: string
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
/** `peer` (M6): another agent checks the outbound content (maker-checker). The human is only informed. */
export type ApproverType = 'human' | 'founder' | 'peer'
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
  /** M6 (assumed): the risk tier of the guarded action and the action name. Absent before M6. */
  tier?: ActionTier | null
  action?: string | null
  /** M6 (assumed): the reviewer agent of a `peer` approval. */
  reviewer_agent_id?: Uuid | null
  /** M6 (assumed): the exact outbound content under review and the DLP scan result. Read via lib/approvalsM6. */
  outbound?: ApprovalOutbound | null
  dlp_findings?: DlpFinding[] | null
  /** M7 (assumed): `member:<id>` when the approval is scoped to one human; null/absent = any owner/admin. */
  approver_ref?: string | null
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

/* ======================================================================== *
 * Milestone 6 (docs/specs/m6-guardrails-and-governance.md): guardrails and finance.
 * ASSUMPTION: the M6 backend does not exist yet. Paths and schema names below follow the spec and fill its gaps
 * conservatively. Each type is re-checked against schema.d.ts in contract.check.ts (WhenInSchema) as soon as a
 * generated schema carries the expected name, so drift fails `pnpm typecheck`. A 404 renders "Arrives in M6".
 *
 * Company-scoped (`/companies/{cid}`):
 *   GET/POST /guardrails/rules                      GuardrailRule / GuardrailRuleCreate
 *   PATCH/DELETE /guardrails/rules/{id}             GuardrailRuleUpdate
 *   GET /guardrails/events?decision=&tier=&agent_id=&cursor=&limit=     GuardrailEvent  (newest first)
 *   GET /guardrails/rate-limits[?agent_id=]         RateLimitStatus[] (as {items} or a bare array)
 *   GET /agents/{id}/effective-permissions          EffectivePermissions
 *   GET /finance/summary?from=&to=                  FinanceSummary   (from/to: YYYY-MM-DD, inclusive, company timezone)
 *   GET /finance/breakdown?from=&to=&group_by=      FinanceBreakdown (agent|project|provider|model|runtime)
 *   GET /finance/trend?from=&to=                    FinanceTrend     (one point per day)
 *   GET /finance/runs?from=&to=&cursor=             FinanceRun       (page)
 *   GET/POST /finance/expenses                      ExternalExpense / ExternalExpenseCreate
 * Global (owner only, all companies):
 *   GET /system/status   POST /system/emergency-stop {reason?}   POST /system/resume {}      EmergencyStopStatus
 *     (the status may be the object itself or nested as `{emergency_stop: {...}}`; both are accepted)
 * Stream event names: guardrail.event (one per decision), guardrail.rule_changed, system.emergency_stop,
 *   system.resumed, expense.created.
 * ======================================================================== */

export type ActionTier =
  | 'read'
  | 'internal_write'
  | 'org_change'
  | 'external_read'
  | 'external_communication'
  | 'external_commitment'
  | 'spend'
  | 'identity'
  | 'destructive'

export type GuardrailDecision = 'allow' | 'peer_review' | 'cofounder_approval' | 'human_approval' | 'deny'
/** A logged decision. `rate_limited` is a 429 outcome rather than a rule decision. */
export type GuardrailEventDecision = GuardrailDecision | 'rate_limited'

export type GuardrailScopeType = 'all' | 'family' | 'agent'

export interface GuardrailConditions {
  /** The rule applies up to this amount (micro-USD). */
  max_amount_micros?: number | null
  /** The rule applies while at most N such actions happened today. */
  max_per_day?: number | null
  /** Hosts the rule is limited to. */
  allowed_domains?: string[] | null
  [key: string]: unknown
}

/** A company policy row. Human-editable only: agents (even with a run token) get 403. */
export interface GuardrailRule {
  id: Uuid
  company_id: Uuid
  /** The tier the rule covers. With `action` set it is the tier of that action. */
  tier: ActionTier
  /** A specific action (e.g. `email.send`); null = the whole tier. A specific action wins over a tier rule. */
  action: string | null
  scope_type: GuardrailScopeType
  /** Role family slug when `scope_type` is `family`, an agent id when `agent`, null for `all`. */
  scope_value: string | null
  decision: GuardrailDecision
  conditions: GuardrailConditions
  created_by_type?: CreatedByType | null
  created_by_id?: Uuid | null
  created_at: Timestamp
  updated_at: Timestamp
}

export interface GuardrailRuleCreate {
  tier: ActionTier
  action?: string | null
  scope_type: GuardrailScopeType
  scope_value?: string | null
  decision: GuardrailDecision
  conditions?: GuardrailConditions
}
export type GuardrailRuleUpdate = Partial<GuardrailRuleCreate>

/** One logged decision. Carries a payload digest only: never the payload, never a secret. */
export interface GuardrailEvent {
  id: Uuid
  company_id: Uuid
  created_at: Timestamp
  agent_id: Uuid | null
  run_id: Uuid | null
  /** Needed to open the run (a run lives under its task). */
  task_id: Uuid | null
  action: string
  tier: ActionTier
  decision: GuardrailEventDecision
  /** The rule that decided, or null when the built-in default did. */
  rule_id: Uuid | null
  approval_id: Uuid | null
  payload_digest: string | null
  reason: string | null
}

export interface GuardrailEventFilters {
  decision?: GuardrailEventDecision
  tier?: ActionTier
  agent_id?: Uuid
}

export type RateLimitKey =
  | 'api_calls_per_min'
  | 'tasks_created_per_hour'
  | 'external_communications_per_day'
  | 'hires_per_day'
  | 'skill_installs_per_day'
  | 'web_fetches_per_hour'

export interface RateLimitStatus {
  /** null = a company-wide window. */
  agent_id: Uuid | null
  key: RateLimitKey | (string & {})
  limit: number
  used: number
  window_seconds: number
  resets_at: Timestamp | null
}

export interface EffectiveTier {
  tier: ActionTier
  decision: GuardrailDecision
  /** `default` = the built-in table decided; `rule` = a company rule did. */
  source: 'default' | 'rule'
  rule_id: Uuid | null
}
export interface EffectiveTool {
  name: string
  tier: ActionTier
  decision: GuardrailDecision
  rule_id: Uuid | null
}
export interface EffectivePermissions {
  agent_id: Uuid
  tiers: EffectiveTier[]
  tools: EffectiveTool[]
  rate_limits?: RateLimitStatus[]
}

export interface ApprovalOutbound {
  /** Where it goes, e.g. `email`, `post`, `pull_request`. */
  channel?: string | null
  to?: string | null
  subject?: string | null
  body: string
}
/** A finding of the outbound DLP scan. The matched text is never part of it (and the UI never renders one). */
export interface DlpFinding {
  /** `secret`, `own_token`, `email`, `phone`, `internal_memory`, ... */
  kind: string
  severity?: 'low' | 'medium' | 'high' | null
  description?: string | null
  count?: number | null
}

/* ---- emergency stop ---- */
export interface EmergencyStopStatus {
  active: boolean
  activated_at: Timestamp | null
  activated_by: string | null
  reason: string | null
  /** Runs cancelled by the stop (known once it has run). */
  runs_cancelled?: number | null
}

/* ---- finance ---- */
export type FinancePeriod = 'day' | 'week' | 'month'
export type FinanceGroupBy = 'agent' | 'project' | 'provider' | 'model' | 'runtime'

/** Cost split by provenance. Only `verified` is a charge; `estimated` is not; `unknown` has no price. */
export interface CostSplit {
  verified_micros: number
  estimated_micros: number
  unknown_micros: number
}

export interface BudgetStatus {
  scope: 'company' | 'agent'
  agent_id: Uuid | null
  period: 'day' | 'month'
  /** null = no limit. */
  limit_micros: number | null
  verified_micros: number
  estimated_micros: number
}

export interface FinanceSummary {
  from: string
  to: string
  timezone: string
  totals: CostSplit & { input_tokens: number; output_tokens: number; runs: number }
  /** Spend of failed, cancelled and timed-out runs. */
  wasted: CostSplit & { runs: number }
  /** Human-recorded external expenses in the period (a separate figure, not model usage). */
  external_expenses_micros: number
  budgets: BudgetStatus[]
}

export interface FinanceBreakdownRow extends CostSplit {
  key: string
  label: string | null
  input_tokens: number
  output_tokens: number
  runs: number
}
export interface FinanceBreakdown {
  group_by: FinanceGroupBy
  from: string
  to: string
  items: FinanceBreakdownRow[]
}

export interface FinanceTrendPoint extends CostSplit {
  /** YYYY-MM-DD in the company timezone. */
  date: string
}
export interface FinanceTrend {
  from: string
  to: string
  points: FinanceTrendPoint[]
}

export interface FinanceRun {
  run_id: Uuid
  task_id: Uuid
  task_title: string | null
  agent_id: Uuid | null
  status: RunStatus
  started_at: Timestamp | null
  finished_at: Timestamp | null
  cost_micros: number | null
  cost_kind: CostKind
  model: string | null
  provider: string | null
}

export type ExpenseRecurrence = 'one_time' | 'monthly' | 'yearly'

export interface ExternalExpense {
  id: Uuid
  company_id: Uuid
  vendor: string
  description: string | null
  amount_micros: number
  recurrence: ExpenseRecurrence
  project_id: Uuid | null
  /** YYYY-MM-DD, company timezone. */
  incurred_on: string
  created_by_type?: CreatedByType | null
  created_by_id?: Uuid | null
  created_at: Timestamp
}
export interface ExternalExpenseCreate {
  vendor: string
  description?: string | null
  amount_micros: number
  recurrence: ExpenseRecurrence
  project_id?: Uuid | null
  incurred_on: string
}


/* ======================================================================== *
 * Milestone 7 (docs/specs/m7-accounts-and-team.md): accounts, login, team, notifications.
 * ASSUMPTION: the M7 backend does not exist yet. Paths follow the spec literally; the shapes below fill its
 * gaps conservatively. Each type is re-checked against schema.d.ts in contract.check.ts (WhenInSchema) as soon
 * as a generated schema carries the expected name, so drift fails `pnpm typecheck`. When `GET /auth/session`
 * answers 404 the UI keeps the M3b-sec owner-token gate and every M7 surface says "Arrives in M7".
 *
 * Auth (not company scoped, cookie `cos_session` + CSRF `cos_csrf`/`X-CSRF-Token`):
 *   GET  /auth/session                    AuthSession   (public: state setup_required | unauthenticated | authenticated)
 *   POST /auth/setup                      SetupRequest -> AuthSession          (first run only)
 *   POST /auth/login                      LoginRequest -> AuthSession          (or two_factor_required)
 *   POST /auth/logout
 *   POST /auth/password                   PasswordChange
 *   GET  /auth/sessions                   SessionInfo[]   DELETE /auth/sessions[/{id}]   (all / one)
 *   GET/POST /auth/tokens                 ApiToken[] / ApiTokenCreate -> ApiTokenCreated (secret shown once)
 *   DELETE /auth/tokens/{id}
 *   POST /auth/2fa/enroll                 -> TwoFactorEnroll    POST /auth/2fa/verify {code}    POST /auth/2fa/disable
 * Team (`/companies/{cid}`):
 *   GET/POST /invitations   DELETE /invitations/{id}        POST /invitations/{token}/accept (global path)
 *   GET /members            PATCH/DELETE /members/{id}
 * Notifications (global, per user): GET /notifications   POST /notifications/read {ids?, all?}
 * Stream event: notification.created
 * Humans on tasks: `assignee_ref` (`member:<id>`), completed with POST /tasks/{id}/complete {comment?, artifact_ids?}.
 * ======================================================================== */

export type AuthState = 'setup_required' | 'unauthenticated' | 'authenticated'

export interface AuthUser {
  id: Uuid
  username: string
  display_name: string
  email: string | null
  two_factor_enabled?: boolean
}

export interface AuthSession {
  state: AuthState
  user: AuthUser | null
  /** `setup_required` only: the request is not from loopback, so the setup token printed by the server is needed. */
  setup_token_required?: boolean
  /** Login answered "password ok, now the authenticator code". */
  two_factor_required?: boolean
  expires_at?: Timestamp | null
}

export interface SetupRequest {
  username: string
  display_name: string
  password: string
  email?: string | null
  setup_token?: string | null
}

export interface LoginRequest {
  username: string
  password: string
  totp_code?: string
}

export interface PasswordChange {
  current_password: string
  new_password: string
}

export interface SessionInfo {
  id: Uuid
  created_at: Timestamp
  last_seen_at: Timestamp | null
  expires_at: Timestamp
  ip: string | null
  user_agent: string | null
  current: boolean
}

export type TokenScope = 'read' | 'write' | 'admin'

export interface ApiToken {
  id: Uuid
  name: string
  scope: TokenScope
  /** First characters of the token, to recognise it in a list. Never the secret. */
  prefix?: string | null
  created_at: Timestamp
  last_used_at: Timestamp | null
  expires_at?: Timestamp | null
  revoked_at?: Timestamp | null
}

export interface ApiTokenCreate {
  name: string
  scope: TokenScope
}

/** The only response that ever carries the secret. */
export interface ApiTokenCreated extends ApiToken {
  token: string
}

export interface TwoFactorEnroll {
  /** Base32 secret, for manual entry. */
  secret: string
  /** `otpauth://totp/...`, rendered as a QR code in the browser. */
  otpauth_uri: string
}

export interface TwoFactorVerifyResult {
  enabled: boolean
  /** Shown once, when the server issues them. */
  recovery_codes?: string[]
}

export type MemberRole = 'owner' | 'admin' | 'member' | 'viewer'
export type MemberStatus = 'active' | 'disabled' | 'removed'

export interface Member {
  id: Uuid
  company_id: Uuid
  user_id: Uuid
  username: string
  display_name: string
  email: string | null
  role: MemberRole
  title: string | null
  /** `agent:<id>` | `member:<id>` | null (reports to the Founder). */
  manager_ref: string | null
  status: MemberStatus
  created_at: Timestamp
}

export interface MemberUpdate {
  role?: MemberRole
  title?: string | null
  manager_ref?: string | null
}

export type InvitationStatus = 'pending' | 'accepted' | 'expired' | 'revoked'

export interface Invitation {
  id: Uuid
  company_id: Uuid
  email: string
  role: MemberRole
  title: string | null
  manager_ref: string | null
  status: InvitationStatus
  expires_at: Timestamp
  created_at: Timestamp
  accepted_at?: Timestamp | null
}

export interface InvitationCreate {
  email: string
  role: MemberRole
  title?: string | null
  manager_ref?: string | null
}

/** `link`: no email provider is connected, so nothing was sent and the link must be shared by hand. */
export type InvitationDelivery = 'email' | 'link'

/** Returned once, at creation: the token is hashed at rest and cannot be shown again. */
export interface InvitationCreated extends Invitation {
  delivery: InvitationDelivery
  invite_url?: string | null
  token?: string | null
}

/** Empty when the caller is signed in; otherwise the new account's credentials. */
export interface InvitationAccept {
  username?: string
  display_name?: string
  password?: string
}

export interface InvitationAccepted {
  company_id: Uuid
  company_name?: string | null
  role?: MemberRole
  member_id?: Uuid
}

export type NotificationKind = 'task_assigned' | 'review_requested' | 'approval_needed' | 'note_replied' | 'alert' | (string & {})

export interface Notification {
  id: Uuid
  company_id: Uuid | null
  kind: NotificationKind
  title: string
  body: string | null
  /** The record it is about (`task`, `approval`, `note`, `alert`, ...), for the deep link. */
  related_type: string | null
  related_id: string | null
  created_at: Timestamp
  read_at: Timestamp | null
}

/** `GET /notifications` as the UI holds it. `unread_count` is the server's when it reports one. */
export interface NotificationList {
  items: Notification[]
  unread_count: number
}

export interface CompleteTaskRequest {
  comment?: string | null
  artifact_ids?: Uuid[]
}

/* ======================================================================== *
 * Milestone 8 (docs/specs/m8-reachability-and-identity.md): reachability and identity.
 * ASSUMPTION: the M8 backend does not exist yet. Paths follow the spec's sections; the shapes below fill its gaps
 * conservatively. Each type is re-checked against schema.d.ts in contract.check.ts (WhenInSchema) as soon as a
 * generated schema carries the expected name, so drift fails `pnpm typecheck`. Every M8 surface says "Arrives in M8"
 * when its endpoint answers 404/405.
 *
 * Device level (install-wide, owner only; not company scoped):
 *   GET   /reachability                     ReachabilityStatus (LAN + tunnel + public-exposure gate in one poll)
 *   PATCH /reachability/lan                 {enabled}              -> ReachabilityStatus  (rebinds 127.0.0.1 <-> 0.0.0.0)
 *   POST  /reachability/tunnel/start        TunnelConfig           -> TunnelStatus
 *   POST  /reachability/tunnel/stop                                -> TunnelStatus
 *   POST  /reachability/tunnel/install      {confirm: true}        -> TunnelStatus  (verified download; only when offered)
 * Company scoped (`/companies/{cid}`):
 *   GET   /connected-accounts               ConnectedAccount[]     POST ConnectedAccountCreate -> ConnectedAccount
 *   POST  /connected-accounts/{id}/test     -> ProviderCheck       DELETE /connected-accounts/{id}  (revoke)
 *   POST  /connected-accounts/oauth/start   {provider}             -> {authorize_url}   (gmail / outlook only)
 *   GET   /domain                           DomainPlan             (the company's current domain, DNS/TLS state)
 *   GET   /domain/registries                DomainRegistry[]       (free-subdomain GitHub-PR registries)
 *   GET   /domain/free-providers            FreeDomainProvider[]   (free domains with a dashboard)
 *   GET   /domain/search?q=                 DomainAvailability[]   (registrar availability + price)
 *   POST  /domain/registrations/preview     DomainRegistrationPreview request -> DomainRegistrationPreview
 *   POST  /domain/registrations             DomainRegistrationCreate -> DomainRegistration (creates the approval)
 *   POST  /domain/verify                    -> DomainPlan          (DNS check after the Founder did the dashboard steps)
 *   GET   /hosted-ui                        HostedUiDeployment | null     POST /hosted-ui/deploy -> HostedUiDeployment
 *   GET   /email                            EmailIdentity          PATCH EmailIdentityUpdate -> EmailIdentity
 *   GET   /mailboxes                        Mailbox[]              POST MailboxCreate -> Mailbox
 * Approvals created by M8 carry an `action` (see lib/reachabilityApprovals.ts): domain.register_pr, domain.purchase,
 * dns.change, mailbox.create, hosted_ui.deploy; the exact scope is in `approval.scope`.
 * Stream events (invalidate-only): reachability.updated, tunnel.updated, connected_account.updated, domain.updated,
 * mailbox.updated, hosted_ui.updated.
 * Secrets: provider tokens go once to POST /credentials (M3b store); a connected account only references the
 * credential id. No M8 response carries a secret.
 * ======================================================================== */

export type ReachabilityMode = 'local' | 'lan' | 'public'

export interface LanAddress {
  address: string
  family: 'ipv4' | 'ipv6'
  /** Network interface name, e.g. en0. */
  interface: string | null
  /** Full URL for this address, e.g. http://192.168.1.20:8700 */
  url: string
}

export interface LanStatus {
  /** The server is bound to all interfaces. */
  enabled: boolean
  /** `<host>.local` mDNS name, when announced. */
  mdns_name: string | null
  mdns_url: string | null
  addresses: LanAddress[]
}

export type TunnelProviderKind = 'cloudflare_quick' | 'cloudflare_named' | 'tailscale_funnel'
export type TunnelState = 'stopped' | 'starting' | 'running' | 'error'

/** The tunnel binary (`cloudflared` / `tailscale`) on this machine. */
export interface TunnelBinary {
  name: string
  found: boolean
  version: string | null
  path: string | null
  /** Plain-text install steps shown when `found` is false. */
  install_instructions: string[]
  /** The server offers a one-click, checksum-verified download (only after the owner confirms). */
  download_offered: boolean
  download_url: string | null
  download_sha256: string | null
  download_size_bytes: number | null
}

export interface TunnelProviderOption {
  kind: TunnelProviderKind
  /** The connected account provider this needs (`cloudflare`, `tailscale`), or null (quick tunnel needs none). */
  requires_account: string | null
  /** The server can start this provider right now. */
  available: boolean
  unavailable_reason: string | null
}

export interface TunnelStatus {
  provider: TunnelProviderKind | null
  state: TunnelState
  url: string | null
  hostname: string | null
  last_health_check_at: string | null
  healthy: boolean | null
  restarts: number
  started_at: string | null
  error: string | null
  binary: TunnelBinary | null
  providers: TunnelProviderOption[]
}

export interface TunnelConfig {
  provider: TunnelProviderKind
  /** Named tunnel / Funnel: the public hostname to serve (must be on a domain the connected account controls). */
  hostname?: string | null
}

/** The current domain, flattened for the landing screen (details in DomainPlan). */
export interface DomainSummary {
  domain: string | null
  dns_status: 'unknown' | 'pending' | 'ok' | 'error'
  tls_status: 'unknown' | 'pending' | 'ok' | 'error'
}

export type PublicExposureBlockCode = 'auth_local' | 'weak_password' | 'no_password' | 'two_factor_recommended' | (string & {})

export interface ReachabilityStatus {
  /** What is reachable right now (the server derives it; the UI re-derives it defensively). */
  mode: ReachabilityMode
  device_name: string
  hostname: string
  auth_mode: string
  /** `http://127.0.0.1:<port>` and friends. */
  local_urls: string[]
  lan: LanStatus
  tunnel: TunnelStatus
  /** The URL to share: the public URL if any, else the best LAN URL, else the local one. */
  active_url: string | null
  /** Set while LAN or public exposure may not be switched on (auth_mode=local or a weak owner password). Null = allowed. */
  public_exposure_blocked_reason: string | null
  public_exposure_blocked_code?: PublicExposureBlockCode | null
  /** The company-level domain, when one exists. */
  domain?: DomainSummary | null
}

export type DomainPath = 'github_registry' | 'free_provider' | 'paid_registrar'
export type DomainStatus = 'none' | 'awaiting_approval' | 'pr_open' | 'awaiting_founder' | 'pending_dns' | 'active' | 'error'

export interface DnsRecord {
  type: 'A' | 'AAAA' | 'CNAME' | 'TXT' | 'NS' | 'MX'
  name: string
  value: string
  ttl?: number | null
  /** Result of the last verification. */
  status?: 'ok' | 'missing' | 'mismatch' | 'pending' | null
}

export type DomainRegistrationStatus = 'awaiting_approval' | 'submitted' | 'pr_open' | 'merged' | 'awaiting_founder' | 'purchased' | 'rejected' | 'failed' | 'cancelled'

export interface DomainRegistration {
  id: Uuid
  company_id: Uuid
  path: DomainPath
  domain: string
  registry_id: string | null
  provider_id: string | null
  registrar: string | null
  status: DomainRegistrationStatus
  /** The human approval this registration waits for (shown in Approvals). */
  approval_id: Uuid | null
  pr_url: string | null
  price_micros: number | null
  error: string | null
  created_at: Timestamp
  updated_at: Timestamp
}

export interface DomainPlan {
  domain: string | null
  path: DomainPath | null
  status: DomainStatus
  dns_status: 'unknown' | 'pending' | 'ok' | 'error'
  tls_status: 'unknown' | 'pending' | 'ok' | 'error'
  /** Records the Founder (assisted path) or the control plane (connected DNS provider) creates. */
  dns_records: DnsRecord[]
  /** Exact human steps for the assisted path. */
  steps: string[]
  registration: DomainRegistration | null
  last_checked_at: Timestamp | null
  error: string | null
}

/** A free-subdomain registry that takes a registration file by pull request (`is-a.dev` style). */
export interface DomainRegistry {
  id: string
  name: string
  /** The shared suffix, e.g. `is-a.dev`. */
  suffix: string
  repo: string
  rules_url: string | null
  /** Plain-language eligibility rules, as the registry documents them. */
  eligibility: string[]
  review: 'human' | 'automatic'
  /** Allowed label: length and pattern (regex source), enforced again by the server. */
  name_min_length: number
  name_max_length: number
  name_pattern: string
}

export interface FreeDomainProvider {
  id: string
  name: string
  dashboard_url: string
  suffixes: string[]
  eligibility: string[]
  steps: string[]
}

export interface DomainAvailability {
  domain: string
  available: boolean
  registrar: string
  /** First-year price in micro-USD as quoted by the registrar; null when the registrar did not quote one. */
  price_micros: number | null
  renewal_price_micros: number | null
  cost_kind?: CostKind | null
}

export interface DomainRegistrationPreviewRequest {
  registry_id: string
  /** The label, without the suffix. */
  name: string
}

/** Exactly what the AI Co-founder will submit if the Founder approves. */
export interface DomainRegistrationPreview {
  fqdn: string
  repo: string
  branch: string | null
  file_path: string
  file_content: string
  pr_title: string
  pr_body: string
  /** The GitHub login the PR is published under (the connected account), when known. */
  publishes_as: string | null
  eligible: boolean
  ineligible_reason: string | null
  warnings: string[]
}

export type DomainRegistrationCreate =
  | { path: 'github_registry'; registry_id: string; name: string }
  | { path: 'free_provider'; provider_id: string; domain: string }
  /** `max_price_micros` binds the approval: a higher price at purchase time fails instead of charging. */
  | { path: 'paid_registrar'; registrar: string; domain: string; years: number; max_price_micros: number | null }

export type HostedUiStatus = 'none' | 'deploying' | 'ready' | 'error'

export interface HostedUiDeployment {
  status: HostedUiStatus
  provider: 'vercel'
  project_name: string | null
  url: string | null
  /** The public URL the hosted UI proxies `/api/*` to. */
  proxy_target: string | null
  last_deployed_at: Timestamp | null
  error: string | null
  /** Set when the deploy waits for an approval. */
  approval_id?: Uuid | null
}

export type EmailProviderKind = 'agentmail' | 'cloudflare_resend' | 'gmail' | 'outlook'
export type EmailStatus = 'not_connected' | 'connected' | 'error'

export interface EmailIdentity {
  provider: EmailProviderKind | null
  status: EmailStatus
  domain: string | null
  error: string | null
  /** The footer that tells recipients a message was written by an AI (M6; default on). */
  ai_disclosure_footer: boolean
}
export interface EmailIdentityUpdate {
  provider?: EmailProviderKind | null
  ai_disclosure_footer?: boolean
}

export type MailboxStatus = 'active' | 'pending' | 'error' | 'disabled'

export interface Mailbox {
  id: Uuid
  company_id: Uuid
  address: string
  display_name: string | null
  owner_agent_id: Uuid | null
  provider: EmailProviderKind | (string & {})
  status: MailboxStatus
  inbound_count: number
  outbound_count: number
  last_inbound_at: Timestamp | null
  last_outbound_at: Timestamp | null
  created_at: Timestamp
}
export interface MailboxCreate {
  /** The part before the @. */
  local_part: string
  owner_agent_id: Uuid
  display_name?: string | null
}

export type ConnectedProvider = 'cloudflare' | 'vercel' | 'github' | 'agentmail' | 'resend' | 'tailscale' | 'porkbun' | 'namecheap' | 'gmail' | 'outlook'
export type ConnectedAccountStatus = 'connected' | 'invalid' | 'revoked'

/** Metadata only: no response ever carries a token. */
export interface ConnectedAccount {
  id: Uuid
  company_id: Uuid
  provider: ConnectedProvider | (string & {})
  label: string
  credential_id: Uuid | null
  scopes: string[]
  status: ConnectedAccountStatus
  last_checked_at: Timestamp | null
  /** Non-secret facts the provider reported: account name, account id, team. */
  metadata: Record<string, string>
  created_at: Timestamp
}
export interface ConnectedAccountCreate {
  provider: ConnectedProvider
  label: string
  /** The main secret, stored in the M3b credential store first. */
  credential_id: Uuid
  /** More secrets by field key (e.g. Porkbun's secret API key). */
  extra_credential_ids?: Record<string, Uuid>
  /** Non-secret fields (account id, team id, tailnet). */
  metadata?: Record<string, string>
}

export interface ProviderCheck {
  ok: boolean
  message: string | null
  checked_at: Timestamp | null
  account_label: string | null
  scopes: string[]
}
