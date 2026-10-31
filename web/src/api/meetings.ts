import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { isNotDeployed } from './work'
import { apiFetch } from './client'
import { qk } from './keys'
import type { Meeting, MeetingDetail, MeetingStatus, Page } from './types'

/**
 * M21 agent meetings: AI employees meet without routing through the Co-founder; people read the transcript and outcome, and an
 * owner/admin can cancel one that has not ended. Types are generated from the OpenAPI schema.
 *
 *   GET  /companies/{cid}/meetings[?status=&agent_id=&limit=]   Page<Meeting>   (newest first)
 *   GET  /companies/{cid}/meetings/{id}                         MeetingDetail    (participants, messages, outcome)
 *   POST /companies/{cid}/meetings/{id}/cancel {reason?}        MeetingDetail    (owner/admin; 409 once ended)
 *   SSE  meeting.updated {meeting_id, status, round, max_rounds, organizer_agent_id, participant_agent_ids}
 *
 * Everything said in a meeting is written by AI employees: render it as plain text or markdown, never as HTML.
 */
const co = (cid: string | undefined) => `/companies/${cid}`
const retry = (n: number, e: Error) => !isNotDeployed(e) && n < 2

/** Meetings of the company, optionally one status and/or one participating agent. */
export function useMeetings(cid: string | undefined, opts: { status?: MeetingStatus; agentId?: string; limit?: number; enabled?: boolean } = {}) {
  const { status, agentId, limit = 20, enabled = true } = opts
  return useQuery({
    queryKey: qk.meetings(cid ?? '', status ?? 'all', agentId ?? ''),
    enabled: !!cid && enabled,
    retry,
    queryFn: ({ signal }) => apiFetch<Page<Meeting>>(`${co(cid)}/meetings`, { query: { status, agent_id: agentId, limit }, signal }),
    select: (page) => page.items,
  })
}

/** The meetings that are running right now (live activity and the org map). A backend without meetings simply yields none. */
export function useRunningMeetings(cid: string | undefined) {
  return useMeetings(cid, { status: 'running', limit: 20 })
}

export function useMeeting(cid: string | undefined, id: string | undefined) {
  return useQuery({
    queryKey: qk.meeting(cid ?? '', id ?? ''),
    enabled: !!cid && !!id,
    retry,
    queryFn: ({ signal }) => apiFetch<MeetingDetail>(`${co(cid)}/meetings/${id}`, { signal }),
  })
}

/** Owner/admin only. Stops a meeting that has not ended; the reason is stored with it. */
export function useCancelMeeting(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) => apiFetch<MeetingDetail>(`${co(cid)}/meetings/${id}/cancel`, { method: 'POST', body: { reason: reason ?? '' } }),
    onSuccess: (detail) => {
      qc.setQueryData(qk.meeting(cid, detail.id), detail)
      void qc.invalidateQueries({ queryKey: qk.meetingsAll(cid) })
      void qc.invalidateQueries({ queryKey: qk.tasksAll(cid) })
      void qc.invalidateQueries({ queryKey: qk.agents(cid) })
    },
  })
}
