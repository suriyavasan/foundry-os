import { ArrowDown, ArrowUp, ChevronDown, FlaskConical, LogOut, Pencil, Trash2 } from 'lucide-react'
import { useState } from 'react'
import {
  errorMessage,
  useAiConnectionLogout,
  usePatchAiConnection,
  useProviderModels,
  useRemoveAiConnection,
  useTestAiConnection,
  type AiConnection,
  type AiOption,
} from '@/api'
import { Badge, Button, ConfirmDialog, Dialog, Field, FormError, Input, Select, Switch } from '@/components/ui'
import { formatRelative } from '@/lib/format'
import { SHARE_LABEL, SHARE_ORDER, STATE_LABEL, STATE_TONE, USE_FOR_LABEL, USE_FOR_ORDER, isPlanOption, type AiUseFor } from '@/lib/aiStack'
import { CliInstall } from './CliInstall'
import { ModelPicker } from './ModelPicker'
import { PlanSignIn } from './PlanSignIn'

/** One connection of the stack: rank, name, status, "Use for", "Importance", on/off, order, and the less common actions. */
export function AiStackRow({
  companyId,
  conn,
  option,
  canEdit,
  isFirst,
  isLast,
  onMove,
  busyOrder,
}: {
  companyId: string
  conn: AiConnection
  option: AiOption | undefined
  canEdit: boolean
  isFirst: boolean
  isLast: boolean
  onMove: (dir: -1 | 1) => void
  busyOrder: boolean
}) {
  const patch = usePatchAiConnection(companyId)
  const test = useTestAiConnection(companyId)
  const remove = useRemoveAiConnection(companyId)
  const logout = useAiConnectionLogout(companyId)
  const [manage, setManage] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [confirmLogout, setConfirmLogout] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [changingModel, setChangingModel] = useState(false)

  const plan = isPlanOption(conn)
  const st = conn.status
  const n = conn.label
  const needsInstall = st.state === 'not_installed'
  const needsSignIn = plan && st.state === 'needs_sign_in'
  const lt = st.last_test

  async function run(fn: () => Promise<unknown>) {
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(errorMessage(e))
    }
  }
  const doTest = () => run(() => test.mutateAsync(conn.id))
  const change = (body: Parameters<typeof patch.mutateAsync>[0]['body']) => run(() => patch.mutateAsync({ id: conn.id, body }))

  return (
    <li className="rounded-lg border border-line bg-surface p-3 sm:p-4" aria-label={`${conn.rank_label}: ${n}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">{conn.rank_label}</p>
          <p className="truncate text-[14px] font-semibold text-fg">{n}</p>
          <p className="text-[12.5px] text-fg-3">
            {option?.label ?? conn.option}
            {conn.model && (
              <>
                {' · '}
                <span className="font-mono text-fg-2">{conn.model}</span>
              </>
            )}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={STATE_TONE[st.state]}>{STATE_LABEL[st.state]}</Badge>
          {canEdit && (
            <div className="flex items-center gap-1">
              <Button size="sm" variant="ghost" aria-label={`Move ${n} up`} disabled={isFirst || busyOrder} onClick={() => onMove(-1)} icon={<ArrowUp className="size-3.5" />}>
                <span className="sr-only">Move up</span>
              </Button>
              <Button size="sm" variant="ghost" aria-label={`Move ${n} down`} disabled={isLast || busyOrder} onClick={() => onMove(1)} icon={<ArrowDown className="size-3.5" />}>
                <span className="sr-only">Move down</span>
              </Button>
            </div>
          )}
        </div>
      </div>

      {(st.message || st.paused_until) && (
        <p className="mt-1.5 text-[12.5px] text-fg-2">
          {st.message}
          {st.paused_until ? ` Paused until ${new Date(st.paused_until).toLocaleString()}.` : ''}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="block">
          <span className="mb-1 block text-[12px] font-medium text-fg-2">Use for</span>
          <Select aria-label={`Use for, ${n}`} value={conn.use_for} disabled={!canEdit || patch.isPending} onChange={(e) => void change({ use_for: e.target.value as AiUseFor })}>
            {USE_FOR_ORDER.map((u) => (
              <option key={u} value={u}>
                {USE_FOR_LABEL[u]}
              </option>
            ))}
          </Select>
        </label>
        <label className="block">
          <span className="mb-1 block text-[12px] font-medium text-fg-2">Importance</span>
          <Select aria-label={`Importance, ${n}`} value={String(conn.share)} disabled={!canEdit || patch.isPending} onChange={(e) => void change({ share: Number(e.target.value) })}>
            {SHARE_ORDER.map((s) => (
              <option key={s} value={s}>
                {SHARE_LABEL[s]}
              </option>
            ))}
          </Select>
        </label>
        <div className="flex items-center gap-2 pb-1">
          <Switch label={`Use ${n}`} checked={conn.enabled} disabled={!canEdit} loading={patch.isPending} onCheckedChange={(v) => void change({ enabled: v })} />
          <span className="text-[12.5px] text-fg-2">{conn.enabled ? 'On' : 'Off'}</span>
        </div>
        <div className="ml-auto flex items-center gap-2 pb-0.5">
          {canEdit && (st.state === 'needs_test' || st.state === 'test_failed') && (
            <Button size="sm" variant="primary" loading={test.isPending} onClick={() => void doTest()}>
              Test
            </Button>
          )}
          <Button size="sm" variant="ghost" aria-expanded={manage} onClick={() => setManage((m) => !m)} icon={<ChevronDown className="size-3.5" />}>
            Manage
          </Button>
        </div>
      </div>

      {needsInstall && option && conn.cli_id && (
        <div className="mt-3">
          <CliInstall companyId={companyId} cliId={conn.cli_id} name={option.label} command={option.install?.available ? option.install.command : null} canEdit={canEdit} />
        </div>
      )}
      {needsSignIn && (
        <div className="mt-3">
          <PlanSignIn companyId={companyId} conn={conn} canEdit={canEdit} />
        </div>
      )}

      {manage && (
        <div className="mt-3 space-y-2 border-t border-line pt-3">
          {lt && (
            <p role="status" className={'text-[12.5px] ' + (lt.ok ? 'text-success' : 'text-danger')}>
              {lt.ok ? 'Last test passed' : 'Last test failed'}
              {lt.detail ? `: ${lt.detail}` : ''}
              {lt.tested_at ? <span className="text-fg-3"> · {formatRelative(lt.tested_at)}</span> : null}
            </p>
          )}
          {plan && st.state !== 'needs_sign_in' && st.state !== 'not_installed' && (
            <PlanSignIn companyId={companyId} conn={conn} canEdit={canEdit} />
          )}
          {canEdit ? (
            <div className="flex flex-wrap gap-2">
              <Button size="sm" icon={<FlaskConical className="size-3.5" />} loading={test.isPending} onClick={() => void doTest()}>
                Test {n}
              </Button>
              {plan && st.signed_in && (
                <Button size="sm" icon={<LogOut className="size-3.5" />} onClick={() => setConfirmLogout(true)}>
                  Sign out of {n}
                </Button>
              )}
              {conn.engine === 'api' && (
                <Button size="sm" onClick={() => setChangingModel(true)}>
                  Change model of {n}
                </Button>
              )}
              <Button size="sm" icon={<Pencil className="size-3.5" />} onClick={() => setRenaming(true)}>
                Rename {n}
              </Button>
              <Button size="sm" variant="danger" icon={<Trash2 className="size-3.5" />} onClick={() => setConfirmRemove(true)}>
                Remove {n}
              </Button>
            </div>
          ) : (
            <p className="text-[12px] text-fg-3">Only an owner or admin can change this.</p>
          )}
          {option?.warning && <p className="text-[12px] text-fg-3">{option.warning}</p>}
        </div>
      )}
      <FormError message={error} />

      <ConfirmDialog
        open={confirmRemove}
        onOpenChange={setConfirmRemove}
        title={`Remove ${n}?`}
        confirmLabel="Remove"
        loading={remove.isPending}
        error={remove.error ? errorMessage(remove.error) : null}
        description={plan ? 'Foundry signs out and stops using it. Work moves to your other AIs.' : 'Foundry stops using it. Work moves to your other AIs. Your key and provider stay in Integrations.'}
        onConfirm={() => remove.mutate(conn.id, { onSuccess: () => setConfirmRemove(false) })}
      />
      <ConfirmDialog
        open={confirmLogout}
        onOpenChange={setConfirmLogout}
        title={`Sign out of ${n}?`}
        confirmLabel="Sign out"
        loading={logout.isPending}
        description="Your company stops using this plan until you sign in again."
        onConfirm={() => logout.mutate(conn.id, { onSettled: () => setConfirmLogout(false) })}
      />
      {renaming && <RenameDialog current={n} onClose={() => setRenaming(false)} onSave={(label) => run(() => patch.mutateAsync({ id: conn.id, body: { label } })).then(() => setRenaming(false))} />}
      {changingModel && (
        <ModelDialog
          companyId={companyId}
          conn={conn}
          onClose={() => setChangingModel(false)}
          onSave={(model) => run(() => patch.mutateAsync({ id: conn.id, body: { model } })).then(() => setChangingModel(false))}
        />
      )}
    </li>
  )
}

function RenameDialog({ current, onClose, onSave }: { current: string; onClose: () => void; onSave: (label: string) => void }) {
  const [v, setV] = useState(current)
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Rename this AI"
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!v.trim()} onClick={() => onSave(v.trim())}>
            Save name
          </Button>
        </>
      }
    >
      <Field label="Name">{({ id }) => <Input id={id} className="h-10" value={v} maxLength={120} onChange={(e) => setV(e.target.value)} />}</Field>
    </Dialog>
  )
}

function ModelDialog({ companyId, conn, onClose, onSave }: { companyId: string; conn: AiConnection; onClose: () => void; onSave: (model: string) => void }) {
  const models = useProviderModels(companyId, conn.model_provider_id)
  const [v, setV] = useState(conn.model ?? '')
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Change model"
      description="The new model is used from the next run. Test it afterwards."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!v.trim() || v.trim() === conn.model} onClick={() => onSave(v.trim())}>
            Save model
          </Button>
        </>
      }
    >
      <Field label="Model">
        {({ id, invalid, describedBy }) => <ModelPicker id={id} value={v} onChange={setV} options={models.data ?? []} loading={models.isFetching} invalid={invalid} describedBy={describedBy} />}
      </Field>
    </Dialog>
  )
}
