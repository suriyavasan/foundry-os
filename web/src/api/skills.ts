import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { ApiError, apiFetch, fetchAllPages } from './client'
import { qk } from './keys'
import type {
  AgentSkill,
  AssignSkillsRequest,
  CatalogItem,
  CatalogSource,
  InstallSkillRequest,
  InstallSkillResult,
  Page,
  RoleTemplate,
  Skill,
  SkillDetail,
  SkillFileContent,
} from './types'

const co = (cid: string | undefined) => `/companies/${cid}`

/** Skills not deployed (backend older than M4): 404/405 on the collection. Same rule as the work engine. */
const noRetryWhenMissing = (n: number, e: Error) => !(e instanceof ApiError && (e.status === 404 || e.status === 405)) && n < 2

/* --------------------------------- library --------------------------------- */

/** The whole library (the UI filters client-side; libraries are tens to low hundreds of skills). */
export function useSkills(cid: string | undefined) {
  return useQuery({
    queryKey: qk.skillList(cid ?? ''),
    queryFn: ({ signal }) => fetchAllPages<Skill>(`${co(cid)}/skills`, {}, signal),
    enabled: !!cid,
    retry: noRetryWhenMissing,
  })
}

export function useSkill(cid: string | undefined, id: string | undefined) {
  return useQuery({
    queryKey: qk.skill(cid ?? '', id ?? ''),
    queryFn: ({ signal }) => apiFetch<SkillDetail>(`${co(cid)}/skills/${id}`, { signal }),
    enabled: !!cid && !!id,
  })
}

/** Text preview of one skill file. `path` is encoded per segment; the server rejects traversal. */
export function useSkillFile(cid: string | undefined, id: string | undefined, path: string | null) {
  return useQuery({
    queryKey: qk.skillFile(cid ?? '', id ?? '', path ?? ''),
    queryFn: ({ signal }) =>
      apiFetch<SkillFileContent>(`${co(cid)}/skills/${id}/files/${(path ?? '').split('/').map(encodeURIComponent).join('/')}`, { signal }),
    enabled: !!cid && !!id && !!path,
    staleTime: 60_000,
  })
}

export function skillFilePathOk(path: string): boolean {
  return path !== '' && !path.startsWith('/') && !path.split('/').some((seg) => seg === '..' || seg === '')
}

/* --------------------------------- catalog --------------------------------- */

export const CATALOG_PAGE_SIZE = 30

/** Cursor is an offset (docs/api.md). Disabled until there is something to search or the source lists on its own. */
export function useSkillCatalog(cid: string | undefined, q: string, source: CatalogSource, enabled = true) {
  return useInfiniteQuery({
    queryKey: qk.skillCatalog(cid ?? '', q, source),
    enabled: !!cid && enabled,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      apiFetch<Page<CatalogItem>>(`${co(cid)}/skills/catalog`, {
        query: { q, source, limit: CATALOG_PAGE_SIZE, cursor: pageParam },
        signal,
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    retry: (n, e) => !(e instanceof ApiError && e.status >= 400 && e.status < 600) && n < 1,
    staleTime: 60_000,
  })
}

function invalidateSkills(qc: QueryClient, cid: string) {
  return Promise.all([
    qc.invalidateQueries({ queryKey: qk.skillsAll(cid) }),
    qc.invalidateQueries({ queryKey: qk.activityAll(cid) }),
  ])
}

export function useInstallSkill(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: InstallSkillRequest) => apiFetch<InstallSkillResult>(`${co(cid)}/skills/install`, { method: 'POST', body }),
    onSuccess: () => invalidateSkills(qc, cid),
  })
}

export function useSeedSkills(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiFetch<{ job_id: string | null; status: string }>(`${co(cid)}/skills/seed`, { method: 'POST' }),
    onSuccess: () => invalidateSkills(qc, cid),
  })
}

export function useSkillAction(cid: string, id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ action, note }: { action: 'archive' | 'unquarantine'; note?: string }) =>
      apiFetch<Skill>(`${co(cid)}/skills/${id}/${action}`, { method: 'POST', body: note ? { note } : {} }),
    onSuccess: () => invalidateSkills(qc, cid),
  })
}

/* ------------------------------ agent <-> skills ---------------------------- */

export function useAgentSkills(cid: string | undefined, agentId: string | undefined) {
  return useQuery({
    queryKey: qk.agentSkills(cid ?? '', agentId ?? ''),
    queryFn: ({ signal }) => apiFetch<{ items: AgentSkill[] }>(`${co(cid)}/agents/${agentId}/skills`, { signal }),
    enabled: !!cid && !!agentId,
    select: (d) => d.items,
    retry: noRetryWhenMissing,
  })
}

export function useAssignSkills(cid: string, agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: AssignSkillsRequest) => apiFetch<{ items: AgentSkill[] }>(`${co(cid)}/agents/${agentId}/skills`, { method: 'POST', body }),
    onSuccess: () => invalidateSkills(qc, cid),
  })
}

export function useRemoveAgentSkill(cid: string, agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (skillRef: string) => apiFetch<void>(`${co(cid)}/agents/${agentId}/skills/${encodeURIComponent(skillRef)}`, { method: 'DELETE' }),
    onSuccess: () => invalidateSkills(qc, cid),
  })
}

/** Remove a skill from an agent without a hook (used by the hire dialog to drop deselected defaults). */
export function removeAgentSkill(cid: string, agentId: string, skillRef: string): Promise<void> {
  return apiFetch<void>(`${co(cid)}/agents/${agentId}/skills/${encodeURIComponent(skillRef)}`, { method: 'DELETE' })
}

/** One `GET /agents/{id}/skills` per agent, used for the "assigned agents" count in the library table. */
export function fetchAgentSkills(cid: string, agentId: string, signal?: AbortSignal) {
  return apiFetch<{ items: AgentSkill[] }>(`${co(cid)}/agents/${agentId}/skills`, { signal })
}

/* ------------------------------- role templates ----------------------------- */

export function useRoleTemplates(cid: string | undefined) {
  return useQuery({
    queryKey: qk.roleTemplates(cid ?? ''),
    queryFn: ({ signal }) => fetchAllPages<RoleTemplate>(`${co(cid)}/role-templates`, {}, signal),
    enabled: !!cid,
    staleTime: 5 * 60_000,
    retry: noRetryWhenMissing,
  })
}
