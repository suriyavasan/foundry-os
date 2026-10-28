import { AlertTriangle, ArrowLeft, Lock } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  errorMessage,
  useAddAiConnection,
  useAiStack,
  useCreateCredential,
  useCreateModelProvider,
  useDetectProviders,
  useProviderModels,
  useProviderTest,
  useTestAiConnection,
  useUpdateModelProvider,
  type AiOption,
} from '@/api'
import type { ProviderTest } from '@/api/types'
import { Button, Dialog, Field, FormError, Input } from '@/components/ui'
import { MultiStepLoader } from '@/features/onboarding/fx/MultiStepLoader'
import { CLAUDE_PLAN_WARNING, OLLAMA_CARD, canTestProvider, cardFor, firstFailure, loaderSteps, providerFormFor, putFailure, testPassed, type ModelOption, type ProviderCardId } from '@/lib/aiSetup'
import { type AiGroup } from '@/lib/aiStack'
import { redactSecret, toCreateCredentialBody, toCreateProviderBody, validateProviderForm } from '@/lib/providers'
import { CliInstall } from './CliInstall'
import { ModelPicker } from './ModelPicker'
import { PlanSignIn } from './PlanSignIn'

const GROUPS: { group: AiGroup; title: string; blurb: string }[] = [
  { group: 'plan', title: 'Sign in with your plan', blurb: 'No API key. Foundry runs the official command-line tool signed in as you.' },
  { group: 'api', title: 'API keys', blurb: 'Paste a key. Billed by the provider.' },
  { group: 'local', title: 'On this computer', blurb: 'Free and private.' },
]

