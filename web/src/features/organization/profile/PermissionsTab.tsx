import { useState } from 'react'
import type { Agent } from '@/api/types'
import { StringListEditor } from '@/components/forms/StringListEditor'
import { Button, Card, CardBody, CardHeader, FormError } from '@/components/ui'
import { useAgentSave } from './useAgentSave'

export function PermissionsTab({ agent }: { agent: Agent }) {
  const readOnly = agent.status === 'retired'
  const { save, saving, error } = useAgentSave(agent)
  const [perms, setPerms] = useState<string[]>(agent.tool_permissions)
  const dirty = perms.length !== agent.tool_permissions.length || perms.some((p, i) => p !== agent.tool_permissions[i])

  return (
    <Card className="max-w-2xl">
      <CardHeader
        title="Tool permissions"
        description="Tools this agent is allowed to use. Only a human can change these; agents cannot edit their own permissions."
      />
      <CardBody className="space-y-4">
        <StringListEditor
          values={perms}
          onChange={setPerms}
          inputLabel="New tool permission"
          placeholder="e.g. web.search"
          addLabel="Add permission"
          emptyText="No tools granted."
          disabled={readOnly}
        />
        <p className="text-[12px] text-fg-3">
          Permissions are stored on the agent record. They are not enforced until agents can execute work (M4).
        </p>
        <FormError message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" disabled={!dirty || saving} onClick={() => setPerms(agent.tool_permissions)}>
            Reset
          </Button>
          <Button variant="primary" loading={saving} disabled={!dirty || readOnly} onClick={() => void save({ tool_permissions: perms }, 'Permissions saved')}>
            Save permissions
          </Button>
        </div>
      </CardBody>
    </Card>
  )
}
