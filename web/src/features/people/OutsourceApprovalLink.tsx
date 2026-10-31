import { Briefcase } from 'lucide-react'
import type { Approval } from '@/api/types'
import { useCanvas } from '@/features/canvas/CanvasContext'

/**
 * On an `outsource_job` approval (M22): a link to the freelance job it is about, where the full brief is. Nothing is posted or paid by
 * approving it: a person does that on the marketplace and records it on the job.
 */
export function OutsourceApprovalLink({ approval }: { approval: Pick<Approval, 'subject_type' | 'subject_id' | 'kind'> }) {
  const { openPanel } = useCanvas()
  if (approval.kind !== 'outsource_job' || approval.subject_type !== 'outsource_job' || !approval.subject_id) return null
  const id = approval.subject_id
  return (
    <p className="mt-1.5">
      <button type="button" onClick={() => openPanel({ kind: 'outsource', id })} className="inline-flex items-center gap-1.5 text-[12.5px] text-accent-fg hover:underline">
        <Briefcase className="size-3.5" aria-hidden /> Open the freelance job
      </button>
    </p>
  )
}
