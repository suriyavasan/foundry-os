import { OctagonX, Play, ShieldAlert } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { errorMessage, isNotDeployed, useEmergencyControl, useSystemStatus } from '@/api'
import type { EmergencyStopStatus } from '@/api/types'
import { Badge, Button, Card, CardBody, CardHeader, ConfirmDialog, Dialog, ErrorState, Field, FormError, Input, LoadingState, NotAvailable, Textarea, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useCompanyRole } from '@/features/canvas/hooks'
import { formatDateTime, formatRelative } from '@/lib/format'
import { EMERGENCY_CONFIRM_WORD, canConfirmEmergencyStop } from '@/lib/guardrails'

/** Only the owner may start or lift the stop (it affects every company). The server enforces it too. */
const useIsOwner = (companyId: string) => useCompanyRole(companyId) === 'owner'

function StopFacts({ status }: { status: EmergencyStopStatus }) {
  const bits = [
    status.activated_at ? `since ${formatDateTime(status.activated_at)} (${formatRelative(status.activated_at)})` : null,
    status.activated_by ? `by ${status.activated_by}` : null,
    status.runs_cancelled != null ? `${status.runs_cancelled} run${status.runs_cancelled === 1 ? '' : 's'} cancelled` : null,
  ].filter(Boolean)
  return (
    <>
      {bits.length > 0 && <span>{bits.join(' · ')}</span>}
      {status.reason && <span> Reason: {status.reason}</span>}
    </>
  )
}

/* ----------------------------------- top bar banner ----------------------------------- */

/** Red banner across the top while the emergency stop is active. Renders nothing otherwise (or when M6 is not deployed). */
export function EmergencyBanner() {
  const q = useSystemStatus()
  const { companyId, openPanel } = useCanvas()
  const isOwner = useIsOwner(companyId)
  const [resuming, setResuming] = useState(false)
  if (!q.data?.active) return null
  return (
    <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-danger bg-danger px-3 py-1.5 text-[12.5px] text-fg-on-accent">
      <OctagonX className="size-4 shrink-0" aria-hidden />
      <span className="min-w-0 flex-1">
        <strong className="font-semibold">Emergency stop active.</strong> Every company is halted: no run starts until the owner resumes. <StopFacts status={q.data} />
      </span>
      <button type="button" onClick={() => openPanel({ kind: 'guardrails' })} className="rounded-sm px-1.5 py-0.5 font-medium underline underline-offset-2 hover:bg-danger-hover">
        Open guardrails
      </button>
      {isOwner && (
        <button type="button" onClick={() => setResuming(true)} className="rounded-md border border-fg-on-accent/60 px-2 py-0.5 font-medium hover:bg-danger-hover">
          Resume…
        </button>
      )}
      {resuming && <ResumeDialog onClose={() => setResuming(false)} />}
    </div>
  )
}

/* ---------------------------------------- dialogs ---------------------------------------- */

/** Confirm lifting the stop. */
export function ResumeDialog({ onClose }: { onClose: () => void }) {
  const control = useEmergencyControl()
  const toast = useToast()
  const [error, setError] = useState<string | null>(null)
  async function confirm() {
    setError(null)
    try {
      await control.mutateAsync({ action: 'resume' })
      toast.success('Emergency stop lifted. Dispatch can restart.')
      onClose()
    } catch (e) {
      setError(errorMessage(e))
    }
  }
  return (
    <ConfirmDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Resume all companies?"
      confirmLabel="Resume"
      destructive={false}
      loading={control.isPending}
      error={error}
      onConfirm={() => void confirm()}
      description={<p>Dispatch restarts in every company. Runs cancelled by the stop are not resumed automatically; ready tasks are picked up again.</p>}
    />
  )
}

/**
 * The emergency-stop confirmation. Starting it cancels every run in every company, so the button stays disabled until
 * the exact word is typed. Mounted once (CompanyLayout); the Guardrails panel and the command palette open it.
 */
