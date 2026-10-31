import type { Meeting, MeetingDetail, MeetingMessage, MeetingParticipant } from '@/api/types'

export const participant = (over: Partial<MeetingParticipant> & { agent_id: string; name: string }): MeetingParticipant => ({
  role_title: null,
  role: 'participant',
  position: 1,
  joined: true,
  passes: 0,
  ...over,
})

export const ana = participant({ agent_id: 'f1', name: 'Atlas', role: 'organizer', position: 0, role_title: 'Founder & CEO' })
export const raj = participant({ agent_id: 'a2', name: 'Iris', position: 1, role_title: 'Market Researcher' })

export function makeMeeting(over: Partial<Meeting> = {}): Meeting {
  return {
    id: 'm1',
    company_id: 'c1',
    organizer_agent_id: 'f1',
    title: 'Plan the launch',
    agenda: 'Decide the launch date and who owns what.',
    expected_outcome: 'A date and an owner for each part.',
    project_id: null,
    task_id: null,
    status: 'running',
    start_at: '2026-10-30T09:00:00Z',
    started_at: '2026-10-30T09:00:05Z',
    ended_at: null,
    round: 2,
    max_rounds: 2,
    summary: null,
    cancel_reason: null,
    participants: [ana, raj],
    created_at: '2026-10-30T08:59:00Z',
    updated_at: '2026-10-30T09:05:00Z',
    ...over,
  }
}

export const message = (over: Partial<MeetingMessage> & { id: string }): MeetingMessage => ({
  agent_id: 'f1',
  agent_name: 'Atlas',
  round: 1,
  kind: 'turn',
  body: 'Hello',
  proposals: {},
  created_at: '2026-10-30T09:01:00Z',
  ...over,
})

export function makeMeetingDetail(over: Partial<MeetingDetail> = {}): MeetingDetail {
  return { ...makeMeeting(), outcome: {}, messages: [], ...over }
}
