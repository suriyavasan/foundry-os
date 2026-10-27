import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, apiFetch, fetchAllPages } from './client'
import { qk } from './keys'
import type {
  AuditEvent,
  GithubInspectRequest,
  GithubInspectResult,
  Page,
  ProjectRepository,
  RepoAvailability,
  RepoItem,
  RepoItemKind,
  RepoItemStatus,
  RepoPullRequest,
  RepositoryCreate,
  RepositoryUpdate,
} from './types'
import { isNotDeployed } from './work'

const co = (cid: string | undefined) => `/companies/${cid}`
const retry = (n: number, e: Error) => !isNotDeployed(e) && !(e instanceof ApiError && e.status === 501) && n < 2

/** Statuses in which the server is still working on the binding (a `repo_clone` job): poll until it settles. */
const IN_PROGRESS = new Set(['pending', 'creating', 'cloning'])

/**
 * Whether git and file locking exist on this machine. A 501 `{available:false}` answer is data, not an error: the caller
 * shows "not available" with the reason. A 404 (a backend without M14) stays an error that `isNotDeployed` recognises.
 */
export function useRepoAvailability(cid: string | undefined) {
  return useQuery({
    queryKey: qk.repoStatus(cid ?? ''),
    enabled: !!cid,
    retry,
    staleTime: 60_000,
    queryFn: async ({ signal }): Promise<RepoAvailability> => {
      try {
        return await apiFetch<RepoAvailability>(`${co(cid)}/repositories/status`, { signal })
      } catch (err) {
        if (err instanceof ApiError && err.status === 501) return { available: false, reason: err.message, git_version: null, milestone: 'M14' }
        throw err
      }
    },
  })
}

/** Every repository of the company (or of one project). */
export function useRepositories(cid: string | undefined, projectId?: string) {
  return useQuery<ProjectRepository[]>({
    queryKey: [...qk.repoList(cid ?? ''), projectId ?? 'all'],
    enabled: !!cid,
    retry,
    // A binding that is still cloning changes without any user action.
    refetchInterval: (q) => (q.state.data?.some((r) => IN_PROGRESS.has(r.status)) ? 3000 : false),
    queryFn: ({ signal }): Promise<ProjectRepository[]> => fetchAllPages<ProjectRepository>(`${co(cid)}/repositories`, { project_id: projectId }, signal),
  })
}

/** One binding. While it is being created or cloned the query re-reads itself every two seconds (`poll`). */
export function useRepository(cid: string | undefined, id: string | null | undefined, opts: { poll?: boolean } = {}) {
  return useQuery<ProjectRepository>({
    queryKey: qk.repo(cid ?? '', id ?? ''),
    enabled: !!cid && !!id,
    retry,
    refetchInterval: (q) => (opts.poll !== false && q.state.data && IN_PROGRESS.has(q.state.data.status) ? 2000 : false),
    queryFn: ({ signal }): Promise<ProjectRepository> => apiFetch<ProjectRepository>(`${co(cid)}/repositories/${id}`, { signal }),
  })
}

export function useRepoPulls(cid: string | undefined, id: string | undefined) {
  return useQuery({
    queryKey: qk.repoPulls(cid ?? '', id ?? ''),
    enabled: !!cid && !!id,
    retry,
    queryFn: ({ signal }) => fetchAllPages<RepoPullRequest>(`${co(cid)}/repositories/${id}/pull-requests`, {}, signal, 3),
  })
}

/** Issues, reviews, CI failures and alerts GitHub gave us. Bodies are clipped excerpts and untrusted. */
export function useRepoItems(cid: string | undefined, id: string | undefined, filters: { kind?: RepoItemKind; status?: RepoItemStatus } = {}) {
  return useQuery({
    queryKey: qk.repoItems(cid ?? '', id ?? '', filters.kind ?? '', filters.status ?? ''),
    enabled: !!cid && !!id,
    retry,
    queryFn: ({ signal }) => fetchAllPages<RepoItem>(`${co(cid)}/repositories/${id}/items`, { kind: filters.kind, status: filters.status }, signal, 3),
  })
}