export function EmergencyStopDialog() {
  const { stopOpen, setStopOpen, companyId } = useCanvas()
  const isOwner = useIsOwner(companyId)
  const control = useEmergencyControl()
  const toast = useToast()
  const [typed, setTyped] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  function close() {
    setStopOpen(false)
    setTyped('')
    setReason('')
    setError(null)
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!canConfirmEmergencyStop(typed) || !isOwner) return
    setError(null)
    try {
      await control.mutateAsync({ action: 'stop', reason })
      toast.success('Emergency stop active. Every company is halted.')
      close()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const ready = canConfirmEmergencyStop(typed) && isOwner
  return (
    <Dialog
      open={stopOpen}
      onOpenChange={(o) => {
        if (!o && !control.isPending) close()
        else if (o) setStopOpen(true)
      }}
      size="sm"
      title="Emergency stop"
      description="Halts every company on this installation."
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={control.isPending}>
            Cancel
          </Button>
          <Button type="submit" form="emergency-stop-form" variant="danger" loading={control.isPending} disabled={!ready} icon={<OctagonX className="size-3.5" aria-hidden />}>
            Stop everything
          </Button>
        </>
      }
    >
      <form id="emergency-stop-form" onSubmit={(e) => void submit(e)} className="space-y-3" noValidate>
        <div className="space-y-2 text-[13px] text-fg-2">
          <p>
            This <strong className="font-semibold text-fg">cancels every run in every company</strong> and{' '}
            <strong className="font-semibold text-fg">disables dispatch</strong> until the owner resumes it. It is not limited to this company.
          </p>
          <p>Tasks, history and spend are kept. Nothing is deleted.</p>
        </div>
        <Field label={`Type ${EMERGENCY_CONFIRM_WORD} to confirm`} required>
          {({ id, describedBy }) => <Input id={id} autoFocus autoComplete="off" spellCheck={false} aria-describedby={describedBy} value={typed} placeholder={EMERGENCY_CONFIRM_WORD} onChange={(e) => setTyped(e.target.value)} />}
        </Field>
        <Field label="Reason (optional)" hint="Recorded in the audit log.">
          {({ id, describedBy }) => <Textarea id={id} rows={2} aria-describedby={describedBy} value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} />}
        </Field>
        {!isOwner && <FormError message="Only the owner can start an emergency stop." />}
        <FormError message={error} />
      </form>
    </Dialog>
  )
}

/* ----------------------------------------- panel ----------------------------------------- */

/** The prominent danger section of the Guardrails panel. */
export function EmergencyStopSection() {
  const { companyId, setStopOpen } = useCanvas()
  const q = useSystemStatus()
  const isOwner = useIsOwner(companyId)
  const [resuming, setResuming] = useState(false)

  const body = () => {
    if (q.isPending) return <LoadingState rows={2} label="Loading emergency stop" />
    if (q.isError) {
      return isNotDeployed(q.error) ? (
        <NotAvailable compact className="p-4" milestone="M6" description="The emergency stop and its status come with guardrails & governance." />
      ) : (
        <ErrorState compact error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      )
    }
    const status = q.data
    const active = status?.active === true
    return (
      <CardBody className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={active ? 'danger' : 'success'}>{active ? 'Stop active: every company halted' : 'Not stopped'}</Badge>
          {active && status && (
            <span className="text-[12.5px] text-fg-2">
              <StopFacts status={status} />
            </span>
          )}
        </div>
        <p className="text-[13px] text-fg-2">
          The emergency stop cancels <strong className="font-semibold text-fg">every run in every company</strong> and disables dispatch until the owner resumes. Use it when something is going wrong and you need everything to stop now. To halt only this company, use{' '}
          <em>Pause company</em> in the top bar.
        </p>
        {!isOwner && <p className="text-[12.5px] text-fg-3">Only the owner can start or lift the emergency stop.</p>}
        <div className="flex flex-wrap gap-2">
          {active ? (
            <Button variant="primary" icon={<Play className="size-3.5" aria-hidden />} disabled={!isOwner} onClick={() => setResuming(true)}>
              Resume all companies…
            </Button>
          ) : (
            <Button variant="danger" icon={<OctagonX className="size-3.5" aria-hidden />} disabled={!isOwner} onClick={() => setStopOpen(true)}>
              Emergency stop…
            </Button>
          )}
        </div>
        {resuming && <ResumeDialog onClose={() => setResuming(false)} />}
      </CardBody>
    )
  }

  return (
    <Card className="border-danger/50" aria-labelledby="emergency-stop-title">
      <CardHeader
        title={
          <span id="emergency-stop-title" className="flex items-center gap-2 text-danger">
            <ShieldAlert className="size-4" aria-hidden /> Emergency stop
          </span>
        }
        description="The global kill switch. Affects all companies."
        className="bg-danger-subtle"
      />
      {body()}
    </Card>
  )
}
