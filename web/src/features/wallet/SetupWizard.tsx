import { useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Lock } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import {
  ApiError,
  errorMessage,
  isNotDeployed,
  qk,
  useCreateConnectedAccount,
  useCreateProviderSecret,
  useUpdatePolicy,
  useWalletProviders,
} from '@/api'
import {
  useCheckCardAccount,
  useCreateCardAccount,
  type CardAccount,
  type CardAccountCheck,
  type CardProviderInfo,
  type CompanyKind,
  type WalletOverview,
} from '@/api/wallet'
import type { ConnectedAccountCreate, UpdatePolicyRequest } from '@/api/types'
import { Badge, Button, Card, CardBody, CardHeader, ErrorState, Field, FormError, Input, LoadingState, NotAvailable, Select } from '@/components/ui'
import { scrubSecrets } from '@/lib/connectedAccounts'
import { formatUsd } from '@/lib/money'
import { diffM15, validateM15, type M15FormValues, type PolicyFormErrors } from '@/features/settings/policyForm'
import { ProviderGuide } from './ProviderGuide'
import {
  CARD_BRANDS,
  COMPANY_KINDS,
  NO_CARD_NUMBER_MESSAGE,
  manualAccountBody,
  recommendProvider,
  selectableProviders,
  validateManualCard,
  type ManualCardErrors,
  type ManualCardForm,
} from './walletLogic'

type Step = 'where' | 'recommend' | 'todo' | 'connect' | 'limits' | 'done'
const STEPS: Array<{ id: Step; label: string }> = [
  { id: 'where', label: 'Your company' },
  { id: 'recommend', label: 'Provider' },
  { id: 'todo', label: 'What you do' },
  { id: 'connect', label: 'Connect' },
  { id: 'limits', label: 'Limits' },
  { id: 'done', label: 'Done' },
]

/** Suggested values from the plan: automatic approval off, the AI Co-founder up to $50 a charge and $200 a month, recurring and new vendors to you. */
function defaultLimits(wallet: WalletOverview): M15FormValues {
  const r = wallet.setup?.recommended_policy
  const usd = (micros: number) => String(micros / 1_000_000)
  return {
    purchasing_enabled: true,
    purchase_auto_approve_micros: r ? usd(r.purchase_auto_approve_micros) : '0',
    purchase_cofounder_limit_micros: r ? usd(r.purchase_cofounder_limit_micros) : '50',
    purchase_cofounder_monthly_micros: r ? usd(r.purchase_cofounder_monthly_micros) : '200',
    purchase_monthly_budget_micros: '',
    purchase_recurring_requires_human: r ? r.purchase_recurring_requires_human : true,
    purchase_new_vendor_requires_human: r ? r.purchase_new_vendor_requires_human : true,
  }
}

/**
 * "Set up agent cards". Asks where the company is incorporated, recommends the provider that actually works there (with
 * the provider's own honest eligibility text), lists what only you can do (KYC, funding, OTP), then connects: a restricted
 * key for a provider, or for your own card only a name, the network and the LAST FOUR digits. A full card number is
 * refused in the browser and never sent. Finally it sets the purchasing limits and turns purchasing on.
 */
