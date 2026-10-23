import { ExternalLink, Lock } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { ApiError, errorMessage, useCreateConnectedAccount, useCreateProviderSecret, useStartOAuth, useTestConnectedAccount } from '@/api'
import type { ConnectedProvider } from '@/api/types'
import { Button, Dialog, Field, FormError, Input, Select, useToast } from '@/components/ui'
import {
  BOUNDARY_COPY,
  PROVIDERS,
  emptyConnectForm,
  isSafeAuthorizeUrl,
  providerDef,
  scrubSecrets,
  toConnectedAccountBody,
  toSecretCredentialRequests,
  typedSecrets,
  validateConnectForm,
  type ConnectFormErrors,
  type ConnectFormValues,
  type ProviderDef,
} from '@/lib/connectedAccounts'

/**
 * Connect a provider account. Tokens are write-only: each goes once to the credential store, the field is emptied the
 * moment the server accepted it, the account only references the credential id, and nothing the server says back is
 * shown without the typed secrets scrubbed out of it.
 */
export function ConnectAccountDialog({ companyId, initialProvider, onClose }: { companyId: string; initialProvider?: ConnectedProvider; onClose: () => void }) {
  const [provider, setProvider] = useState<ConnectedProvider>(initialProvider ?? 'cloudflare')
  const def = providerDef(provider)!
  const [values, setValues] = useState<ConnectFormValues>(() => emptyConnectForm(def))
  const [errors, setErrors] = useState<ConnectFormErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  // Credential ids already stored for this attempt, so a retry never stores a secret twice.
  const [saved, setSaved] = useState<Record<string, string>>({})
  const secret = useCreateProviderSecret(companyId)
  const create = useCreateConnectedAccount(companyId)
  const test = useTestConnectedAccount(companyId)
  const oauth = useStartOAuth(companyId)
  const toast = useToast()
  const busy = secret.isPending || create.isPending || test.isPending || oauth.isPending

  function changeProvider(next: ConnectedProvider) {
    setProvider(next)
    setValues(emptyConnectForm(providerDef(next)!))
    setErrors({})
    setFormError(null)
    setSaved({})
  }
  function patchField(key: string, value: string) {
    setValues((v) => ({ ...v, fields: { ...v.fields, [key]: value } }))
    setErrors((e) => {
      const n = { ...e }
      delete n[key]
      return n
    })
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const clientErrors = validateConnectForm(def, values, { savedKeys: Object.keys(saved) })
    setErrors(clientErrors)
    if (Object.keys(clientErrors).length) return

    const secrets = typedSecrets(def, values)
    const ids = { ...saved }
    try {
      for (const req of toSecretCredentialRequests(def, values)) {
        if (ids[req.key]) continue
        const cred = await secret.mutateAsync(req.body)
        ids[req.key] = cred.id
        setSaved((s) => ({ ...s, [req.key]: cred.id }))
        // The server has it; the browser does not need to keep it.
        setValues((v) => ({ ...v, fields: { ...v.fields, [req.key]: '' } }))
      }
      const account = await create.mutateAsync(toConnectedAccountBody(def, values, ids))
      // A pasted wrong token must not look connected: check it once, right away.
      try {
        const check = await test.mutateAsync(account.id)
        if (check.ok) toast.success(`${def.label} connected`)
        else toast.error(`${def.label} was saved, but the provider rejected it${check.message ? `: ${scrubSecrets(check.message, secrets)}` : '.'}`)
      } catch {
        toast.success(`${def.label} saved. Test the connection from the list.`)
      }
      onClose()
    } catch (err) {
      const msg = scrubSecrets(errorMessage(err), secrets)
      if (err instanceof ApiError && err.status === 422) {
        const mapped: ConnectFormErrors = {}
        const firstSecret = def.fields.find((f) => f.secret)?.key
        for (const [k, m] of Object.entries(err.fieldErrors)) {
          const key = k === 'value' ? firstSecret : k.replace(/^metadata\./, '')
          if (key && (key === 'label' || def.fields.some((f) => f.key === key))) mapped[key] = scrubSecrets(m, secrets)
        }
        setErrors(mapped)
        if (!Object.keys(mapped).length) setFormError(msg)
      } else {
        setFormError(msg)
      }
    }
  }

  async function signIn() {
    setFormError(null)
    try {
      const { authorize_url } = await oauth.mutateAsync(provider)
      if (!isSafeAuthorizeUrl(authorize_url)) throw new Error('The server did not return a safe sign-in address.')
      window.open(authorize_url, '_blank', 'noopener,noreferrer')
      toast.success(`Finish signing in to ${def.label} in the new tab, then come back.`)
      onClose()
    } catch (err) {
      setFormError(errorMessage(err))
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !busy && onClose()}
      size="lg"
      title="Connect an account"
      description={BOUNDARY_COPY}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          {def.auth === 'oauth' ? (
            <Button variant="primary" icon={<ExternalLink className="size-3.5" />} loading={oauth.isPending} onClick={() => void signIn()}>
              Sign in to {def.label}
            </Button>
          ) : (
            <Button variant="primary" type="submit" form="connect-account-form" loading={busy}>
              Connect {def.label}
            </Button>
          )}
        </>
      }
    >
      <form id="connect-account-form" onSubmit={submit} noValidate autoComplete="off" className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Provider" required>
            {({ id }) => (
              <Select id={id} value={provider} disabled={busy || Object.keys(saved).length > 0} onChange={(e) => changeProvider(e.target.value as ConnectedProvider)}>
                {PROVIDERS.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          {def.auth === 'token' && (
            <Field label="Label" error={errors.label} hint={`Shown in lists. Defaults to “${def.label}”.`}>
              {({ id, invalid, describedBy }) => <Input id={id} value={values.label} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setValues((v) => ({ ...v, label: e.target.value }))} />}
            </Field>
          )}
        </div>
        <p className="text-[12.5px] text-fg-2">
          <strong className="font-medium text-fg">{def.purpose}.</strong> {def.tokenHelp}
          {def.tokenUrl && (
            <>
              {' '}
              <a href={def.tokenUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-accent-fg hover:underline">
                Where to create it <ExternalLink className="size-3" aria-hidden />
              </a>
            </>
          )}
        </p>

        {def.auth === 'token' && <TokenFields def={def} values={values} errors={errors} saved={saved} onChange={patchField} />}
        <FormError message={formError} />
      </form>
    </Dialog>
  )
}

function TokenFields({ def, values, errors, saved, onChange }: { def: ProviderDef; values: ConnectFormValues; errors: ConnectFormErrors; saved: Record<string, string>; onChange: (key: string, value: string) => void }) {
  const secrets = def.fields.filter((f) => f.secret)
  const plain = def.fields.filter((f) => !f.secret)
  return (
    <>
      <fieldset className="space-y-3 rounded-md border border-line bg-raised p-3">
        <legend className="px-1 text-[12px] font-medium text-fg-2">Secret</legend>
        <div className="grid gap-4 sm:grid-cols-2">
          {secrets.map((f) =>
            saved[f.key] ? (
              <p key={f.key} role="status" className="text-[12.5px] text-fg-2">
                {f.label} saved. It cannot be shown again. Fix the error above and connect; it will not be stored twice.
              </p>
            ) : (
              <Field key={f.key} label={f.label} required={f.required} error={errors[f.key]}>
                {({ id, invalid, describedBy }) => (
                  <Input id={id} type="password" autoComplete="off" spellCheck={false} data-lpignore="true" value={values.fields[f.key] ?? ''} invalid={invalid} aria-describedby={describedBy} placeholder={f.placeholder} onChange={(e) => onChange(f.key, e.target.value)} />
                )}
              </Field>
            ),
          )}
        </div>
        <p className="flex items-center gap-1.5 text-[12px] text-fg-3">
          <Lock className="size-3" aria-hidden /> Write-only. Stored encrypted on the server; never shown again, logged or given to agents.
        </p>
      </fieldset>
      {plain.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2">
          {plain.map((f) => (
            <Field key={f.key} label={f.label} required={f.required} error={errors[f.key]} hint={f.hint}>
              {({ id, invalid, describedBy }) => <Input id={id} spellCheck={false} value={values.fields[f.key] ?? ''} invalid={invalid} aria-describedby={describedBy} placeholder={f.placeholder} onChange={(e) => onChange(f.key, e.target.value)} />}
            </Field>
          ))}
        </div>
      )}
    </>
  )
}
