import { Sparkles } from 'lucide-react'
import { useAiSetup } from '@/api'
import { blockingReasonText } from '@/lib/aiSetup'
import { Badge, Button, Card, CardBody, CardHeader } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'

/** Settings entry to the AI panel: the current model and work mode in one line. Not drawn until the backend serves the AI setup. */
export function AiLinkCard() {
  const { companyId, openPanel } = useCanvas()
  const q = useAiSetup(companyId)
  if (q.isPending || q.isError || !q.data) return null
  const n = q.data.stack_count
  return (
    <Card>
      <CardHeader title="AI" description="The AIs that run the company, in order" actions={<Badge tone={q.data.ready ? 'success' : 'warning'}>{q.data.ready ? 'Ready' : 'Not ready'}</Badge>} />
      <CardBody className="space-y-3">
        <p className="text-[13px] text-fg-2">
          <span className="font-medium text-fg">{n === 0 ? 'No AI yet' : n === 1 ? '1 AI in your stack' : `${n} AIs in your stack`}</span>
          {q.data.blocking_reason ? <span className="block text-[12px] text-fg-3">{blockingReasonText(q.data.blocking_reason)}</span> : null}
        </p>
        <Button size="sm" icon={<Sparkles className="size-3.5" />} onClick={() => openPanel({ kind: 'ai' })}>
          Open AI settings
        </Button>
      </CardBody>
    </Card>
  )
}
