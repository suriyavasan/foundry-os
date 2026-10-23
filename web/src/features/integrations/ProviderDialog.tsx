import { Lock } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { ApiError, errorMessage, useCreateCredential, useCreateModelProvider } from '@/api'
import { Button, Dialog, Field, FormError, Input, Select, useToast } from '@/components/ui'
import {
  BASE_URL_HINT,
  PROVIDER_KINDS,
  allowsCredential,
  deriveIsLocal,
  emptyProviderForm,
  needsCredential,
  providerKindLabel,
  redactSecret,
  toCreateCredentialBody,
  toCreateProviderBody,
  validateProviderForm,
  type ProviderFormErrors,
  type ProviderFormValues,
} from '@/lib/providers'

/**
 * Add a model provider. The API key is write-only: it goes to the credential endpoint once, the field is
 * emptied the moment the server accepted it, and the provider only references the credential's id.
 */
export function ProviderDialog({ companyId, initial, onClose }: { companyId: string; initial?: ProviderFormValues; onClose: () => void }) {
  const [values, setValues] = useState<ProviderFormValues>(() => initial ?? emptyProviderForm())
  const [errors, setErrors] = useState<ProviderFormErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  // Set once the credential exists, so a retry after a provider error never stores the key twice.
  const [savedCredential, setSavedCredential] = useState<{ id: string; name: string } | null>(null)
  const createCredential = useCreateCredential(companyId)
  const createProvider = useCreateModelProvider(companyId)
  const toast = useToast()
  const busy = createCredential.isPending || createProvider.isPending

  const patch = (p: Partial<ProviderFormValues>) => {
    setValues((v) => ({ ...v, ...p }))
    setErrors((e) => {
      const next = { ...e }
      for (const k of Object.keys(p)) delete next[k as keyof ProviderFormValues]
      return next
    })
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const clientErrors = validateProviderForm(values, { hasCredential: !!savedCredential })
    setErrors(clientErrors)
    if (Object.keys(clientErrors).length) return

    const secret = values.secret
    let credentialId = savedCredential?.id ?? null
    try {
      const credBody = savedCredential ? null : toCreateCredentialBody(values)
      if (credBody) {
        const cred = await createCredential.mutateAsync(credBody)
        credentialId = cred.id
        setSavedCredential({ id: cred.id, name: cred.name })
        // The server has it; the browser does not need to keep it.
        setValues((v) => ({ ...v, secret: '' }))
      }
      const provider = await createProvider.mutateAsync(toCreateProviderBody(values, credentialId))
      toast.success(`${provider.name} added`)
      onClose()
    } catch (err) {
      const msg = redactSecret(errorMessage(err), secret)
      if (err instanceof ApiError && err.status === 422) {
        const fields = err.fieldErrors
        const known = new Set(Object.keys(emptyProviderForm()))
        const mapped: ProviderFormErrors = {}
        for (const [k, m] of Object.entries(fields)) {
          const key = k === 'value' ? 'secret' : k.replace(/_micros$/, '').replace('input_price_per_mtok', 'input_price').replace('output_price_per_mtok', 'output_price')
          if (known.has(key)) mapped[key as keyof ProviderFormValues] = redactSecret(m, secret)
        }
        setErrors(mapped)
        if (!Object.keys(mapped).length) setFormError(msg)
      } else {
        setFormError(msg)
      }
    }
  }

  const kind = values.kind
  const local = deriveIsLocal(kind, values.base_url)
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !busy && onClose()}
      size="lg"
      title="Add model provider"
      description="Where agents get their language model. Local Ollama costs nothing; cloud providers need an API key."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="provider-form" loading={busy}>
            Add provider
          </Button>
        </>
      }
    >
      <form id="provider-form" onSubmit={submit} noValidate autoComplete="off" className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name" required error={errors.name}>
            {({ id, invalid, describedBy }) => <Input id={id} value={values.name} invalid={invalid} aria-describedby={describedBy} placeholder="e.g. Local Ollama" onChange={(e) => patch({ name: e.target.value })} />}
          </Field>
          <Field label="Kind" required error={errors.kind}>
            {({ id, invalid }) => (
              <Select id={id} value={kind} invalid={invalid} onChange={(e) => patch({ kind: e.target.value as ProviderFormValues['kind'] })}>
                {PROVIDER_KINDS.map((k) => (
                  <option key={k} value={k}>
                    {providerKindLabel(k)}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
        <Field
          label="Base URL"
          required={kind === 'ollama' || kind === 'openai_compatible'}
          error={errors.base_url}
          hint={local ? 'Runs on this machine: no provider charge.' : kind === 'ollama' || kind === 'openai_compatible' ? undefined : 'Optional. Leave blank to use the provider’s standard address.'}
        >
          {({ id, invalid, describedBy }) => (
            <Input id={id} inputMode="url" spellCheck={false} value={values.base_url} invalid={invalid} aria-describedby={describedBy} placeholder={BASE_URL_HINT[kind]} onChange={(e) => patch({ base_url: e.target.value })} />
          )}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Default model" error={errors.default_model} hint="Used by agents that do not name a model.">
            {({ id, invalid, describedBy }) => <Input id={id} value={values.default_model} invalid={invalid} aria-describedby={describedBy} placeholder="e.g. qwen2.5:7b" onChange={(e) => patch({ default_model: e.target.value })} />}
          </Field>
          <Field label="Context length (tokens)" error={errors.context_length} hint="What you expect the model to be served with.">
            {({ id, invalid, describedBy }) => <Input id={id} inputMode="numeric" value={values.context_length} invalid={invalid} aria-describedby={describedBy} placeholder="e.g. 32768" onChange={(e) => patch({ context_length: e.target.value })} />}
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Input price (USD per 1M tokens)" error={errors.input_price} hint={local ? 'Local models are free; leave blank.' : 'Blank means unknown, never $0.'}>
            {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={values.input_price} invalid={invalid} aria-describedby={describedBy} placeholder="e.g. 0.15" onChange={(e) => patch({ input_price: e.target.value })} />}
          </Field>
          <Field label="Output price (USD per 1M tokens)" error={errors.output_price} hint={local ? 'Local models are free; leave blank.' : 'Blank means unknown, never $0.'}>
            {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={values.output_price} invalid={invalid} aria-describedby={describedBy} placeholder="e.g. 0.60" onChange={(e) => patch({ output_price: e.target.value })} />}
          </Field>
        </div>

        {allowsCredential(kind) && (
          <fieldset className="space-y-3 rounded-md border border-line bg-raised p-3">
            <legend className="px-1 text-[12px] font-medium text-fg-2">API key {needsCredential(kind) ? '' : '(optional)'}</legend>
            {savedCredential ? (
              <p role="status" className="text-[12.5px] text-fg-2">
                Key saved as credential <strong className="text-fg">{savedCredential.name}</strong>. It cannot be shown again. Fix the error above and add the provider; the key will not be stored twice.
              </p>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Credential name" hint="A label for this key, e.g. openrouter-main.">
                  {({ id, describedBy }) => <Input id={id} aria-describedby={describedBy} value={values.credential_name} onChange={(e) => patch({ credential_name: e.target.value })} />}
                </Field>
                <Field label="Secret value" required={needsCredential(kind)} error={errors.secret}>
                  {({ id, invalid, describedBy }) => (
                    <Input id={id} type="password" autoComplete="off" spellCheck={false} data-lpignore="true" value={values.secret} invalid={invalid} aria-describedby={describedBy} placeholder="paste the key" onChange={(e) => patch({ secret: e.target.value })} />
                  )}
                </Field>
              </div>
            )}
            <p className="flex items-center gap-1.5 text-[12px] text-fg-3">
              <Lock className="size-3" aria-hidden /> Write-only. Stored encrypted on the server; never shown again, logged or given to agents.
            </p>
          </fieldset>
        )}
        <FormError message={formError} />
      </form>
    </Dialog>
  )
}