/** "Add AI": pick an option, then a short flow inside the dialog (install, sign in or key, model, test, add). */
export function AddAiDialog({ companyId, options, canEdit, onClose }: { companyId: string; options: AiOption[]; canEdit: boolean; onClose: () => void }) {
  const [picked, setPicked] = useState<AiOption | null>(null)
  // Keep the picked option fresh (an install flips `needs_install` once the stack is re-read).
  const live = picked ? (options.find((o) => o.option === picked.option) ?? picked) : null

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      size="md"
      title={live ? `Add ${live.label}` : 'Add an AI'}
      description={live ? undefined : 'Pick how Foundry should think. You can add several and change them at any time.'}
    >
      {!live ? (
        <div className="space-y-5">
          {GROUPS.map((g) => {
            const list = options.filter((o) => o.group === g.group)
            if (list.length === 0) return null
            return (
              <section key={g.group} aria-label={g.title}>
                <h3 className="text-[13px] font-semibold text-fg">{g.title}</h3>
                <p className="mb-2 text-[12px] text-fg-3">{g.blurb}</p>
                <ul className="grid gap-2 sm:grid-cols-2">
                  {list.map((o) => (
                    <li key={o.option}>
                      <button
                        type="button"
                        disabled={!o.available || !canEdit}
                        onClick={() => setPicked(o)}
                        className="w-full rounded-md border border-line-strong bg-surface px-3 py-2 text-left text-[13px] transition-colors hover:border-fg-3 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        <span className="block font-medium text-fg">{o.label}</span>
                        {!o.available && o.reason && <span className="mt-0.5 block text-[12px] text-fg-3">{o.reason}</span>}
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )
          })}
          {!canEdit && <p className="text-[12px] text-fg-3">Only an owner or admin can add an AI.</p>}
        </div>
      ) : (
        <div className="space-y-4">
          <Button size="sm" variant="ghost" icon={<ArrowLeft className="size-3.5" />} onClick={() => setPicked(null)}>
            Back to the list
          </Button>
          {live.group === 'plan' ? (
            <PlanFlow companyId={companyId} option={live} canEdit={canEdit} onDone={onClose} />
          ) : live.engine === 'cli' ? (
            <CliKeyFlow companyId={companyId} option={live} canEdit={canEdit} onDone={onClose} />
          ) : (
            <ApiFlow companyId={companyId} option={live} canEdit={canEdit} onDone={onClose} />
          )}
        </div>
      )}
    </Dialog>
  )
}

function Warning({ text }: { text: string }) {
  return (
    <div role="note" className="flex items-start gap-2 rounded-md border border-warning/40 bg-warning-subtle px-2.5 py-2 text-[12px] text-fg">
      <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
      <p>{text}</p>
    </div>
  )
}

/* -------------------------------- plan: install, add, sign in -------------------------------- */

function PlanFlow({ companyId, option, canEdit, onDone }: { companyId: string; option: AiOption; canEdit: boolean; onDone: () => void }) {
  const add = useAddAiConnection(companyId)
  const stack = useAiStack(companyId)
  const [addedId, setAddedId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const conn = addedId ? stack.data?.items.find((c) => c.id === addedId) : undefined
  const warning = option.warning ?? (option.option === 'claude_plan' ? CLAUDE_PLAN_WARNING : null)

  async function doAdd() {
    setError(null)
    try {
      const c = await add.mutateAsync({ option: option.option })
      if (c) setAddedId(c.id)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <div className="space-y-3">
      {warning && <Warning text={warning} />}
      {!addedId && option.needs_install && option.cli_id && (
        <CliInstall companyId={companyId} cliId={option.cli_id} name={option.label} command={option.install?.available ? option.install.command : null} canEdit={canEdit} />
      )}
      {!addedId && (
        <Button variant="primary" loading={add.isPending} disabled={!canEdit || option.needs_install} onClick={() => void doAdd()}>
          Add {option.label}
        </Button>
      )}
      {addedId && conn && (
        <>
          <p className="text-[12.5px] text-fg-2">Added. Now sign in so Foundry can use it.</p>
          <PlanSignIn companyId={companyId} conn={conn} canEdit={canEdit} />
        </>
      )}
      {addedId && (
        <Button onClick={onDone}>{conn?.status.signed_in ? 'Done' : 'Done, I will finish signing in later'}</Button>
      )}
      <FormError message={error} />
    </div>
  )
}

/* ------------------------------ CLI with an API key ------------------------------ */

/** Where each CLI's key comes from (from the vendors' own docs; no deep links that could go stale). */
const CLI_KEY_HINT: Partial<Record<AiOption['option'], string>> = {
  cursor_api: 'Create a user API key in the Cursor dashboard, under API Keys. Usage is billed to your Cursor account.',
  codex_api: 'Use an OpenAI API key from your OpenAI platform account. Usage is billed to that account.',
  antigravity_api: 'Use a Gemini API key from Google AI Studio. Usage is billed to that Google account.',
}

function CliKeyFlow({ companyId, option, canEdit, onDone }: { companyId: string; option: AiOption; canEdit: boolean; onDone: () => void }) {
  const createCredential = useCreateCredential(companyId)
  const add = useAddAiConnection(companyId)
  const test = useTestAiConnection(companyId)
  const [secret, setSecret] = useState('')
  const [model, setModel] = useState('')
  const [credentialId, setCredentialId] = useState<string | null>(null)
  const [addedId, setAddedId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  async function go() {
    setError(null)
    const typed = secret
    if (!credentialId && !typed.trim()) {
      setError('Paste the API key first.')
      return
    }
    setBusy(true)
    try {
      let cid = credentialId
      if (!cid) {
        cid = (await createCredential.mutateAsync({ name: `${option.cli_id ?? option.option}-key`, kind: 'api_key', value: typed.trim() })).id
        setCredentialId(cid)
        setSecret('') // the server has it; the browser does not need to keep it
      }
      let id = addedId
      if (!id) {
        const c = await add.mutateAsync({ option: option.option, credential_id: cid, model: model.trim() || null })
        id = c?.id ?? null
        setAddedId(id)
      }
      if (id) {
        const t = await test.mutateAsync(id)
        const ok = t?.status.last_test?.ok === true || t?.status.state === 'ready'
        setFailed(!ok)
        setResult(ok ? 'Test passed.' : `Test failed${t?.status.last_test?.detail ? `: ${t.status.last_test.detail}` : '.'} It was added; fix it and test again.`)
        if (ok) onDone()
      }
    } catch (e) {
      setError(redactSecret(errorMessage(e), typed))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      {option.warning && <Warning text={option.warning} />}
      {option.needs_install && option.cli_id ? (
        <CliInstall companyId={companyId} cliId={option.cli_id} name={option.label} command={option.install?.available ? option.install.command : null} canEdit={canEdit} />
      ) : (
        <form
          className="space-y-4"
          autoComplete="off"
          onSubmit={(e) => {
            e.preventDefault()
            if (canEdit && !busy) void go()
          }}
        >
          <Field label="API key" required hint={CLI_KEY_HINT[option.option]}>
            {({ id, describedBy }) => (
              <div>
                <Input
                  id={id}
                  className="h-10"
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  data-lpignore="true"
                  aria-describedby={describedBy}
                  value={secret}
                  placeholder={credentialId ? 'Saved. Type a new key only to replace it' : 'Paste the key'}
                  onChange={(e) => setSecret(e.target.value)}
                />
                <p className="mt-1.5 flex items-center gap-1.5 text-[12px] text-fg-3">
                  <Lock className="size-3" aria-hidden /> Write-only. Stored encrypted on the server; never shown again, logged or given to agents.
                </p>
              </div>
            )}
          </Field>
          <Field label="Model" hint="Optional. Leave empty to use the tool's default.">
            {({ id, describedBy }) => <Input id={id} className="h-10" aria-describedby={describedBy} value={model} disabled={!!addedId} onChange={(e) => setModel(e.target.value)} />}
          </Field>
          <div className="flex items-center gap-3">
            <Button type="submit" variant="primary" loading={busy} disabled={!canEdit}>
              {addedId ? 'Test again' : 'Add and test'}
            </Button>
            {addedId && failed && (
              <Button variant="ghost" onClick={onDone}>
                Close
              </Button>
            )}
          </div>
        </form>
      )}
      {result && (
        <p role={failed ? 'alert' : 'status'} className={'text-[12.5px] ' + (failed ? 'text-danger' : 'text-success')}>
          {result}
        </p>
      )}
      <FormError message={error} />
    </div>
  )
}

/* ------------------------------ API providers and Ollama ------------------------------ */

function ApiFlow({ companyId, option, canEdit, onDone }: { companyId: string; option: AiOption; canEdit: boolean; onDone: () => void }) {
  const kind = (option.provider_kind ?? 'openai_compatible') as ProviderCardId
  const card = cardFor(kind)
  const detect = useDetectProviders(companyId)
  const createCredential = useCreateCredential(companyId)
  const createProvider = useCreateModelProvider(companyId)
  const updateProvider = useUpdateModelProvider(companyId)
  const providerTest = useProviderTest(companyId)
  const add = useAddAiConnection(companyId)
  const testConn = useTestAiConnection(companyId)

  const [typedBaseUrl, setBaseUrl] = useState<string | null>(null)
  const [model, setModel] = useState('')
  const [secret, setSecret] = useState('')
  const [contextLength, setContextLength] = useState('')
  const [created, setCreated] = useState<{ providerId: string; credentialId: string | null; baseUrl: string; model: string } | null>(null)
  const [test, setTest] = useState<ProviderTest | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<{ model?: string; baseUrl?: string; secret?: string }>({})

  const probed = useRef(false)
  const { mutate: runDetect } = detect
  useEffect(() => {
    if (kind !== 'ollama' || probed.current) return
    probed.current = true
    runDetect()
  }, [kind, runDetect])
  const detected = detect.data && detect.data.reachable ? detect.data : null
  // Ollama: until the person types an address, use the one the detection found (or the usual local one).
  const baseUrl = typedBaseUrl ?? (kind === 'ollama' ? (detected?.base_url ?? OLLAMA_CARD.baseUrlHint) : '')

  const models = useProviderModels(companyId, created?.providerId)
  const modelOptions: ModelOption[] = useMemo(() => {
    if (models.data && models.data.length > 0) return models.data
    if (kind === 'ollama' && detected) return detected.models.map((m) => ({ id: m.name, context_length: m.context_length }))
    return []
  }, [models.data, kind, detected])

  const canTest = canEdit && !busy && canTestProvider(card, { baseUrl, model, secret, hasCredential: !!created?.credentialId })

  async function run() {
    setError(null)
    setTest(null)
    const typed = secret
    try {
      const modelName = model.trim()
      const form = providerFormFor(card, { baseUrl, model: modelName, secret, contextLength })
      const errs = validateProviderForm(form, { hasCredential: !!created?.credentialId })
      const mapped = { model: errs.default_model, baseUrl: errs.base_url, secret: errs.secret }
      setFieldErrors(mapped)
      if (mapped.model || mapped.baseUrl || mapped.secret) return
      setBusy(true)
      let pid: string
      const reuse = created && created.baseUrl === baseUrl.trim() && !secret.trim()
      if (reuse) {
        pid = created.providerId
        if (created.model !== modelName) await updateProvider.mutateAsync({ id: pid, body: { default_model: modelName } })
        setCreated({ ...created, model: modelName })
      } else {
        if (created) await updateProvider.mutateAsync({ id: created.providerId, body: { enabled: false } }).catch(() => undefined)
        let credentialId: string | null = null
        const credBody = toCreateCredentialBody(form)
        if (credBody) {
          credentialId = (await createCredential.mutateAsync(credBody)).id
          setSecret('')
        }
        pid = (await createProvider.mutateAsync(toCreateProviderBody(form, credentialId))).id
        setCreated({ providerId: pid, credentialId, baseUrl: baseUrl.trim(), model: modelName })
      }
      const result = await providerTest.mutateAsync(pid)
      setTest(result)
      if (!testPassed(result)) return
      const conn = await add.mutateAsync({ option: option.option, model_provider_id: pid, model: modelName })
      if (conn && conn.status.state === 'needs_test') await testConn.mutateAsync(conn.id).catch(() => undefined)
      onDone()
    } catch (err) {
      setError(redactSecret(putFailure(err), typed))
    } finally {
      setBusy(false)
    }
  }

  const failure = firstFailure(test)

  return (
    <form
      className="space-y-4"
      autoComplete="off"
      onSubmit={(e) => {
        e.preventDefault()
        if (canTest) void run()
      }}
    >
      {option.warning && <Warning text={option.warning} />}
      {kind === 'ollama' && !detected && detect.isSuccess && <p className="text-[12.5px] text-fg-3">No Ollama found at that address yet. Start it, or type its address below.</p>}
      {card.needsBaseUrl && (
        <Field label="Base URL" required error={fieldErrors.baseUrl} hint={kind === 'ollama' ? 'Runs on this computer: free and private.' : 'The API address, usually ending in /v1.'}>
          {({ id, invalid, describedBy }) => (
            <Input id={id} className="h-10" inputMode="url" spellCheck={false} value={baseUrl} invalid={invalid} aria-describedby={describedBy} placeholder={card.baseUrlHint} onChange={(e) => setBaseUrl(e.target.value)} />
          )}
        </Field>
      )}
      {(card.needsKey || kind === 'openai_compatible') && (
        <Field label={card.keyLabel} required={card.needsKey} error={fieldErrors.secret}>
          {({ id, invalid, describedBy }) => (
            <div>
              <Input
                id={id}
                className="h-10"
                type="password"
                autoComplete="off"
                spellCheck={false}
                data-lpignore="true"
                value={secret}
                invalid={invalid}
                aria-describedby={describedBy}
                placeholder={created?.credentialId ? 'Saved. Type a new key only to replace it' : 'Paste the key'}
                onChange={(e) => setSecret(e.target.value)}
              />
              <p className="mt-1.5 flex items-center gap-1.5 text-[12px] text-fg-3">
                <Lock className="size-3" aria-hidden /> Write-only. Stored encrypted on the server; never shown again, logged or given to agents.
              </p>
            </div>
          )}
        </Field>
      )}
      <Field label="Model" required error={fieldErrors.model} hint={modelOptions.length > 0 ? 'Pick from what the provider offers, or type another name.' : 'The model Foundry should use.'}>
        {({ id, invalid, describedBy }) => (
          <ModelPicker
            id={id}
            value={model}
            onChange={(v) => {
              setModel(v)
              setTest(null)
            }}
            options={modelOptions}
            loading={models.isFetching && !!created}
            suggestions={card.modelSuggestions}
            invalid={invalid}
            describedBy={describedBy}
            disabled={!canEdit || busy}
          />
        )}
      </Field>
      {kind === 'openai_compatible' && (
        <Field label="Context length (tokens)" hint="Optional. What the server is set up to serve; the test measures it either way.">
          {({ id }) => <Input id={id} className="h-10" inputMode="numeric" value={contextLength} placeholder="e.g. 32768" onChange={(e) => setContextLength(e.target.value)} />}
        </Field>
      )}
      <div className="flex items-center gap-3">
        <Button type="submit" variant="primary" loading={busy} disabled={!canTest}>
          {test ? 'Test again and add' : 'Test and add'}
        </Button>
        {!canEdit && <p className="text-[12px] text-fg-3">Only an owner or admin can add an AI.</p>}
      </div>
      {(busy || test) && (
        <div className="rounded-lg border border-line bg-surface p-4">
          <MultiStepLoader steps={loaderSteps(busy ? null : test, busy)} />
          {test && !testPassed(test) && (
            <p role="alert" className="mt-3 text-[12.5px] text-danger">
              {failure ? `${failure.name.replace(/_/g, ' ')} failed${failure.detail ? `: ${failure.detail}` : ''}. ` : ''}
              Fix it and test again. Nothing is added until every check passes.
            </p>
          )}
        </div>
      )}
      <FormError message={error} />
    </form>
  )
}
