import { AlertTriangle, Download, FlaskConical, Lock, ShieldCheck, Terminal } from 'lucide-react'
import { useState } from 'react'
import { errorMessage, useCliInstallLog, useCreateCredential, useInstallCli, useModelProviders, useSetCliAuth, useTestCli } from '@/api'
import type { CliAuthMode, CliLastTest, CliRuntimeStatus } from '@/api/types'
import { Badge, Button, ConfirmDialog, Field, FormError, Input, Select, Tooltip } from '@/components/ui'
import { formatRelative } from '@/lib/format'
import { CLI_AUTH_BLURB, CLI_AUTH_LABEL, OWN_LOGIN_WARNING, cliReady, cliStatusLine, installCommandOf, sandboxSummary } from '@/lib/aiSetup'
import { redactSecret } from '@/lib/providers'

/**
 * One coding CLI (Claude Code, Codex, OpenCode): is it installed, which sandbox it would run in, how it signs in, a test, and
 * (when it passed) "use it for work". Everything that changes the machine is an explicit, owner-only action: installing shows
 * the exact command first and only runs after the person confirms; a key goes to the credential store and is never shown
 * again; an own login is the person's own step in a terminal, never automated.
 */
export function CliTile({
  cli,
  companyId,
  canManage,
  selectedForWork,
  onSelectForWork,
}: {
  cli: CliRuntimeStatus
  companyId: string
  canManage: boolean
  selectedForWork?: boolean
  onSelectForWork?: () => void
}) {
  const status = cliStatusLine(cli)
  const sandbox = sandboxSummary(cli.sandbox)
  const ready = cliReady(cli)
  const [mode, setMode] = useState<CliAuthMode>(cli.auth.mode ?? (cli.auth_modes.includes('api_key') ? 'api_key' : (cli.auth_modes[0] ?? 'api_key')))
  const [replacing, setReplacing] = useState(false)
  const [secret, setSecret] = useState('')
  // OpenCode runs against one of the company's model providers (its endpoint and key) instead of a key of its own.
  const [providerId, setProviderId] = useState(cli.auth.model_provider_id ?? '')
  const [providerModel, setProviderModel] = useState(cli.auth.model ?? '')
  const [confirmInstall, setConfirmInstall] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<CliLastTest | null>(null)
  const providers = useModelProviders(cli.id === 'opencode' ? companyId : undefined)
  const install = useInstallCli(companyId)
  const log = useCliInstallLog(installing ? cli.id : null, installing)
  const createCredential = useCreateCredential(companyId)
  const setAuth = useSetCliAuth(companyId)
  const test = useTestCli(companyId)
  const command = installCommandOf(cli)
  const lastTest = testResult ?? cli.auth.last_test
  const logDone = !!log.data && (log.data.state === 'succeeded' || log.data.state === 'failed')

  async function startInstall() {
    setError(null)
    try {
      await install.mutateAsync(cli.id)
      setConfirmInstall(false)
      setInstalling(true)
    } catch (e) {
      setConfirmInstall(false)
      setError(errorMessage(e))
    }
  }

  async function saveKey() {
    setError(null)
    const value = secret.trim()
    if (!value) {
      setError('Paste the API key first.')
      return
    }
    try {
      const cred = await createCredential.mutateAsync({ name: `${cli.id}-key`, kind: 'api_key', value })
      setSecret('') // the server has it; the browser does not need to keep it
      await setAuth.mutateAsync({ id: cli.id, body: { mode: 'api_key', credential_id: cred.id } })
      setReplacing(false)
    } catch (e) {
      setError(redactSecret(errorMessage(e), value))
    }
  }

  async function chooseProvider() {
    setError(null)
    if (!providerId || !providerModel.trim()) {
      setError('Choose a model provider and a model first.')
      return
    }
    try {
      await setAuth.mutateAsync({ id: cli.id, body: { mode: 'api_key', model_provider_id: providerId, model: providerModel.trim() } })
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  async function chooseOwnLogin() {
    setError(null)
    try {
      await setAuth.mutateAsync({ id: cli.id, body: { mode: 'own_login' } })
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  async function runTest() {
    setError(null)
    setTestResult(null)
    try {
      const r = await test.mutateAsync(cli.id)
      if (r) setTestResult(r)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const keyConfigured = cli.auth.configured && cli.auth.mode === 'api_key'
  const ownConfigured = cli.auth.configured && cli.auth.mode === 'own_login'
  const authBusy = createCredential.isPending || setAuth.isPending
  const noSandbox = !cli.sandbox.available || cli.sandbox.kind === 'none'

  return (
    <article aria-label={cli.display_name} className={'rounded-lg border bg-surface p-4 ' + (selectedForWork ? 'border-accent' : 'border-line-strong')}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-[14px] font-semibold text-fg">
            <Terminal className="size-4 text-fg-3" aria-hidden />
            {cli.display_name}
          </h3>
          {cli.installed && cli.binary_path && <p className="mt-0.5 truncate font-mono text-[11.5px] text-fg-3">{cli.binary_path}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={status.tone}>{status.label}</Badge>
          <Tooltip content={sandbox.help}>
            <Badge tone={sandbox.tone}>
              <ShieldCheck className="size-3" aria-hidden />
              {sandbox.label}
            </Badge>
          </Tooltip>
        </div>
      </div>

      {noSandbox && (
        <p className="mt-2 text-[12px] text-fg-3">
          No sandbox is available, so this tool would run without shell access.{cli.sandbox.detail ? ` ${cli.sandbox.detail}` : ''}
        </p>
      )}
      {cli.warnings.map((w) => (
        <p key={w} className="mt-2 flex items-start gap-1.5 text-[12px] text-warning">
          <AlertTriangle className="mt-0.5 size-3 shrink-0" aria-hidden />
          {w}
        </p>
      ))}

      {!cli.installed && (
        <div className="mt-3 space-y-2">
          {command ? (
            <>
              <p className="text-[12.5px] text-fg-2">Not installed. Foundry AI OS can install it for you after you confirm the command.</p>
              {canManage ? (
                <Button icon={<Download className="size-3.5" />} onClick={() => setConfirmInstall(true)} disabled={install.isPending || installing}>
                  Install
                </Button>
              ) : (
                <p className="text-[12px] text-fg-3">Only the installation owner can install tools.</p>
              )}
            </>
          ) : (
            <p className="text-[12.5px] text-fg-2">
              Not installed, and Foundry AI OS cannot install it here{cli.install.requires.length > 0 ? ` (it needs ${cli.install.requires.join(', ')} first)` : ''}. Install it yourself, then refresh this page.
            </p>
          )}
        </div>
      )}

      {installing && (
        <div className="mt-3">
          <p className="mb-1 text-[12px] font-medium text-fg-2">{log.data?.state === 'failed' ? 'Install failed' : logDone ? 'Install finished' : 'Installing…'}</p>
          <pre aria-label={`${cli.display_name} install log`} tabIndex={0} className="max-h-40 overflow-auto rounded-md border border-line bg-canvas p-2 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap text-fg-2">
            {log.data && log.data.lines.length > 0 ? log.data.lines.join('\n') : 'Waiting for output…'}
          </pre>
          {logDone && (
            <Button size="sm" className="mt-2" variant="ghost" onClick={() => setInstalling(false)}>
              Hide log
            </Button>
          )}
        </div>
      )}

      {cli.installed && (
        <div className="mt-4 space-y-3 border-t border-line pt-3">
          {cli.auth_modes.length > 0 && (
            <fieldset>
              <legend className="mb-1.5 text-[12px] font-medium text-fg-2">How it signs in</legend>
              <div role="radiogroup" aria-label={`${cli.display_name} sign-in`} className="space-y-1.5">
                {cli.auth_modes.map((m) => (
                  <label key={m} className={'flex cursor-pointer items-start gap-2 rounded-md border px-2.5 py-2 text-[12.5px] transition-colors ' + (mode === m ? 'border-accent bg-accent-subtle' : 'border-line hover:border-line-strong')}>
                    <input type="radio" name={`${cli.id}-auth`} className="mt-0.5 accent-(--accent)" checked={mode === m} disabled={!canManage} onChange={() => setMode(m)} />
                    <span>
                      <span className="font-medium text-fg">{CLI_AUTH_LABEL[m]}</span>
                      <span className="mt-0.5 block text-fg-3">{CLI_AUTH_BLURB[m]}</span>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>
          )}

          {mode === 'api_key' && cli.auth_modes.includes('api_key') && cli.id === 'opencode' && (
            <div className="space-y-2">
              <Field label="Run OpenCode on" hint="One of your model providers: OpenCode uses its address and key, held by Foundry AI OS.">
                {({ id, describedBy }) => (
                  <Select id={id} aria-describedby={describedBy} value={providerId} disabled={!canManage} onChange={(e) => setProviderId(e.target.value)}>
                    <option value="">Choose a provider…</option>
                    {(providers.data ?? []).filter((p) => p.enabled).map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label="Model">
                {({ id }) => <Input id={id} className="h-9 font-mono" value={providerModel} disabled={!canManage} spellCheck={false} onChange={(e) => setProviderModel(e.target.value)} />}
              </Field>
              {canManage && (
                <Button size="sm" variant="primary" loading={authBusy} onClick={() => void chooseProvider()}>
                  Use this provider
                </Button>
              )}
              {keyConfigured && <p className="text-[12.5px] text-fg-2">Configured.</p>}
            </div>
          )}

          {mode === 'api_key' && cli.auth_modes.includes('api_key') && cli.id !== 'opencode' && (
            <div className="space-y-2">
              {keyConfigured && !replacing ? (
                <p className="flex items-center gap-2 text-[12.5px] text-fg-2">
                  <Lock className="size-3 text-success" aria-hidden /> A key is saved for this tool. It cannot be shown again.
                  {canManage && (
                    <button type="button" className="text-accent-fg hover:underline" onClick={() => setReplacing(true)}>
                      Replace
                    </button>
                  )}
                </p>
              ) : (
                canManage && (
                  <>
                    <Field label={`${cli.display_name} API key`}>
                      {({ id, describedBy }) => (
                        <Input id={id} className="h-9" type="password" autoComplete="off" spellCheck={false} data-lpignore="true" aria-describedby={describedBy} value={secret} placeholder="Paste the key" onChange={(e) => setSecret(e.target.value)} />
                      )}
                    </Field>
                    <p className="flex items-center gap-1.5 text-[12px] text-fg-3">
                      <Lock className="size-3" aria-hidden /> Write-only. Handed only to this tool’s own runs, in a private home folder.
                    </p>
                    <Button size="sm" variant="primary" loading={authBusy} onClick={() => void saveKey()}>
                      Save key
                    </Button>
                  </>
                )
              )}
            </div>
          )}

          {mode === 'own_login' && cli.auth_modes.includes('own_login') && (
            <div className="space-y-2">
              <div role="note" className="flex items-start gap-2 rounded-md border border-warning/40 bg-warning-subtle px-2.5 py-2 text-[12px] text-fg">
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
                <p>{OWN_LOGIN_WARNING}</p>
              </div>
              {cli.login_hint && (
                <p className="text-[12.5px] text-fg-2">
                  Sign in yourself, in a terminal: <span className="font-mono text-fg">{cli.login_hint}</span>
                </p>
              )}
              {ownConfigured ? (
                <p className="text-[12.5px] text-fg-2">Own login is selected.</p>
              ) : (
                canManage && (
                  <Button size="sm" loading={authBusy} onClick={() => void chooseOwnLogin()}>
                    Use my own login
                  </Button>
                )
              )}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2">
            {canManage && (
              <Button size="sm" icon={<FlaskConical className="size-3.5" />} loading={test.isPending} disabled={!cli.auth.configured} onClick={() => void runTest()}>
                Test
              </Button>
            )}
            {lastTest && (
              <span role="status" className={'text-[12.5px] ' + (lastTest.ok ? 'text-success' : 'text-danger')}>
                {lastTest.ok ? 'Test passed' : 'Test failed'}
                {lastTest.detail ? `: ${lastTest.detail}` : ''}
                {lastTest.tested_at ? <span className="text-fg-3"> · {formatRelative(lastTest.tested_at)}</span> : null}
              </span>
            )}
            {!cli.auth.configured && canManage && <span className="text-[12px] text-fg-3">Choose how it signs in, then test it.</span>}
          </div>

          {onSelectForWork && (
            <label className={'flex items-center gap-2 text-[13px] ' + (ready ? 'cursor-pointer text-fg' : 'text-fg-3')}>
              <input type="radio" name="work-runtime" className="accent-(--accent)" checked={!!selectedForWork} disabled={!ready} onChange={onSelectForWork} />
              Use {cli.display_name} for hands-on work
              {!ready && <span className="text-[12px]"> (test it first)</span>}
            </label>
          )}
        </div>
      )}

      <FormError message={error} />

      <ConfirmDialog
        open={confirmInstall}
        onOpenChange={setConfirmInstall}
        title={`Install ${cli.display_name}?`}
        destructive={false}
        confirmLabel="Run this command"
        loading={install.isPending}
        description={
          <div className="space-y-2">
            <p>This will run exactly the command below on the computer that runs Foundry AI OS. It does not use sudo.</p>
            <pre aria-label="Install command" className="overflow-x-auto rounded-md border border-line bg-canvas px-3 py-2 font-mono text-[12.5px] text-fg">
              {command}
            </pre>
            {cli.install.requires.length > 0 && <p className="text-[12px] text-fg-3">Needs: {cli.install.requires.join(', ')}.</p>}
          </div>
        }
        onConfirm={() => void startInstall()}
      />
    </article>
  )
}
