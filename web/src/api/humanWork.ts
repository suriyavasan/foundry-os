import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './client'
import { qk } from './keys'
import type { HumanWorkItem, OutsourceJob, OutsourceStatus, Page } from './types'
import { isNotDeployed } from './work'

/**
 * M22 human work and outsourcing through HR. An AI employee that needs a person (a signature, a call, KYC, an errand) asks for it;
 * the request is a task for one member or for the human pool. HR can also draft a freelance job: a person approves it, posts it on
 * the marketplace's own site and records the steps here. Foundry never posts, messages, hires or pays on a marketplace itself.
 *
 *   GET  /companies/{cid}/human-work[?status=open|done&mine=]     Page<HumanWorkItem>   (overdue first)
 *   POST /companies/{cid}/human-work/{id}/claim                   take it from the pool (409 when somebody else did)
 *   POST /companies/{cid}/human-work/{id}/decline {reason}        owner/admin; the agent is told why
 *   (completing: the existing POST /tasks/{id}/complete, see `useHumanTaskAction`)
 *   GET  /companies/{cid}/outsource-jobs[?status=]                Page<OutsourceJob>
 *   GET  /companies/{cid}/outsource-jobs/{id}                     OutsourceJob
 *   POST .../{id}/posted {listing_url} | hired {...} | delivered {...} | paid {amount_usd} | close | cancel {reason?} | raise-budget {...}
 *   SSE  human_work.created / human_work.updated / outsource.updated (ids only)
 *
 * Text written by an AI employee (titles, instructions, briefs) is plain text, never markup.
 */
const co = (cid: string | undefined) => `/companies/${cid}`
const retry = (n: number, e: Error) => !isNotDeployed(e) && n < 2

/** Human work of the company: `open` (overdue first) or `done`; `mine` keeps what is assigned to the caller. */
export function useHumanWork(cid: string | undefined, opts: { status?: 'open' | 'done'; mine?: boolean; enabled?: boolean } = {}) {
  const { status = 'open', mine = false, enabled = true } = opts
  return useQuery({
    queryKey: qk.humanWork(cid ?? '', status, mine),
    enabled: !!cid && enabled,
    retry,
    queryFn: ({ signal }) => apiFetch<Page<HumanWorkItem>>(`${co(cid)}/human-work`, { query: { status, mine: mine || undefined, limit: 100 }, signal }),
    select: (page) => page.items,
  })
}

function useInvalidateHumanWork(cid: string) {
  const qc = useQueryClient()
  return () => {
    void qc.invalidateQueries({ queryKey: qk.humanWorkAll(cid) })
    void qc.invalidateQueries({ queryKey: qk.tasksAll(cid) })
    void qc.invalidateQueries({ queryKey: qk.activityAll(cid) })
  }
}

/** Take a task from the human pool. A 409 means somebody else got there first. */
export function useClaimHumanWork(cid: string) {
  const done = useInvalidateHumanWork(cid)
  return useMutation({
    mutationFn: (id: string) => apiFetch<HumanWorkItem>(`${co(cid)}/human-work/${id}/claim`, { method: 'POST' }),
    onSettled: done,
  })
}

/** Owner/admin: nobody will do this. The AI employee that asked is woken with the reason. */
export function useDeclineHumanWork(cid: string) {
  const done = useInvalidateHumanWork(cid)
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) => apiFetch<HumanWorkItem>(`${co(cid)}/human-work/${id}/decline`, { method: 'POST', body: { reason } }),
    onSuccess: done,
  })
}

/* ---------------------------------- freelancers ---------------------------------- */

export function useOutsourceJobs(cid: string | undefined, opts: { status?: OutsourceStatus; enabled?: boolean } = {}) {
  const { status, enabled = true } = opts
  return useQuery({
    queryKey: qk.outsourceJobs(cid ?? '', status ?? 'all'),
    enabled: !!cid && enabled,
    retry,
    queryFn: ({ signal }) => apiFetch<Page<OutsourceJob>>(`${co(cid)}/outsource-jobs`, { query: { status, limit: 100 }, signal }),
    select: (page) => page.items,
  })
}

export function useOutsourceJob(cid: string | undefined, id: string | undefined) {
  return useQuery({
    queryKey: qk.outsourceJob(cid ?? '', id ?? ''),
    enabled: !!cid && !!id,
    retry,
    queryFn: ({ signal }) => apiFetch<OutsourceJob>(`${co(cid)}/outsource-jobs/${id}`, { signal }),
  })
}

export type OutsourceStep =
  | { step: 'posted'; body: { listing_url: string } }
  | { step: 'hired'; body: { freelancer_name: string; freelancer_profile_url?: string | null; agreed_price_usd: number } }
  | { step: 'delivered'; body: { artifact_ids: string[]; notes?: string | null } }
  | { step: 'paid'; body: { amount_usd: number } }
  | { step: 'close'; body: Record<string, never> }
  | { step: 'cancel'; body: { reason?: string | null } }
  | { step: 'raise-budget'; body: { new_budget_usd: number; reason?: string | null } }

/**
 * Record one step a person did on the marketplace (owner/admin). `raise-budget` creates a new approval and leaves the job's budget as
 * it is until a person approves it; `paid` records a verified expense (Foundry moves no money).
 */
export function useOutsourceStep(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, step, body }: { id: string } & OutsourceStep) => apiFetch<OutsourceJob>(`${co(cid)}/outsource-jobs/${id}/${step}`, { method: 'POST', body }),
    onSuccess: (job) => {
      qc.setQueryData(qk.outsourceJob(cid, job.id), job)
      void qc.invalidateQueries({ queryKey: qk.outsourceAll(cid) })
      void qc.invalidateQueries({ queryKey: qk.approvalsAll(cid) })
      void qc.invalidateQueries({ queryKey: qk.activityAll(cid) })
      void qc.invalidateQueries({ queryKey: qk.financeAll(cid) })
    },
  })
}

/**
 * Record the delivery: an optional file is uploaded first as an artifact (no task), then the step is recorded with its id and notes.
 * The linked task (if the job has one) gets the artifact as an output.
 */
export function useRecordDelivery(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, notes, file }: { id: string; notes: string; file: File | null }) => {
      const artifact_ids: string[] = []
      if (file) {
        const form = new FormData()
        form.append('file', file)
        form.append('title', file.name)
        form.append('kind', 'other')
        const artifact = await apiFetch<{ id: string }>(`${co(cid)}/artifacts`, { method: 'POST', body: form })
        artifact_ids.push(artifact.id)
      }
      return apiFetch<OutsourceJob>(`${co(cid)}/outsource-jobs/${id}/delivered`, { method: 'POST', body: { artifact_ids, notes: notes.trim() || null } })
    },
    onSuccess: (job) => {
      qc.setQueryData(qk.outsourceJob(cid, job.id), job)
      void qc.invalidateQueries({ queryKey: qk.outsourceAll(cid) })
      void qc.invalidateQueries({ queryKey: qk.artifactsAll(cid) })
      void qc.invalidateQueries({ queryKey: qk.tasksAll(cid) })
    },
  })
}
