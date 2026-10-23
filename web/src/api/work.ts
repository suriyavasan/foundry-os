import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { mergeRunEvents, lastSeq } from '@/lib/runEvents'
import { isRunActive } from '@/lib/taskStatus'
import { normalizeUsage } from '@/lib/usage'
import { ApiError, API_BASE, apiFetch, fetchAllPages } from './client'
import { qk } from './keys'
import type {
  Approval,
  ApprovalDecision,
  ApprovalFilters,
  Artifact,
  ArtifactFilters,
  Company,
  CompanyPolicy,
  CreateGoalRequest,
  CreateProjectRequest,
  CreateTaskRequest,
  Decision,
  Goal,
  Page,
  Project,
  RunEvent,
  Schedule,
  Task,
  TaskAction,
  TaskFilters,
  TaskRun,
  UpdateGoalRequest,
  UpdatePolicyRequest,
  UpdateProjectRequest,
  UpdateTaskRequest,
  UsageGroupBy,
  UsageReport,
} from './types'

/**
 * An endpoint the backend does not serve yet (M2 not deployed): 404/405 on a collection path.
 * Widgets show an explicit "not built yet" state for these instead of an error or fake data.
 */
export function isNotDeployed(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 404 || err.status === 405)
}

const co = (cid: string | undefined) => `/companies/${cid}`

/* ---------------------------------- policy --------------------------------- */

export function usePolicy(cid: string | undefined) {
  return useQuery({
    queryKey: qk.policy(cid ?? ''),
    queryFn: ({ signal }) => apiFetch<CompanyPolicy>(`${co(cid)}/policy`, { signal }),
    enabled: !!cid,
    retry: (n, e) => !isNotDeployed(e) && n < 2,
  })
}

export function useUpdatePolicy(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: UpdatePolicyRequest) => apiFetch<CompanyPolicy>(`${co(cid)}/policy`, { method: 'PATCH', body }),
    onSuccess: async (policy) => {
      qc.setQueryData(qk.policy(cid), policy)
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.activityAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.usageAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.tasksAll(cid) }),
      ])
    },
  })
}

/* ------------------------------ goals & projects --------------------------- */

export function useGoals(cid: string | undefined) {
  return useQuery({
    queryKey: qk.goals(cid ?? ''),
    queryFn: ({ signal }) => fetchAllPages<Goal>(`${co(cid)}/goals`, {}, signal),
    enabled: !!cid,
    retry: (n, e) => !isNotDeployed(e) && n < 2,
  })
}

export function useCreateGoal(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateGoalRequest) => apiFetch<Goal>(`${co(cid)}/goals`, { method: 'POST', body }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: qk.goals(cid) }), qc.invalidateQueries({ queryKey: qk.activityAll(cid) })]),
  })
}

export function useUpdateGoal(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: UpdateGoalRequest & { id: string }) => apiFetch<Goal>(`${co(cid)}/goals/${id}`, { method: 'PATCH', body }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: qk.goals(cid) }), qc.invalidateQueries({ queryKey: qk.activityAll(cid) })]),
  })
}

export function useProjects(cid: string | undefined) {
  return useQuery({
    queryKey: qk.projects(cid ?? ''),
    queryFn: ({ signal }) => fetchAllPages<Project>(`${co(cid)}/projects`, {}, signal),
    enabled: !!cid,
    retry: (n, e) => !isNotDeployed(e) && n < 2,
  })
}

export function useCreateProject(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateProjectRequest) => apiFetch<Project>(`${co(cid)}/projects`, { method: 'POST', body }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: qk.projects(cid) }), qc.invalidateQueries({ queryKey: qk.activityAll(cid) })]),
  })
}

export function useUpdateProject(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: UpdateProjectRequest & { id: string }) => apiFetch<Project>(`${co(cid)}/projects/${id}`, { method: 'PATCH', body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.projects(cid) }),
  })
}

/* ---------------------------------- tasks ---------------------------------- */

/** Every task (bounded). Shared by the Work and Goals widgets and the command palette. */
export function useAllTasks(cid: string | undefined) {
  return useQuery({
    queryKey: qk.taskEvery(cid ?? ''),
    queryFn: ({ signal }) => fetchAllPages<Task>(`${co(cid)}/tasks`, {}, signal, 10),
    enabled: !!cid,
    retry: (n, e) => !isNotDeployed(e) && n < 2,
  })
}

export function useTasks(cid: string | undefined, filters: TaskFilters = {}) {
  return useQuery({
    queryKey: qk.taskList(cid ?? '', filters),
    queryFn: ({ signal }) => fetchAllPages<Task>(`${co(cid)}/tasks`, { ...filters }, signal, 10),
    enabled: !!cid,
    retry: (n, e) => !isNotDeployed(e) && n < 2,
  })
}

export function useTask(cid: string | undefined, id: string | undefined) {
  return useQuery({
    queryKey: qk.task(cid ?? '', id ?? ''),
    queryFn: ({ signal }) => apiFetch<Task>(`${co(cid)}/tasks/${id}`, { signal }),
    enabled: !!cid && !!id,
  })
}

