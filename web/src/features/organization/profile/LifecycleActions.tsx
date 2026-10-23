import { useState } from 'react'
import { ApiError, errorMessage, useAgentLifecycle } from '@/api'
import type { Agent, AgentLifecycleAction } from '@/api/types'
import { Button, ConfirmDialog, Field, FormError, Textarea, useToast } from '@/components/ui'
import { availableLifecycleActions, pastTense, type LifecycleActionSpec } from '@/lib/lifecycle'

export function LifecycleActions({ agent, companyId }: { agent: Agent; companyId: string }) {
  const lifecycle = useAgentLifecycle(companyId, agent.id)
  const toast = useToast()
  const [pending, setPending] = useState<LifecycleActionSpec | null>(null)
  const [reason, setReason] = useState('')
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [inlineError, setInlineError] = useState<string | null>(null)
  const [running, setRunning] = useState<AgentLifecycleAction | null>(null)

  const actions = availableLifecycleActions(agent.status, agent.kind)

  async function run(spec: LifecycleActionSpec, withReason?: string) {
    setInlineError(null)
    setDialogError(null)
    setRunning(spec.action)
    try {
      await lifecycle.mutateAsync({ action: spec.action, reason: withReason?.trim() || undefined })
      toast.success(`${agent.name} ${pastTense[spec.action]}`)
      setPending(null)
      setReason('')
    } catch (err) {
      const msg =
        err instanceof ApiError && (err.status === 409 || err.status === 422)
          ? err.message
          : errorMessage(err)
      if (spec.confirm) setDialogError(msg)
      else setInlineError(msg)
    } finally {
      setRunning(null)
    }
  }

  if (actions.length === 0) return null

  return (
    <>
      {actions.map((spec) => (
        <Button
          key={spec.action}
          variant={spec.variant}
          loading={running === spec.action && !spec.confirm}
          disabled={running !== null}
          onClick={() => (spec.confirm ? (setPending(spec), setDialogError(null)) : void run(spec))}
        >
          {spec.label}
        </Button>
      ))}
      {inlineError && (
        <div className="basis-full">
          <FormError message={inlineError} />
        </div>
      )}
      {pending && (
        <ConfirmDialog
          open
          onOpenChange={(o) => {
            if (!o) {
              setPending(null)
              setReason('')
              setDialogError(null)
            }
          }}
          title={`${pending.label} ${agent.name}?`}
          confirmLabel={pending.label}
          destructive={pending.destructive}
          loading={running === pending.action}
          error={dialogError}
          onConfirm={() => void run(pending, reason)}
          description={
            <div className="space-y-3">
              <p>{pending.consequence}</p>
              {agent.kind === 'founder' && pending.action === 'retire' && (
                <p className="text-warning">The AI Co-founder cannot be retired while it is the company&apos;s only AI Co-founder.</p>
              )}
              <Field label="Reason (optional)" hint="Recorded in the audit log.">
                {({ id }) => <Textarea id={id} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />}
              </Field>
            </div>
          }
        />
      )}
    </>
  )
}
