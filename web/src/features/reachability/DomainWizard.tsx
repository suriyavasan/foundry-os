import clsx from 'clsx'
import { ArrowLeft, ExternalLink, Search, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { errorMessage, useCreateRegistration, useDomainPlan, useDomainRegistries, useDomainSearch, useFreeDomainProviders, usePreviewRegistration, useVerifyDomain } from '@/api'
import type { DomainAvailability, DomainRegistration, DomainRegistrationPreview, DomainRegistry, FreeDomainProvider } from '@/api/types'
import { Badge, Boundary, Button, CostKindBadge, Dialog, Field, FormError, Input, SectionLabel, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { formatUsd } from '@/lib/money'
import { PATH_INFO, STEP_TITLE, choosePath, createsApproval, fqdnFor, initialWizard, isLastStep, nextStep, prevStep, previewRequest, registrationRequest, stepBlocker, submitLabel, validateDomain, validateLabel, type WizardState, type WizardStepId } from '@/lib/domainWizard'
import { DnsRecordsTable } from './DomainCard'

/** Only a plain https link from the server is shown as a link. */
const safeHttps = (u: string | null | undefined): string | null => {
  try {
    return u && new URL(u).protocol === 'https:' ? u : null
  } catch {
    return null
  }
}

function Choice({ selected, onSelect, disabled, children, label }: { selected: boolean; onSelect: () => void; disabled?: boolean; children: React.ReactNode; label: string }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      aria-label={label}
      disabled={disabled}
      onClick={onSelect}
      className={clsx('w-full rounded-md border p-3 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50', selected ? 'border-accent bg-accent-subtle' : 'border-line bg-surface hover:bg-hover')}
    >
      {children}
    </button>
  )
}

function Eligibility({ items }: { items: string[] }) {
  if (items.length === 0) return null
  return (
    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[12px] text-fg-3">
      {items.map((e, i) => (
        <li key={i}>{e}</li>
      ))}
    </ul>
  )
}

/**
 * "Get a domain": three clearly separate paths. Nothing is registered or bought here: the last step creates a
 * registration, and for the GitHub and paid paths the server turns it into a human approval that authorises exactly
 * what was previewed.
 */
export function DomainWizard({ companyId, onClose }: { companyId: string; onClose: () => void }) {
  const { openPanel } = useCanvas()
  const toast = useToast()
  const [state, setState] = useState<WizardState>(initialWizard)
  const [step, setStep] = useState<WizardStepId>('path')
  const [preview, setPreview] = useState<DomainRegistrationPreview | null>(null)
  const [result, setResult] = useState<DomainRegistration | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [term, setTerm] = useState('')
  const [submittedTerm, setSubmittedTerm] = useState('')

  const registriesQ = useDomainRegistries(companyId, state.path === 'github_registry')
  const providersQ = useFreeDomainProviders(companyId, state.path === 'free_provider')
  const searchQ = useDomainSearch(companyId, submittedTerm)
  const previewM = usePreviewRegistration(companyId)
  const createM = useCreateRegistration(companyId)
  const busy = previewM.isPending || createM.isPending

  const registries = registriesQ.data ?? []
  const providers = providersQ.data ?? []
  const registry = registries.find((r) => r.id === state.registryId) ?? null
  const provider = providers.find((p) => p.id === state.providerId) ?? null
  const blocker = stepBlocker(step, state, { registries, providers })
  const last = isLastStep(state, step)
  const patch = (p: Partial<WizardState>) => {
    setState((s) => ({ ...s, ...p }))
    setError(null)
  }

  async function next() {
    setError(null)
    if (step === 'name') {
      const body = previewRequest(state)
      if (!body) return
      try {
        setPreview(await previewM.mutateAsync(body))
        setStep('preview')
      } catch (e) {
        setError(errorMessage(e))
      }
      return
    }
    if (last) return submit()
    const n = nextStep(state, step)
    if (n) setStep(n)
  }

  async function submit() {
    const body = registrationRequest(state)
    if (!body) return
    try {
      const reg = await createM.mutateAsync(body)
      setResult(reg)
      if (createsApproval(state.path)) toast.success('Approval requested')
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  function back() {
    const p = prevStep(state, step)
    if (p) {
      setStep(p)
      setError(null)
    }
  }

  const footer = result ? (
    <Button variant="primary" onClick={onClose}>
      Done
    </Button>
  ) : (
    <>
      {prevStep(state, step) && (
        <Button variant="ghost" icon={<ArrowLeft className="size-3.5" />} onClick={back} disabled={busy} className="mr-auto">
          Back
        </Button>
      )}
      <Button variant="ghost" onClick={onClose} disabled={busy}>
        Cancel
      </Button>
      <Button
        variant="primary"
        loading={busy}
        disabled={blocker !== null || (step === 'preview' && preview !== null && !preview.eligible)}
        title={blocker ?? undefined}
        onClick={() => void next()}
      >
        {last && state.path ? submitLabel(state.path) : step === 'name' ? 'Preview the registration' : 'Next'}
      </Button>
    </>
  )

  return (
    <Dialog open onOpenChange={(o) => !o && !busy && onClose()} size="lg" title="Get a domain" description={result ? 'Done' : `${STEP_TITLE[step]}${state.path ? ` · ${PATH_INFO.find((p) => p.path === state.path)!.title}` : ''}`} footer={footer}>
      {result ? (
        <ResultView companyId={companyId} registration={result} provider={provider} onOpenApprovals={() => { onClose(); openPanel({ kind: 'approvals' }) }} />
      ) : (
        <div className="space-y-4">
          {step === 'path' && (
            <div role="radiogroup" aria-label="Domain path" className="space-y-2">
              {PATH_INFO.map((p, i) => (
                <Choice key={p.path} label={`${i + 1}. ${p.title}`} selected={state.path === p.path} onSelect={() => { setState(choosePath(p.path)); setPreview(null); setError(null) }}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="text-[13px] font-medium text-fg">
                      {i + 1}. {p.title}
                    </div>
                    <Badge tone={p.path === 'paid_registrar' ? 'warning' : 'success'}>{p.cost}</Badge>
                  </div>
                  <p className="mt-1 text-[12.5px] text-fg-2">{p.summary}</p>
                  <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-[12px] text-fg-3">
                    {p.caveats.map((c, j) => (
                      <li key={j}>{c}</li>
                    ))}
                  </ul>
                </Choice>
              ))}
            </div>
          )}

          {step === 'registry' && (
            <Boundary query={registriesQ} milestone="M8" compact={false} unavailableTitle="Registries are not built yet" isEmpty={(d) => d.length === 0} empty={{ title: 'No registry available', description: 'The server did not list any free-subdomain registry.' }}>
              {(items) => (
                <div role="radiogroup" aria-label="Registry" className="space-y-2">
                  {items.map((r) => (
                    <RegistryChoice key={r.id} registry={r} selected={state.registryId === r.id} onSelect={() => patch({ registryId: r.id })} />
                  ))}
                </div>
              )}
            </Boundary>
          )}

          {step === 'name' && registry && (
            <div className="space-y-3">
              <Field label="Desired name" required error={state.name ? validateLabel(state.name, registry) : null} hint={`${registry.name_min_length}–${registry.name_max_length} characters: lowercase letters, digits and dashes.`}>
                {({ id, invalid, describedBy }) => (
                  <div className="flex items-center gap-2">
                    <Input id={id} autoFocus spellCheck={false} autoCapitalize="none" invalid={invalid} aria-describedby={describedBy} placeholder="my-company" value={state.name} onChange={(e) => patch({ name: e.target.value.toLowerCase() })} />
                    <span className="shrink-0 font-mono text-[12.5px] text-fg-2">.{registry.suffix}</span>
                  </div>
                )}
              </Field>
              <p className="text-[12.5px] text-fg-2">
                Result: <span className="font-mono text-fg">{fqdnFor(registry, state.name) || `…${'.'}${registry.suffix}`}</span>
              </p>
              <div className="rounded-md border border-line bg-raised p-3">
                <SectionLabel>Eligibility, as {registry.name} states it</SectionLabel>
                <Eligibility items={registry.eligibility} />
                {safeHttps(registry.rules_url) && (
                  <a href={safeHttps(registry.rules_url)!} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex items-center gap-1 text-[12.5px] text-accent-fg hover:underline">
                    Read the registry’s rules <ExternalLink className="size-3" aria-hidden />
                  </a>
                )}
                <p className="mt-2 text-[12px] text-fg-3">{registry.review === 'human' ? 'Requests are reviewed by hand, which can take days.' : 'Requests are processed automatically.'}</p>
              </div>
            </div>
          )}

          {step === 'preview' && preview && <PreviewView preview={preview} />}

          {step === 'provider' && (
            <Boundary query={providersQ} milestone="M8" compact={false} unavailableTitle="Free domain providers are not built yet" isEmpty={(d) => d.length === 0} empty={{ title: 'No provider available', description: 'The server did not list any free domain provider.' }}>
              {(items) => (
                <div role="radiogroup" aria-label="Provider" className="space-y-2">
                  {items.map((p) => (
                    <Choice key={p.id} label={p.name} selected={state.providerId === p.id} onSelect={() => patch({ providerId: p.id })}>
                      <div className="text-[13px] font-medium text-fg">{p.name}</div>
                      {p.suffixes.length > 0 && <div className="text-[12px] text-fg-3">Domains ending in {p.suffixes.map((s) => `.${s.replace(/^\./, '')}`).join(', ')}</div>}
                      <Eligibility items={p.eligibility} />
                    </Choice>
                  ))}
                </div>
              )}
            </Boundary>
          )}

          {step === 'domain' && provider && (
            <div className="space-y-3">
              <Field label="Domain you want" required error={state.domain ? validateDomain(state.domain) : null} hint="The exact name you will register on the provider’s site.">
                {({ id, invalid, describedBy }) => <Input id={id} autoFocus spellCheck={false} autoCapitalize="none" invalid={invalid} aria-describedby={describedBy} placeholder={`my-company${provider.suffixes[0] ? `.${provider.suffixes[0].replace(/^\./, '')}` : '.example'}`} value={state.domain} onChange={(e) => patch({ domain: e.target.value.toLowerCase() })} />}
              </Field>
              <p className="text-[12.5px] text-fg-2">
                On the next screen you get the exact steps and DNS records. <strong className="font-medium text-fg">You</strong> create the account and register the domain yourself; the AI Co-founder verifies DNS afterwards.
              </p>
            </div>
          )}

          {step === 'search' && (
            <SearchStep
              term={term}
              onTerm={setTerm}
              onSearch={() => { setSubmittedTerm(term.trim()); patch({ selected: null }) }}
              submitted={submittedTerm}
              query={searchQ}
              selected={state.selected}
              onSelect={(selected) => patch({ selected })}
            />
          )}

          {step === 'confirm' && state.selected && (
            <div className="space-y-3">
              <div className="rounded-md border border-line bg-raised p-3 text-[13px]">
                <div className="font-mono text-[14px] text-fg">{state.selected.domain}</div>
                <div className="mt-1 text-fg-2">
                  {state.selected.price_micros != null ? `${formatUsd(state.selected.price_micros)} for the first year` : 'No price quoted'} · {state.selected.registrar}
                </div>
                {state.selected.renewal_price_micros != null && <div className="text-[12px] text-fg-3">Renews at {formatUsd(state.selected.renewal_price_micros)} per year.</div>}
              </div>
              <p className="flex items-start gap-2 text-[12.5px] text-fg-2">
                <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-accent-fg" aria-hidden />
                <span>
                  This does not buy anything. It creates an approval in <strong className="font-medium text-fg">Approvals</strong> for exactly this domain at no more than {state.selected.price_micros != null ? formatUsd(state.selected.price_micros) : 'the quoted price'}. You decide there.
                </span>
              </p>
            </div>
          )}

          <FormError message={error} />
        </div>
      )}
    </Dialog>
  )
}

function RegistryChoice({ registry, selected, onSelect }: { registry: DomainRegistry; selected: boolean; onSelect: () => void }) {
  return (
    <Choice label={registry.name} selected={selected} onSelect={onSelect}>
      <div className="flex items-start justify-between gap-2">
        <div className="text-[13px] font-medium text-fg">{registry.name}</div>
        <Badge tone="neutral">{registry.review === 'human' ? 'Human review' : 'Automatic'}</Badge>
      </div>
      <div className="font-mono text-[12px] text-fg-3">
        *.{registry.suffix} · {registry.repo}
      </div>
      <Eligibility items={registry.eligibility} />
    </Choice>
  )
}

function PreviewView({ preview }: { preview: DomainRegistrationPreview }) {
  return (
    <div className="space-y-3">
      {!preview.eligible && (
        <p role="alert" className="rounded-md border border-danger/40 bg-danger-subtle px-3 py-2 text-[13px] text-danger">
          Not eligible: {preview.ineligible_reason ?? 'the registry’s rules do not allow this request.'}
        </p>
      )}
      <div className="rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-[12.5px] text-fg">
        If you approve, the AI Co-founder opens a pull request <strong className="font-medium">publishing this under your GitHub identity{preview.publishes_as ? ` (@${preview.publishes_as})` : ''}</strong>, which also accepts the registry’s terms. Nothing is submitted before your approval, and the approval covers exactly the content below.
      </div>
      <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1 text-[12.5px]">
        <dt className="text-fg-3">Domain</dt>
        <dd className="font-mono text-fg">{preview.fqdn}</dd>
        <dt className="text-fg-3">Repository</dt>
        <dd className="font-mono break-all text-fg">{preview.repo}</dd>
        {preview.branch && (
          <>
            <dt className="text-fg-3">Branch</dt>
            <dd className="font-mono text-fg">{preview.branch}</dd>
          </>
        )}
        <dt className="text-fg-3">PR title</dt>
        <dd className="text-fg">{preview.pr_title}</dd>
      </dl>
      <div>
        <SectionLabel>
          File <span className="font-mono normal-case">{preview.file_path}</span>
        </SectionLabel>
        <pre aria-label="Registration file" className="mt-1 max-h-60 overflow-auto rounded-sm border border-line bg-canvas p-2 text-[12px] leading-snug whitespace-pre-wrap break-words text-fg">{preview.file_content}</pre>
      </div>
      <div>
        <SectionLabel>Pull request text</SectionLabel>
        <pre aria-label="Pull request text" className="mt-1 max-h-40 overflow-auto rounded-sm border border-line bg-canvas p-2 text-[12px] leading-snug whitespace-pre-wrap break-words text-fg">{preview.pr_body}</pre>
      </div>
      {preview.warnings.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-[12.5px] text-warning">
          {preview.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

function SearchStep({
  term,
  onTerm,
  onSearch,
  submitted,
  query,
  selected,
  onSelect,
}: {
  term: string
  onTerm: (v: string) => void
  onSearch: () => void
  submitted: string
  query: ReturnType<typeof useDomainSearch>
  selected: DomainAvailability | null
  onSelect: (d: DomainAvailability) => void
}) {
  return (
    <div className="space-y-3">
      <form
        className="flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (term.trim()) onSearch()
        }}
      >
        <Field label="Search a name" className="min-w-0 flex-1" hint="A name or a full domain, e.g. mycompany or mycompany.com.">
          {({ id, describedBy }) => <Input id={id} autoFocus spellCheck={false} autoCapitalize="none" aria-describedby={describedBy} value={term} onChange={(e) => onTerm(e.target.value.toLowerCase())} />}
        </Field>
        <Button type="submit" icon={<Search className="size-3.5" />} disabled={!term.trim()}>
          Search
        </Button>
      </form>
      {submitted && (
        <Boundary query={query} milestone="M8" compact={false} unavailableTitle="Domain search is not built yet" isEmpty={(d) => d.length === 0} empty={{ title: 'No results', description: 'The connected registrar returned nothing for that name. Is a registrar account connected?' }}>
          {(items) => (
            <div role="radiogroup" aria-label="Search results" className="space-y-1.5">
              {items.map((d) => (
                <Choice key={`${d.registrar}-${d.domain}`} label={`${d.domain}${d.available ? '' : ' (taken)'}`} selected={selected?.domain === d.domain && selected.registrar === d.registrar} disabled={!d.available} onSelect={() => onSelect(d)}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-[13px] text-fg">{d.domain}</span>
                    {d.available ? (
                      <span className="flex items-center gap-2 text-[12.5px] text-fg-2 tabular-nums">
                        {d.price_micros != null ? `${formatUsd(d.price_micros)} first year` : 'No price quoted'}
                        {d.cost_kind && <CostKindBadge kind={d.cost_kind} />}
                      </span>
                    ) : (
                      <Badge tone="neutral">Taken</Badge>
                    )}
                  </div>
                  <div className="text-[12px] text-fg-3">
                    {d.registrar}
                    {d.renewal_price_micros != null ? ` · renews at ${formatUsd(d.renewal_price_micros)} / year` : ''}
                  </div>
                </Choice>
              ))}
              <p className="text-[12px] text-fg-3">Prices are quotes from the registrar. The final amount is confirmed by the registrar at purchase and must not exceed what you approve.</p>
            </div>
          )}
        </Boundary>
      )}
    </div>
  )
}

function ResultView({ companyId, registration, provider, onOpenApprovals }: { companyId: string; registration: DomainRegistration; provider: FreeDomainProvider | null; onOpenApprovals: () => void }) {
  if (registration.path === 'free_provider') return <AssistedView companyId={companyId} registration={registration} provider={provider} />
  const paid = registration.path === 'paid_registrar'
  return (
    <div className="space-y-3" role="status">
      <h3 className="text-[14px] font-semibold text-fg">Waiting for your approval</h3>
      <p className="text-[13px] text-fg-2">
        {paid
          ? `The purchase of ${registration.domain}${registration.price_micros != null ? ` at no more than ${formatUsd(registration.price_micros)}` : ''} is an approval in Approvals. Nothing is bought until you approve it.`
          : `The pull request for ${registration.domain} is an approval in Approvals. You can read the exact content there. It is only opened after you approve it.`}
      </p>
      <Button variant="primary" onClick={onOpenApprovals}>
        Open Approvals
      </Button>
    </div>
  )
}

/** The assisted path after it was created: exact steps, the DNS records to add, and "I've done it, verify". */
function AssistedView({ companyId, registration, provider }: { companyId: string; registration: DomainRegistration; provider: FreeDomainProvider | null }) {
  const plan = useDomainPlan(companyId)
  const verify = useVerifyDomain(companyId)
  const toast = useToast()
  const dash = safeHttps(provider?.dashboard_url)
  const p = plan.data
  const steps = [...(provider?.steps ?? []), ...(p?.steps ?? [])]
  return (
    <div className="space-y-4">
      <h3 className="text-[14px] font-semibold text-fg">Register {registration.domain} yourself</h3>
      <p className="text-[13px] text-fg-2">You create the account and accept the provider’s terms. Foundry AI OS does that for nobody.</p>
      {dash && (
        <a href={dash} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 text-[13px] text-accent-fg hover:underline">
          Open {provider?.name ?? 'the provider'}’s dashboard <ExternalLink className="size-3" aria-hidden />
        </a>
      )}
      {steps.length > 0 && (
        <ol aria-label="Steps" className="list-decimal space-y-1.5 pl-5 text-[13px] text-fg-2">
          {steps.map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ol>
      )}
      {p && p.dns_records.length > 0 && (
        <div>
          <SectionLabel>DNS records to add</SectionLabel>
          <div className="mt-1 rounded-md border border-line">
            <DnsRecordsTable records={p.dns_records} />
          </div>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" loading={verify.isPending} onClick={() => verify.mutate(undefined, { onSuccess: (r) => toast.success(r.dns_status === 'ok' ? 'DNS is correct' : 'Checked. DNS is not ready yet. Changes can take a while to spread.'), onError: (e) => toast.error(errorMessage(e)) })}>
          I’ve done it, verify
        </Button>
        {verify.data && <Badge tone={verify.data.dns_status === 'ok' ? 'success' : 'warning'}>{verify.data.dns_status === 'ok' ? 'DNS verified' : 'Not verified yet'}</Badge>}
      </div>
    </div>
  )
}