export function SetupWizard({ companyId, wallet, onDone }: { companyId: string; wallet: WalletOverview; onDone: () => void }) {
  const providers = useWalletProviders(companyId)
  const [step, setStep] = useState<Step>('where')
  const [kind, setKind] = useState<CompanyKind | null>(null)
  const [haveMercury, setHaveMercury] = useState(false)
  const [chosen, setChosen] = useState<string | null>(null)
  const [checked, setChecked] = useState<Record<string, Set<number>>>({})
  const [account, setAccount] = useState<CardAccount | null>(null)

  const catalog = providers.data?.items ?? []
  const rec = kind ? recommendProvider(kind, catalog, { haveMercury }) : null
  const provider: CardProviderInfo | null = (chosen ? selectableProviders(catalog).find((p) => p.id === chosen && p.available) : null) ?? rec?.provider ?? null
  const index = STEPS.findIndex((s) => s.id === step)

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Set up agent cards" description="Agents ask to buy things; you stay in charge of the money. This takes a few minutes, and the parts only you can do are marked." />
        <CardBody className="space-y-4">
          <ol className="flex flex-wrap gap-x-4 gap-y-1 text-[12px]" aria-label="Setup steps">
            {STEPS.map((s, i) => (
              <li key={s.id} aria-current={s.id === step ? 'step' : undefined} className={i === index ? 'font-semibold text-fg' : i < index ? 'text-fg-2' : 'text-fg-3'}>
                {i + 1}. {s.label}
              </li>
            ))}
          </ol>
          {wallet.enabled && step === 'where' && <p className="rounded-md border border-line bg-raised px-3 py-2 text-[12.5px] text-fg-2">Purchasing is already on. You can add another account here.</p>}

          {step === 'where' && (
            <WhereStep
              kind={kind}
              haveMercury={haveMercury}
              onKind={(k) => {
                setKind(k)
                setChosen(null)
              }}
              onHaveMercury={setHaveMercury}
              onNext={() => setStep('recommend')}
            />
          )}

          {step !== 'where' && providers.isPending && <LoadingState rows={3} label="Loading providers" />}
          {step !== 'where' && providers.isError &&
            (isNotDeployed(providers.error) ? (
              <NotAvailable compact milestone="M15" description="The provider catalog is not served by this backend." />
            ) : (
              <ErrorState compact error={providers.error} onRetry={() => void providers.refetch()} retrying={providers.isFetching} />
            ))}

          {step !== 'where' && providers.data && !provider && <p className="text-[13px] text-fg-3">No card provider is available on this backend yet.</p>}

          {step === 'recommend' && provider && rec && (
            <div className="space-y-3">
              <div className="rounded-md border border-line bg-raised p-3" role="group" aria-label="Recommendation">
                <p className="flex flex-wrap items-center gap-2 text-[14px] font-semibold text-fg">
                  {provider.label}
                  <Badge tone="accent">{provider.id === rec.provider.id ? 'Recommended' : 'Your choice'}</Badge>
                </p>
                <p className="mt-1 text-[13px] text-fg-2">{provider.id === rec.provider.id ? rec.reason : 'You chose this one yourself.'}</p>
                <p className="mt-1.5 text-[12.5px] text-fg-3">{provider.eligibility}</p>
              </div>
              {rec.alternatives.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-fg-3">
                  <span>Other options:</span>
                  {rec.alternatives
                    .concat(provider.id === rec.provider.id ? [] : [rec.provider])
                    .filter((p) => p.id !== provider.id)
                    .map((p) => (
                      <Button key={p.id} size="sm" onClick={() => setChosen(p.id)}>
                        Use {p.label} instead
                      </Button>
                    ))}
                </div>
              )}
              <StepNav onBack={() => setStep('where')} onNext={() => setStep('todo')} nextLabel="Show me what to do" />
            </div>
          )}

          {step === 'todo' && provider && (
            <div className="space-y-3">
              <ProviderGuide
                provider={provider}
                guideUpdatedOn={providers.data?.guide_updated_on}
                checked={checked[provider.id] ?? new Set()}
                onToggle={(i) =>
                  setChecked((c) => {
                    const next = new Set(c[provider.id] ?? [])
                    if (next.has(i)) next.delete(i)
                    else next.add(i)
                    return { ...c, [provider.id]: next }
                  })
                }
              />
              <p className="text-[12.5px] text-fg-3">
                {(checked[provider.id]?.size ?? 0)} of {provider.steps.length} done. You can connect now and finish the rest later; agents cannot pay until the account is funded.
              </p>
              <StepNav onBack={() => setStep('recommend')} onNext={() => setStep('connect')} nextLabel="Connect" />
            </div>
          )}

          {step === 'connect' && provider &&
            (provider.id === 'manual' ? (
              <ManualCardStep
                companyId={companyId}
                onBack={() => setStep('todo')}
                onCreated={(a) => {
                  setAccount(a)
                  setStep('limits')
                }}
              />
            ) : (
              <ConnectProviderStep
                companyId={companyId}
                provider={provider}
                onBack={() => setStep('todo')}
                onConnected={(a) => {
                  setAccount(a)
                  setStep('limits')
                }}
              />
            ))}

          {step === 'limits' && <LimitsStep companyId={companyId} wallet={wallet} onBack={() => setStep('connect')} onSaved={() => setStep('done')} />}

          {step === 'done' && (
            <div className="space-y-3" role="status">
              <p className="flex items-center gap-2 text-[14px] font-semibold text-fg">
                <CheckCircle2 className="size-4 text-success" aria-hidden />
                Purchasing is on
              </p>
              <p className="text-[13px] text-fg-2">
                {account ? `${account.label} is connected. ` : ''}Agents can now ask to buy things. Each request is approved by the band you set, and you pay at checkout when it needs you. Nothing is ever charged without an approval, and card numbers never come through Foundry AI OS.
              </p>
              <Button variant="primary" onClick={onDone}>
                Go to overview
              </Button>
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  )
}

