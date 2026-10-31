import { useMeetings } from '@/api'
import { Badge, Boundary, Card } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { formatRelative } from '@/lib/format'
import { MEETING_STATUS_LABEL, MEETING_STATUS_TONE, participantNames, roundLabel } from '@/lib/meetings'

const RECENT = 10

/** The agent panel's "Meetings" tab: the agent's 10 most recent meetings (as organizer or participant). */
export function AgentMeetings({ companyId, agentId }: { companyId: string; agentId: string }) {
  const { openPanel } = useCanvas()
  const q = useMeetings(companyId, { agentId, limit: RECENT })
  return (
    <Card>
      <Boundary
        query={q}
        milestone="M21"
        compact={false}
        unavailableTitle="Meetings are not built yet"
        unavailableText="Agent meetings arrive with M21."
        isEmpty={(d) => d.length === 0}
        empty={{ title: 'No meetings yet', description: 'When this employee meets other employees to plan or decide something, the meeting is listed here.' }}
      >
        {(meetings) => (
          <ul aria-label="Recent meetings" className="divide-y divide-line">
            {meetings.slice(0, RECENT).map((m) => (
              <li key={m.id}>
                <button type="button" onClick={() => openPanel({ kind: 'meeting', id: m.id })} className="flex w-full items-start justify-between gap-3 px-4 py-2.5 text-left transition-colors hover:bg-hover">
                  <span className="min-w-0">
                    <span className="block text-[13px] font-medium break-words text-fg">{m.title}</span>
                    <span className="block text-[12px] text-fg-3">
                      {participantNames(m)} · {formatRelative(m.ended_at ?? m.started_at ?? m.start_at)}
                      {m.status === 'running' ? ` · ${roundLabel(m)}` : ''}
                    </span>
                  </span>
                  <Badge tone={MEETING_STATUS_TONE[m.status]}>{MEETING_STATUS_LABEL[m.status]}</Badge>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Boundary>
    </Card>
  )
}