async function invalidateTaskRelated(qc: QueryClient, cid: string) {
  await Promise.all([
    qc.invalidateQueries({ queryKey: qk.tasksAll(cid) }),
    qc.invalidateQueries({ queryKey: qk.runsAll(cid) }),
    qc.invalidateQueries({ queryKey: qk.agents(cid) }),
    qc.invalidateQueries({ queryKey: qk.overview(cid) }),
    qc.invalidateQueries({ queryKey: qk.activityAll(cid) }),
  ])
}

export function useCreateTask(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateTaskRequest) => apiFetch<Task>(`${co(cid)}/tasks`, { method: 'POST', body }),
    onSuccess: () => invalidateTaskRelated(qc, cid),
  })
}

export function useUpdateTask(cid: string, id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: UpdateTaskRequest) => apiFetch<Task>(`${co(cid)}/tasks/${id}`, { method: 'PATCH', body }),
    onSuccess: (task) => {
      qc.setQueryData(qk.task(cid, id), task)
      return invalidateTaskRelated(qc, cid)
    },
  })
}

export function useTaskAction(cid: string, id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ action, reason }: { action: TaskAction; reason?: string }) =>
      apiFetch<Task>(`${co(cid)}/tasks/${id}/${action}`, { method: 'POST', body: reason ? { reason } : {} }),
    onSuccess: (task) => {
      if (task && typeof task === 'object' && 'id' in task) qc.setQueryData(qk.task(cid, id), task)
      return invalidateTaskRelated(qc, cid)
    },
  })
}

/* ----------------------------------- runs ---------------------------------- */

export function useTaskRuns(cid: string | undefined, taskId: string | undefined) {
  return useQuery({
    queryKey: qk.taskRuns(cid ?? '', taskId ?? ''),
    queryFn: ({ signal }) => fetchAllPages<TaskRun>(`${co(cid)}/tasks/${taskId}/runs`, {}, signal, 5),
    enabled: !!cid && !!taskId,
    retry: (n, e) => !isNotDeployed(e) && n < 2,
  })
}

/** A run in flight is re-read every 3 s (the stream also invalidates it); a finished one is not polled. */
export function useRun(cid: string | undefined, runId: string | undefined) {
  return useQuery({
    queryKey: qk.run(cid ?? '', runId ?? ''),
    queryFn: ({ signal }) => apiFetch<TaskRun>(`${co(cid)}/runs/${runId}`, { signal }),
    enabled: !!cid && !!runId,
    refetchInterval: (q) => (q.state.data && isRunActive(q.state.data.status) ? 3000 : false),
  })
}

const EVENT_PAGE_GUARD = 20

/**
 * Run log, tailed with `after_seq`. Each fetch resumes from the last cached seq and merges
 * idempotently, so stream-triggered invalidations and the 2 s poll never duplicate lines.
 */
export function useRunEvents(cid: string | undefined, runId: string | undefined, active: boolean) {
  const qc = useQueryClient()
  return useQuery({
    queryKey: qk.runEvents(cid ?? '', runId ?? ''),
    enabled: !!cid && !!runId,
    refetchInterval: active ? 2000 : false,
    queryFn: async ({ signal }) => {
      let events = qc.getQueryData<RunEvent[]>(qk.runEvents(cid!, runId!)) ?? []
      for (let i = 0; i < EVENT_PAGE_GUARD; i++) {
        const page = await apiFetch<Page<RunEvent>>(`${co(cid)}/runs/${runId}/events`, {
          query: { after_seq: lastSeq(events), limit: 200 },
          signal,
        })
        if (page.items.length === 0) break
        events = mergeRunEvents(events, page.items)
        if (!page.next_cursor) break
      }
      return events
    },
  })
}

export function useCancelRun(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) => apiFetch<TaskRun>(`${co(cid)}/runs/${runId}/cancel`, { method: 'POST', body: {} }),
    onSuccess: () => invalidateTaskRelated(qc, cid),
  })
}

/* -------------------------------- artifacts -------------------------------- */

export function useArtifacts(cid: string | undefined, filters: ArtifactFilters = {}, limit = 200) {
  return useQuery({
    queryKey: [...qk.artifactList(cid ?? '', filters), limit],
    queryFn: ({ signal }) => apiFetch<Page<Artifact>>(`${co(cid)}/artifacts`, { query: { ...filters, limit }, signal }),
    enabled: !!cid,
    retry: (n, e) => !isNotDeployed(e) && n < 2,
  })
}

export function useArtifact(cid: string | undefined, id: string | undefined) {
  return useQuery({
    queryKey: qk.artifact(cid ?? '', id ?? ''),
    queryFn: ({ signal }) => apiFetch<Artifact>(`${co(cid)}/artifacts/${id}`, { signal }),
    enabled: !!cid && !!id,
  })
}

export function artifactContentUrl(cid: string, id: string, version: number): string {
  return `${API_BASE}${co(cid)}/artifacts/${id}/versions/${version}/content`
}

export const PREVIEW_MAX_BYTES = 512 * 1024

