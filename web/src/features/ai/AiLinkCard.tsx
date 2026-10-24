import { Sparkles } from 'lucide-react'
import { useAiSetup } from '@/api'
import { Badge, Button, Card, CardBody, CardHeader } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { WORK_MODE_LABEL } from '@/lib/aiSetup'

/** Settings entry to the AI panel: the current model and work mode in one line. Not drawn until the backend serves the AI setup. */
export function AiLinkCard() {
  const { companyId, openPanel } = useCanvas()
  const q = useAiSetup(companyId)
  if (q.isPending || q.isError || !q.data) return null
  const m = q.data.management
  return (
    <Card>
      <CardHeader title="AI" description="The model that runs the company, and how work gets done" actions={<Badge tone={q.data.ready ? 'success' : 'warning'}>{q.data.ready ? 'Ready' : 'Not ready'}</Badge>} />
      <CardBody className="space-y-3">
        <p className="text-[13px] text-fg-2">
          <span className="font-medium text-fg">{m.provider_name ?? 'No provider'}</span>
          {m.model ? <span className="ml-1 font-mono text-[12px] break-all">{m.model}</span> : null}
          <span className="block text-[12px] text-fg-3">{WORK_MODE_LABEL[q.data.work_mode]}</span>
        </p>
        <Button size="sm" icon={<Sparkles className="size-3.5" />} onClick={() => openPanel({ kind: 'ai' })}>
          Open AI settings
        </Button>
      </CardBody>
    </Card>
  )
}
