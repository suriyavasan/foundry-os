import { Users } from 'lucide-react'
import { useRunningMeetings } from '@/api'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { meetingLine } from '@/lib/meetings'

/**
 * The meetings that are running right now, one line each ("Meeting · Ana, Raj, Lee · Plan the launch · round 2/2"), pinned above
 * the live feed. A click opens the meeting. Nothing is drawn when none is running or the backend has no meetings.
 */
export function RunningMeetings() {
  const { companyId, openPanel } = useCanvas()
  const q = useRunningMeetings(companyId)
  const items = q.data ?? []
  if (items.length === 0) return null
  return (
    <ul aria-label="Meetings in progress" className="divide-y divide-line border-b border-line bg-accent-subtle/40">
      {items.map((m) => (
        <li key={m.id}>
          <button type="button" onClick={() => openPanel({ kind: 'meeting', id: m.id })} className="flex w-full items-start gap-2.5 px-3.5 py-2 text-left text-[12.5px] leading-snug text-fg transition-colors hover:bg-hover">
            <Users className="mt-0.5 size-3.5 shrink-0 text-accent-fg" aria-hidden />
            <span className="min-w-0 flex-1 break-words">{meetingLine(m)}</span>
          </button>
        </li>
      ))}
    </ul>
  )
}
