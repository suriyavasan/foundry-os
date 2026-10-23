import { Globe } from 'lucide-react'
import { useReachability } from '@/api'
import { Button, Card, CardBody, CardHeader } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { isMissingEndpoint } from '@/lib/auth'
import { globeModel } from '@/lib/reachability'

/** Settings entry to Reachability: current state in one line. Not drawn until the backend serves reachability. */
export function ReachabilityLinkCard() {
  const { openPanel } = useCanvas()
  const q = useReachability()
  if (q.isPending || (q.isError && isMissingEndpoint(q.error))) return null
  const g = globeModel(q.data ?? null)
  return (
    <Card>
      <CardHeader title="Reachability" description="How to reach this company from other devices" />
      <CardBody className="space-y-3">
        <p className="text-[13px] text-fg-2">
          <span className="font-medium text-fg">{g.label}</span>
          {g.url ? <span className="ml-1 font-mono text-[12px] break-all">{g.url}</span> : null}
        </p>
        <Button size="sm" icon={<Globe className="size-3.5" />} onClick={() => openPanel({ kind: 'reachability' })}>
          Open reachability
        </Button>
      </CardBody>
    </Card>
  )
}