function StepNav({ onBack, onNext, nextLabel, nextDisabled, loading }: { onBack?: () => void; onNext: () => void; nextLabel: string; nextDisabled?: boolean; loading?: boolean }) {
  return (
    <div className="flex justify-between gap-2 pt-1">
      {onBack ? (
        <Button variant="ghost" onClick={onBack}>
          Back
        </Button>
      ) : (
        <span />
      )}
      <Button variant="primary" onClick={onNext} disabled={nextDisabled} loading={loading}>
        {nextLabel}
      </Button>
    </div>
  )
}

function WhereStep({ kind, haveMercury, onKind, onHaveMercury, onNext }: { kind: CompanyKind | null; haveMercury: boolean; onKind: (k: CompanyKind) => void; onHaveMercury: (v: boolean) => void; onNext: () => void }) {
  return (
    <div className="space-y-3">
      <fieldset>
        <legend className="mb-1.5 text-[13px] font-medium text-fg">Where is your company incorporated?</legend>
        <div className="grid gap-1.5 sm:grid-cols-2">
          {COMPANY_KINDS.map((k) => (
            <label key={k.value} className="flex cursor-pointer items-start gap-2 rounded-md border border-line bg-raised px-2.5 py-2 text-[13px] hover:bg-hover">
              <input type="radio" name="company-kind" className="mt-0.5 size-3.5 accent-(--accent)" checked={kind === k.value} onChange={() => onKind(k.value)} />
              <span>
                <span className="block text-fg">{k.label}</span>
                <span className="block text-[12px] text-fg-3">{k.hint}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      {kind === 'us_company' && (
        <label className="flex cursor-pointer items-start gap-2 rounded-md border border-line bg-raised px-2.5 py-2 text-[13px] hover:bg-hover">
          <input type="checkbox" className="mt-0.5 size-3.5 accent-(--accent)" checked={haveMercury} onChange={(e) => onHaveMercury(e.target.checked)} />
          <span>
            <span className="block text-fg">I already bank with Mercury</span>
            <span className="block text-[12px] text-fg-3">Otherwise Stripe Issuing is recommended (it also works if you already have a Stripe account).</span>
          </span>
        </label>
      )}
      <StepNav onNext={onNext} nextLabel="Next" nextDisabled={!kind} />
    </div>
  )
}

function ManualCardStep({ companyId, onBack, onCreated }: { companyId: string; onBack: () => void; onCreated: (a: CardAccount) => void }) {
  const create = useCreateCardAccount(companyId)
  const [form, setForm] = useState<ManualCardForm>({ label: '', brand: '', last4: '' })
  const [errors, setErrors] = useState<ManualCardErrors>({})
  const [formError, setFormError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const found = validateManualCard(form)
    if (Object.values(found).some((m) => m === NO_CARD_NUMBER_MESSAGE)) {
      // Do not leave a pasted card number sitting in the page.
      setForm((f) => ({ label: found.label === NO_CARD_NUMBER_MESSAGE ? '' : f.label, brand: found.brand === NO_CARD_NUMBER_MESSAGE ? '' : f.brand, last4: found.last4 === NO_CARD_NUMBER_MESSAGE ? '' : f.last4 }))
    }
    setErrors(found)
    if (Object.keys(found).length) return
    try {
      onCreated(await create.mutateAsync(manualAccountBody(form)))
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const fe = err.fieldErrors
        setErrors({ label: fe.label, brand: fe.manual_brand, last4: fe.manual_last4 })
        if (!fe.label && !fe.manual_brand && !fe.manual_last4) setFormError(err.message)
      } else setFormError(errorMessage(err))
    }
  }

  return (
    <form onSubmit={submit} noValidate autoComplete="off" className="space-y-3" aria-label="Register your own card">
      <p className="flex items-start gap-2 rounded-md border border-line bg-raised px-3 py-2 text-[12.5px] text-fg-2">
        <Lock className="mt-0.5 size-3.5 shrink-0 text-fg-3" aria-hidden />
        <span>Foundry AI OS only needs a name, the network and the last 4 digits, so it can tell you which card to use. Never enter the full card number: it is refused and never sent.</span>
      </p>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Card name" required error={errors.label} hint='For example "HDFC corporate card".'>
          {({ id, invalid, describedBy }) => <Input id={id} value={form.label} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))} />}
        </Field>
        <Field label="Card network" required error={errors.brand}>
          {({ id, invalid, describedBy }) => (
            <Select id={id} invalid={invalid} aria-describedby={describedBy} value={form.brand} onChange={(e) => setForm((f) => ({ ...f, brand: e.target.value }))}>
              <option value="">Choose…</option>
              {CARD_BRANDS.map((b) => (
                <option key={b} value={b}>
                  {b}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Last 4 digits" required error={errors.last4} hint="Only the last four.">
          {({ id, invalid, describedBy }) => <Input id={id} inputMode="numeric" autoComplete="off" value={form.last4} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setForm((f) => ({ ...f, last4: e.target.value }))} />}
        </Field>
      </div>
      <FormError message={formError} />
      <div className="flex justify-between gap-2 pt-1">
        <Button variant="ghost" onClick={onBack}>
          Back
        </Button>
        <Button variant="primary" type="submit" loading={create.isPending}>
          Register card
        </Button>
      </div>
    </form>
  )
}

function ConnectProviderStep({ companyId, provider, onBack, onConnected }: { companyId: string; provider: CardProviderInfo; onBack: () => void; onConnected: (a: CardAccount) => void }) {
  const secret = useCreateProviderSecret(companyId)
  const connect = useCreateConnectedAccount(companyId)
  const createAccount = useCreateCardAccount(companyId)
  const check = useCheckCardAccount(companyId)
  const [label, setLabel] = useState(`${provider.label} agent cards`)
  const [mode, setMode] = useState<'live' | 'test'>(provider.capabilities.test_mode ? 'test' : 'live')
  const [key, setKey] = useState('')
  const [errors, setErrors] = useState<{ label?: string; key?: string }>({})
  const [formError, setFormError] = useState<string | null>(null)
  // Ids created by an earlier attempt, so a retry never stores a secret or an account twice.
  const [credentialId, setCredentialId] = useState<string | null>(null)
  const [connectedId, setConnectedId] = useState<string | null>(null)
  const [account, setAccount] = useState<CardAccount | null>(null)
  const [result, setResult] = useState<CardAccountCheck | null>(null)
  const busy = secret.isPending || connect.isPending || createAccount.isPending || check.isPending

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const next: typeof errors = {}
    if (!label.trim()) next.label = 'Give the account a name'
    if (!account && !credentialId && !key.trim()) next.key = 'Paste the restricted key'
    setErrors(next)
    if (Object.keys(next).length) return
    const typed = [key]
    try {
      let acct = account
      if (!acct) {
        let cid = credentialId
        if (!cid) {
          const cred = await secret.mutateAsync({ name: `${provider.id}-agent-cards`, kind: 'token', value: key.trim() })
          cid = cred.id
          setCredentialId(cid)
          setKey('') // the server has it; the browser does not need to keep it
        }
        let connId = connectedId
        if (!connId) {
          const body = { provider: provider.id, label: label.trim(), credential_id: cid } as unknown as ConnectedAccountCreate
          connId = (await connect.mutateAsync(body)).id
          setConnectedId(connId)
        }
        acct = await createAccount.mutateAsync({ provider: provider.id, label: label.trim(), mode, connected_account_id: connId, make_default: true })
        setAccount(acct)
      }
      // A pasted wrong key must not look connected: check it right away.
      const r = await check.mutateAsync(acct.id)
      setResult(r)
    } catch (err) {
      const msg = scrubSecrets(errorMessage(err), typed)
      if (err instanceof ApiError && err.status === 422) {
        const fe = err.fieldErrors
        setErrors({ label: fe.label ? scrubSecrets(fe.label, typed) : undefined, key: fe.value ? scrubSecrets(fe.value, typed) : undefined })
        if (!fe.label && !fe.value) setFormError(msg)
      } else setFormError(msg)
    }
  }

  const connected = !!account && result?.ok === true

  return (
    <form onSubmit={submit} noValidate autoComplete="off" className="space-y-3" aria-label={`Connect ${provider.label}`}>
      <p className="flex items-start gap-2 rounded-md border border-line bg-raised px-3 py-2 text-[12.5px] text-fg-2">
        <Lock className="mt-0.5 size-3.5 shrink-0 text-fg-3" aria-hidden />
        <span>The key goes once to the encrypted credential store and is never shown again, never sent to agents and never written to logs. Use a restricted key, with only the permissions in the guide.</span>
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Account name" required error={errors.label}>
          {({ id, invalid, describedBy }) => <Input id={id} value={label} invalid={invalid} aria-describedby={describedBy} disabled={!!account} onChange={(e) => setLabel(e.target.value)} />}
        </Field>
        {provider.capabilities.test_mode && (
          <Field label="Mode" hint="Test with a sandbox key first: no real money moves.">
            {({ id, describedBy }) => (
              <Select id={id} aria-describedby={describedBy} value={mode} disabled={!!account} onChange={(e) => setMode(e.target.value as 'live' | 'test')}>
                <option value="test">Test (sandbox key)</option>
                <option value="live">Live</option>
              </Select>
            )}
          </Field>
        )}
        {!account && (
          <Field label={`${provider.label} restricted key`} required error={errors.key} className="sm:col-span-2" hint="Paste it here. It is hidden as you type.">
            {({ id, invalid, describedBy }) => (
              <Input id={id} type="password" autoComplete="off" spellCheck={false} value={key} invalid={invalid} aria-describedby={describedBy} placeholder="paste the key" onChange={(e) => setKey(e.target.value)} />
            )}
          </Field>
        )}
      </div>
      {result && (
        <p role="status" className={result.ok ? 'text-[13px] text-success' : 'text-[13px] text-danger'}>
          {result.ok ? `Connected. ${result.mode === 'test' ? 'This is a test-mode account.' : ''}` : `The provider rejected the check. ${result.message ?? ''}`}
          {result.ok && result.balance_micros != null ? ` Balance ${formatUsd(result.balance_micros)}.` : ''}
          {result.ok && result.issuing_enabled === false ? ' Issuing is not enabled on this account yet: finish the steps in the guide.' : ''}
        </p>
      )}
      <FormError message={formError} />
      <div className="flex justify-between gap-2 pt-1">
        <Button variant="ghost" onClick={onBack} disabled={busy}>
          Back
        </Button>
        {connected ? (
          <Button variant="primary" onClick={() => onConnected(account!)}>
            Continue
          </Button>
        ) : (
          <Button variant="primary" type="submit" loading={busy}>
            {account ? 'Check again' : 'Connect and check'}
          </Button>
        )}
      </div>
    </form>
  )
}

function LimitsStep({ companyId, wallet, onBack, onSaved }: { companyId: string; wallet: WalletOverview; onBack: () => void; onSaved: () => void }) {
  const update = useUpdatePolicy(companyId)
  const qc = useQueryClient()
  const [values, setValues] = useState<M15FormValues>(() => defaultLimits(wallet))
  const [errors, setErrors] = useState<PolicyFormErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const set = (p: Partial<M15FormValues>) => {
    setValues((v) => ({ ...v, ...p }))
    setErrors({})
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const found = validateM15(values)
    setErrors(found)
    if (Object.keys(found).length) return
    try {
      await update.mutateAsync({ ...diffM15(values), purchasing_enabled: true } as UpdatePolicyRequest)
      void qc.invalidateQueries({ queryKey: qk.walletAll(companyId) })
      onSaved()
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const fe = err.fieldErrors
        setErrors(fe)
        setFormError(Object.keys(fe).length ? 'Some fields need attention.' : err.message)
      } else setFormError(errorMessage(err))
    }
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-3" aria-label="Purchase limits">
      <p className="text-[13px] text-fg-2">Who may approve what. You can change all of this later in Settings. Anything above a band goes to you.</p>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Approve automatically up to (USD per charge)" error={errors.purchase_auto_approve_micros} hint="0 = every purchase needs the AI Co-founder or you. Ceiling $100.">
          {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={values.purchase_auto_approve_micros} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set({ purchase_auto_approve_micros: e.target.value })} />}
        </Field>
        <Field label="AI Co-founder may approve up to (USD per charge)" error={errors.purchase_cofounder_limit_micros} hint="Above this you decide. Ceiling $1,000.">
          {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={values.purchase_cofounder_limit_micros} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set({ purchase_cofounder_limit_micros: e.target.value })} />}
        </Field>
        <Field label="AI Co-founder total per month (USD)" error={errors.purchase_cofounder_monthly_micros}>
          {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={values.purchase_cofounder_monthly_micros} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set({ purchase_cofounder_monthly_micros: e.target.value })} />}
        </Field>
        <Field label="Purchase budget per month (USD)" error={errors.purchase_monthly_budget_micros} hint="A hard cap on committed plus charged. Blank = no cap.">
          {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={values.purchase_monthly_budget_micros} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set({ purchase_monthly_budget_micros: e.target.value })} />}
        </Field>
      </div>
      <div className="grid gap-1.5 sm:grid-cols-2">
        <label className="flex cursor-pointer items-start gap-2 rounded-md border border-line bg-raised px-2.5 py-2 text-[13px] hover:bg-hover">
          <input type="checkbox" className="mt-0.5 size-3.5 accent-(--accent)" checked={values.purchase_recurring_requires_human ?? true} onChange={(e) => set({ purchase_recurring_requires_human: e.target.checked })} />
          <span>
            <span className="block text-fg">Starting a subscription needs me</span>
            <span className="block text-[12px] text-fg-3">Recommended.</span>
          </span>
        </label>
        <label className="flex cursor-pointer items-start gap-2 rounded-md border border-line bg-raised px-2.5 py-2 text-[13px] hover:bg-hover">
          <input type="checkbox" className="mt-0.5 size-3.5 accent-(--accent)" checked={values.purchase_new_vendor_requires_human ?? true} onChange={(e) => set({ purchase_new_vendor_requires_human: e.target.checked })} />
          <span>
            <span className="block text-fg">A new vendor needs me</span>
            <span className="block text-[12px] text-fg-3">Recommended.</span>
          </span>
        </label>
      </div>
      <FormError message={formError} />
      <div className="flex justify-between gap-2 pt-1">
        <Button variant="ghost" onClick={onBack} disabled={update.isPending}>
          Back
        </Button>
        <Button variant="primary" type="submit" loading={update.isPending}>
          Turn on purchasing
        </Button>
      </div>
    </form>
  )
}