/** Fetch a text artifact for inline preview. Throws ApiError on non-2xx. */
export function useArtifactText(cid: string, id: string, version: number, enabled: boolean) {
  return useQuery({
    queryKey: ['company', cid, 'artifacts', 'text', id, version] as const,
    enabled,
    queryFn: async ({ signal }) => {
      const res = await fetch(artifactContentUrl(cid, id, version), { signal })
      if (!res.ok) throw new ApiError(res.status, 'bad_request', `Could not load content (${res.status})`)
      const text = await res.text()
      return text.length > PREVIEW_MAX_BYTES ? { text: text.slice(0, PREVIEW_MAX_BYTES), truncated: true } : { text, truncated: false }
    },
  })
}

/* -------------------------------- approvals -------------------------------- */

export function useApprovals(cid: string | undefined, filters: ApprovalFilters = {}) {
  return useQuery({
    queryKey: qk.approvalList(cid ?? '', filters),
    queryFn: ({ signal }) => fetchAllPages<Approval>(`${co(cid)}/approvals`, { ...filters }, signal, 5),
    enabled: !!cid,
    retry: (n, e) => !isNotDeployed(e) && n < 2,
  })
}

const DECISION_PATH: Record<ApprovalDecision, string> = {
  approve: 'approve',
  reject: 'reject',
  request_changes: 'request_changes',
}

export function useDecideApproval(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, decision, note }: { id: string; decision: ApprovalDecision; note?: string }) =>
      apiFetch<Approval>(`${co(cid)}/approvals/${id}/${DECISION_PATH[decision]}`, { method: 'POST', body: { note: note ?? '' } }),
    onSuccess: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: qk.approvalsAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.tasksAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.activityAll(cid) }),
      ]),
  })
}

/* ------------------------- decisions, schedules, usage ---------------------- */

export function useDecisions(cid: string | undefined) {
  return useQuery({
    queryKey: qk.decisions(cid ?? ''),
    queryFn: ({ signal }) => apiFetch<Page<Decision>>(`${co(cid)}/decisions`, { query: { limit: 50 }, signal }),
    enabled: !!cid,
    retry: (n, e) => !isNotDeployed(e) && n < 2,
  })
}

export function useSchedules(cid: string | undefined) {
  return useQuery({
    queryKey: qk.schedules(cid ?? ''),
    queryFn: ({ signal }) => fetchAllPages<Schedule>(`${co(cid)}/schedules`, {}, signal, 5),
    enabled: !!cid,
    retry: (n, e) => !isNotDeployed(e) && n < 2,
  })
}

export function useUsage(cid: string | undefined, groupBy: UsageGroupBy, from: string, to: string) {
  return useQuery<UsageReport>({
    queryKey: qk.usage(cid ?? '', groupBy, from, to),
    queryFn: async ({ signal }) =>
      normalizeUsage(await apiFetch<unknown>(`${co(cid)}/usage`, { query: { group_by: groupBy, from, to }, signal })),
    enabled: !!cid,
    retry: (n, e) => !isNotDeployed(e) && n < 2,
  })
}

/* ------------------------------ company kill switch ------------------------- */

/** Pause/resume the whole company (kill switch: pause cancels active runs and stops dispatch). */
export function useCompanyControl(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (action: 'pause' | 'resume'): Promise<Company> =>
      apiFetch<Company>(`${co(cid)}/${action}`, { method: 'POST', body: {} }),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.companyDetail(cid) }),
        qc.invalidateQueries({ queryKey: qk.companies }),
        qc.invalidateQueries({ queryKey: qk.me }),
        qc.invalidateQueries({ queryKey: qk.overview(cid) }),
        qc.invalidateQueries({ queryKey: qk.tasksAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.runsAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.agents(cid) }),
        qc.invalidateQueries({ queryKey: qk.activityAll(cid) }),
      ])
    },
  })
}

/** Infinite paging helper kept for the full task table in the Tasks panel. */
export function useTasksInfinite(cid: string | undefined, filters: TaskFilters = {}, pageSize = 50) {
  return useInfiniteQuery({
    queryKey: [...qk.taskList(cid ?? '', filters), 'infinite', pageSize],
    enabled: !!cid,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      apiFetch<Page<Task>>(`${co(cid)}/tasks`, { query: { ...filters, limit: pageSize, cursor: pageParam }, signal }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    retry: (n, e) => !isNotDeployed(e) && n < 2,
  })
}

/* ------------------------------- dependencies ------------------------------- */

export function useAddDependency(cid: string, taskId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (dependsOnTaskId: string) =>
      apiFetch<unknown>(`${co(cid)}/tasks/${taskId}/dependencies`, { method: 'POST', body: { depends_on_task_id: dependsOnTaskId } }),
    onSuccess: () => invalidateTaskRelated(qc, cid),
  })
}

export function useRemoveDependency(cid: string, taskId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (depId: string) => apiFetch<unknown>(`${co(cid)}/tasks/${taskId}/dependencies/${depId}`, { method: 'DELETE' }),
    onSuccess: () => invalidateTaskRelated(qc, cid),
  })
}
