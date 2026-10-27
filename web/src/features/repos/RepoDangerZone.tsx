import { useState } from 'react'
import { errorMessage, useDisconnectRepository, useRepoAction, useResetIntegration } from '@/api'
import type { ProjectRepository } from '@/api/types'
import { Button, Card, CardBody, CardHeader, Dialog, Field, FormError, Input, Select, useToast } from '@/components/ui'
import { DISCONNECT_CONFIRM_WORD, RESET_CONFIRM_WORD, confirmsWord } from '@/lib/repos'

type Reset = 'remote_work_branch' | 'default_branch'

function ConfirmWordDialog({ open, onOpenChange, title, word, body, confirmLabel, loading, error, onConfirm, extra }: { open: boolean; onOpenChange: (o: boolean) => void; title: string; word: string; body: string; confirmLabel: string; loading: boolean; error: string | null; onConfirm: () => void; extra?: React.ReactNode }) {
  const [typed, setTyped] = useState('')
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!loading) {
          onOpenChange(o)
          if (!o) setTyped('')
        }
      }}
      title={title}
      size="sm"
      footer={
        <>
          <Button variant="ghost" disabled={loading} onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="danger" loading={loading} disabled={!confirmsWord(typed, word)} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-[13px] text-fg-2">{body}</p>
        {extra}
        <Field label={`Type “${word}” to confirm`}>{({ id }) => <Input id={id} autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} />}</Field>
        <FormError message={error} />
      </div>
    </Dialog>
  )
}

/** Pause or resume, sync now, reset the integration branch, disconnect. Owners and admins only; the server refuses the rest. */
export function RepoDangerZone({ companyId, repo, canEdit }: { companyId: string; repo: ProjectRepository; canEdit: boolean }) {
  const action = useRepoAction(companyId, repo.id)
  const disconnect = useDisconnectRepository(companyId, repo.id)
  const reset = useResetIntegration(companyId, repo.id)
  const toast = useToast()
  const [confirm, setConfirm] = useState<'reset' | 'disconnect' | null>(null)
  const [resetTo, setResetTo] = useState<Reset>('remote_work_branch')
  const [error, setError] = useState<string | null>(null)
  const disconnected = repo.status === 'disconnected'
  const noNetwork = disconnected || repo.status === 'paused' || repo.status === 'creating' || repo.status === 'cloning'

  async function run(kind: 'sync' | 'pause' | 'resume' | 'pull-requests/resume-opening', done: string) {
    try {
      await action.mutateAsync(kind)
      toast.success(done)
    } catch (e) {
      toast.error(errorMessage(e))
    }
  }

  if (!canEdit) {
    return (
      <Card>
        <CardBody>
          <p className="text-[13px] text-fg-3">Only owners and admins can pause, reset or disconnect a repository.</p>
        </CardBody>
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Sync" description="Foundry checks GitHub on a timer. A paused repository makes no network calls at all; local work continues." />
        <CardBody className="flex flex-wrap items-center gap-2">
          <Button disabled={noNetwork} loading={action.isPending && action.variables === 'sync'} onClick={() => void run('sync', 'Sync queued')}>
            Sync now
          </Button>
          {repo.status === 'paused' ? (
            <Button loading={action.isPending && action.variables === 'resume'} onClick={() => void run('resume', 'Sync resumed')}>
              Resume sync
            </Button>
          ) : (
            <Button disabled={disconnected} loading={action.isPending && action.variables === 'pause'} onClick={() => void run('pause', 'Sync paused')}>
              Pause sync
            </Button>
          )}
          {repo.pr_opening_paused && (
            <Button loading={action.isPending && action.variables === 'pull-requests/resume-opening'} onClick={() => void run('pull-requests/resume-opening', 'Opening pull requests again')}>
              Resume opening pull requests
            </Button>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Reset the integration branch" description="Throws away Foundry’s unmerged local integration work. The old state is kept on a foundry/rescue-… branch." />
        <CardBody>
          <Button variant="danger" disabled={disconnected} onClick={() => { setError(null); setConfirm('reset') }}>
            Reset integration…
          </Button>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Disconnect" description="The binding and its local clone stay, and the project keeps working locally on its work branch. Refused while a pull request is being merged or a run of the project is active." />
        <CardBody>
          <Button variant="danger" disabled={disconnected} onClick={() => { setError(null); setConfirm('disconnect') }}>
            {disconnected ? 'Already disconnected' : 'Disconnect…'}
          </Button>
        </CardBody>
      </Card>

      {confirm === 'reset' && (
      <ConfirmWordDialog
        open
        onOpenChange={(o) => !o && setConfirm(null)}
        title="Reset the integration branch"
        word={RESET_CONFIRM_WORD}
        body="This is refused while a run of the project is active. Nothing on GitHub is deleted."
        confirmLabel="Reset"
        loading={reset.isPending}
        error={error}
        extra={
          <Field label="Reset it to">
            {({ id }) => (
              <Select id={id} value={resetTo} onChange={(e) => setResetTo(e.target.value as Reset)}>
                <option value="remote_work_branch">The work branch on GitHub ({repo.work_branch})</option>
                <option value="default_branch">The default branch ({repo.default_branch})</option>
              </Select>
            )}
          </Field>
        }
        onConfirm={async () => {
          setError(null)
          try {
            await reset.mutateAsync(resetTo)
            toast.success('Integration branch reset')
            setConfirm(null)
          } catch (e) {
            setError(errorMessage(e))
          }
        }}
      />
      )}
      {confirm === 'disconnect' && (
      <ConfirmWordDialog
        open
        onOpenChange={(o) => !o && setConfirm(null)}
        title={`Disconnect ${repo.full_name}`}
        word={DISCONNECT_CONFIRM_WORD}
        body="Foundry stops talking to GitHub for this project. You can connect a repository again afterwards."
        confirmLabel="Disconnect"
        loading={disconnect.isPending}
        error={error}
        onConfirm={async () => {
          setError(null)
          try {
            await disconnect.mutateAsync()
            toast.success('Repository disconnected')
            setConfirm(null)
          } catch (e) {
            setError(errorMessage(e))
          }
        }}
      />
      )}
    </div>
  )
}
