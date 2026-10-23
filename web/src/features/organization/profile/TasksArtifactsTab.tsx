import { Card, CardHeader, NotAvailable } from '@/components/ui'

export function TasksArtifactsTab() {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card>
        <CardHeader title="Tasks" />
        <NotAvailable compact className="p-4" milestone="M3" description="Assigned tasks and their run history." />
      </Card>
      <Card>
        <CardHeader title="Artifacts" />
        <NotAvailable compact className="p-4" milestone="M7" description="Documents and files this agent produced." />
      </Card>
    </div>
  )
}