/** Audit events of one repository, newest first. */
export function useRepoActivity(cid: string | undefined, id: string | undefined) {
  return useQuery({
    queryKey: qk.repoActivity(cid ?? '', id ?? ''),
    enabled: !!cid && !!id,
    retry,
    queryFn: async ({ signal }): Promise<AuditEvent[]> => (await apiFetch<Page<AuditEvent>>(`${co(cid)}/repositories/${id}/activity`, { query: { limit: 50 }, signal })).items,
  })
}

/* -------------------------------------- mutations -------------------------------------- */

function useRefreshRepos(cid: string) {
  const qc = useQueryClient()
  return () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: qk.reposAll(cid) }),
      qc.invalidateQueries({ queryKey: qk.projects(cid) }),
      qc.invalidateQueries({ queryKey: qk.activityAll(cid) }),
    ])
}

/**
 * What the token can do (and, with `full_name`, what it can do on one repository). Never carries the token. `gcTime: 0`
 * keeps nothing about the call in the cache.
 */
export function useInspectGithub(cid: string) {
  return useMutation({
    gcTime: 0,
    mutationFn: (body: GithubInspectRequest) => apiFetch<GithubInspectResult>(`${co(cid)}/github/inspect`, { method: 'POST', body }),
  })
}

export function useCreateRepository(cid: string) {
  const refresh = useRefreshRepos(cid)
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: RepositoryCreate) => apiFetch<ProjectRepository>(`${co(cid)}/repositories`, { method: 'POST', body }),
    onSuccess: (repo) => {
      qc.setQueryData(qk.repo(cid, repo.id), repo)
      return Promise.all([refresh(), qc.invalidateQueries({ queryKey: qk.connectedAccounts(cid) })])
    },
  })
}

export function useUpdateRepository(cid: string, id: string) {
  const refresh = useRefreshRepos(cid)
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: RepositoryUpdate) => apiFetch<ProjectRepository>(`${co(cid)}/repositories/${id}`, { method: 'PATCH', body }),
    onSuccess: (repo) => {
      qc.setQueryData(qk.repo(cid, id), repo)
      return refresh()
    },
  })
}

export type RepoAction = 'sync' | 'pause' | 'resume' | 'pull-requests/resume-opening'

/** Sync now (202 `{queued}`), pause, resume, resume opening pull requests. */
export function useRepoAction(cid: string, id: string) {
  const refresh = useRefreshRepos(cid)
  return useMutation({
    mutationFn: (action: RepoAction) => apiFetch<unknown>(`${co(cid)}/repositories/${id}/${action}`, { method: 'POST', body: {} }),
    onSuccess: () => refresh(),
  })
}

/** 409 `repository_busy` while a Foundry pull request is merging or a run of the project is active. */
export function useDisconnectRepository(cid: string, id: string) {
  const refresh = useRefreshRepos(cid)
  return useMutation({
    mutationFn: () => apiFetch<ProjectRepository>(`${co(cid)}/repositories/${id}/disconnect`, { method: 'POST', body: { confirm: true } }),
    onSuccess: () => refresh(),
  })
}

export function useResetIntegration(cid: string, id: string) {
  const refresh = useRefreshRepos(cid)
  return useMutation({
    mutationFn: (to: 'remote_work_branch' | 'default_branch') => apiFetch<ProjectRepository>(`${co(cid)}/repositories/${id}/integration/reset`, { method: 'POST', body: { to, confirm: true } }),
    onSuccess: () => refresh(),
  })
}

/** 409 `public_url_ephemeral` on a quick tunnel; 409 `webhook_permission_missing` when the token cannot manage hooks. */
export function useSetWebhook(cid: string, id: string) {
  const refresh = useRefreshRepos(cid)
  return useMutation({
    mutationFn: (enabled: boolean) => apiFetch<ProjectRepository>(`${co(cid)}/repositories/${id}/webhook`, { method: 'PUT', body: { enabled } }),
    onSuccess: () => refresh(),
  })
}
