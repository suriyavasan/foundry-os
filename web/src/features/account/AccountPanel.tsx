import { Check, Copy, KeyRound, LogOut, ShieldCheck, Trash2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import {
  errorMessage,
  useApiTokens,
  useAuthSessions,
  useAuthStatus,
  useChangePassword,
  useCreateApiToken,
  useDisableTwoFactor,
  useEnrollTwoFactor,
  useLogout,
  useRevokeAllSessions,
  useRevokeApiToken,
  useRevokeSession,
  useVerifyTwoFactor,
} from '@/api'
import type { ApiTokenCreated, AuthUser, TokenScope, TwoFactorEnroll, TwoFactorVerifyResult } from '@/api/types'
import { QrCode } from '@/components/qr/QrCode'
import {
  Badge,
  Boundary,
  Button,
  Card,
  CardBody,
  CardHeader,
  ConfirmDialog,
  DefinitionList,
  Dialog,
  Field,
  FormError,
  Input,
  LoadingState,
  NotAvailable,
  Select,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  useToast,
} from '@/components/ui'
import { NewPasswordFields, PasswordInput } from '@/features/auth/PasswordFields'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { PasskeysCard } from './PasskeysCard'
import { TOKEN_SCOPES, activeTokens, describeUserAgent, sortSessions, validateTokenName } from '@/lib/account'
import { isTotpCode, normalizeTotp } from '@/lib/auth'
import { formatDateTime, formatRelative } from '@/lib/format'
import { validateNewPassword } from '@/lib/password'

export function AccountPanel() {
  const status = useAuthStatus()
  return (
    <PanelFrame title="Account" subtitle="Your profile, password, sessions, API tokens and two-step verification">
      {status.kind === 'loading' ? (
        <Card>
          <LoadingState rows={4} label="Loading account" />
        </Card>
      ) : status.kind !== 'authenticated' ? (
        <Card>
          <NotAvailable
            milestone="M7"
            title="Accounts are not built yet"
            description="This backend uses the single-owner login link printed in its terminal. Passwords, sessions, tokens and two-step verification arrive with accounts."
          />
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-4">
            <ProfileCard user={status.session.user!} passkeysCount={status.session.passkeys_count} />
            <PasswordCard username={status.session.user!.username} displayName={status.session.user!.display_name} />
            <TwoFactorCard enabled={status.session.user!.two_factor_enabled === true} />
            <PasskeysCard supportedOrigin={status.session.passkey_supported_origin} />
          </div>
          <div className="space-y-4">
            <SessionsCard />
            <TokensCard />
          </div>
        </div>
      )}
    </PanelFrame>
  )
}

/* --------------------------------- profile --------------------------------- */

function ProfileCard({ user, passkeysCount }: { user: AuthUser; passkeysCount?: number }) {
  const logout = useLogout()
  return (
    <Card>
      <CardHeader
        title="Profile"
        actions={
          <Button size="sm" icon={<LogOut className="size-3.5" />} loading={logout.isPending} onClick={() => logout.mutate()}>
            Log out
          </Button>
        }
      />
      <CardBody>
        <DefinitionList
          items={[
            { label: 'Name', value: user.display_name },
            { label: 'Username', value: <span className="font-mono text-[12.5px]">@{user.username}</span> },
            { label: 'Email', value: user.email ?? <span className="text-fg-3">Not set</span> },
            { label: 'Two-step', value: user.two_factor_enabled ? <Badge tone="success">On</Badge> : <Badge tone="neutral">Off</Badge> },
            ...(passkeysCount === undefined ? [] : [{ label: 'Passkeys', value: passkeysCount > 0 ? <Badge tone="success">{passkeysCount}</Badge> : <Badge tone="neutral">None</Badge> }]),
          ]}
        />
      </CardBody>
    </Card>
  )
}

/* -------------------------------- password --------------------------------- */

function PasswordCard({ username, displayName }: { username: string; displayName: string }) {
  const change = useChangePassword()
  const toast = useToast()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [errors, setErrors] = useState<{ current?: string; password?: string; confirm?: string }>({})
  const [formError, setFormError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const pw = validateNewPassword(next, confirm, { username, displayName })
    const errs = { current: current ? undefined : 'Enter your current password', ...pw }
    if (!errs.current && next === current) errs.password = 'Choose a password different from the current one'
    setErrors(errs)
    if (errs.current || errs.password || errs.confirm) return
    try {
      await change.mutateAsync({ current_password: current, new_password: next })
      setCurrent('')
      setNext('')
      setConfirm('')
      toast.success('Password changed')
    } catch (err) {
      setFormError(errorMessage(err, 'Could not change the password'))
    }
  }

  return (
    <Card>
      <CardHeader title="Change password" />
      <CardBody>
        <form onSubmit={submit} noValidate className="space-y-4" aria-label="Change password">
          <Field label="Current password" required error={errors.current}>
            {({ id, invalid, describedBy }) => <PasswordInput id={id} autoComplete="current-password" value={current} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setCurrent(e.target.value)} />}
          </Field>
          <NewPasswordFields label="New password" password={next} confirm={confirm} onPassword={setNext} onConfirm={setConfirm} context={{ username, displayName }} errors={{ password: errors.password, confirm: errors.confirm }} />
          <FormError message={formError} />
          <Button type="submit" variant="primary" loading={change.isPending}>
            Change password
          </Button>
        </form>
      </CardBody>
    </Card>
  )
}

/* --------------------------------- sessions -------------------------------- */

function SessionsCard() {
  const q = useAuthSessions()
  const revoke = useRevokeSession()
  const revokeAll = useRevokeAllSessions()
  const toast = useToast()
  const [confirmAll, setConfirmAll] = useState(false)

  return (
    <Card>
      <CardHeader
        title="Sessions"
        description="Where you are signed in. Revoke any you do not recognise."
        actions={
          <Button size="sm" variant="danger" disabled={!q.data?.length} onClick={() => setConfirmAll(true)}>
            Log out everywhere
          </Button>
        }
      />
      <Boundary query={q} milestone="M7" isEmpty={(d) => d.length === 0} empty={{ title: 'No sessions', description: 'Nothing to show.' }} unavailableText="Session management arrives with accounts.">
        {(items) => (
          <Table>
            <THead>
              <TR>
                <TH>Device</TH>
                <TH>Last active</TH>
                <TH>
                  <span className="sr-only">Actions</span>
                </TH>
              </TR>
            </THead>
            <TBody>
              {sortSessions(items).map((s) => (
                <TR key={s.id}>
                  <TD>
                    <div className="flex items-center gap-2">
                      <span className="truncate">{describeUserAgent(s.user_agent)}</span>
                      {s.current && <Badge tone="accent">This device</Badge>}
                    </div>
                    <div className="text-[11.5px] text-fg-3">{s.ip ?? 'Unknown address'} · expires {formatDateTime(s.expires_at)}</div>
                  </TD>
                  <TD className="text-fg-2">{formatRelative(s.last_seen_at ?? s.created_at)}</TD>
                  <TD className="text-right">
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={s.current ? 'Log out this device' : `Revoke session from ${describeUserAgent(s.user_agent)}`}
                      loading={revoke.isPending && revoke.variables === s.id}
                      onClick={() => revoke.mutate(s.id, { onError: (e) => toast.error(errorMessage(e, 'Could not revoke the session')) })}
                    >
                      {s.current ? 'Log out' : 'Revoke'}
                    </Button>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Boundary>
      <ConfirmDialog
        open={confirmAll}
        onOpenChange={setConfirmAll}
        title="Log out everywhere?"
        description="Every session, including this one, is ended. You will need to sign in again on each device."
        confirmLabel="Log out everywhere"
        loading={revokeAll.isPending}
        error={revokeAll.isError ? errorMessage(revokeAll.error) : null}
        onConfirm={() => revokeAll.mutate()}
      />
    </Card>
  )
}

/* ---------------------------------- tokens --------------------------------- */

function TokensCard() {
  const q = useApiTokens()
  const revoke = useRevokeApiToken()
  const toast = useToast()
  const [creating, setCreating] = useState(false)
  const [created, setCreated] = useState<ApiTokenCreated | null>(null)
  const [toRevoke, setToRevoke] = useState<{ id: string; name: string } | null>(null)

  return (
    <Card>
      <CardHeader
        title="Personal API tokens"
        description="For the command line and scripts. A token acts as you, with the scope you choose."
        actions={
          <Button size="sm" variant="primary" icon={<KeyRound className="size-3.5" />} onClick={() => setCreating(true)}>
            Create token
          </Button>
        }
      />
      {created && <TokenReveal token={created} onDismiss={() => setCreated(null)} />}
      <Boundary query={q} milestone="M7" isEmpty={(d) => activeTokens(d).length === 0} empty={{ title: 'No tokens', description: 'Create one when you need to use Foundry AI OS from a script.' }} unavailableText="Personal tokens arrive with accounts.">
        {(items) => (
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Scope</TH>
                <TH>Last used</TH>
                <TH>
                  <span className="sr-only">Actions</span>
                </TH>
              </TR>
            </THead>
            <TBody>
              {activeTokens(items).map((t) => (
                <TR key={t.id}>
                  <TD>
                    <div className="truncate font-medium">{t.name}</div>
                    <div className="text-[11.5px] text-fg-3">
                      {t.prefix ? <span className="font-mono">{t.prefix}…</span> : null} created {formatRelative(t.created_at)}
                    </div>
                  </TD>
                  <TD>
                    <Badge tone={t.scope === 'admin' ? 'warning' : 'neutral'}>{t.scope}</Badge>
                  </TD>
                  <TD className="text-fg-2">{t.last_used_at ? formatRelative(t.last_used_at) : 'Never'}</TD>
                  <TD className="text-right">
                    <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} aria-label={`Revoke token ${t.name}`} onClick={() => setToRevoke({ id: t.id, name: t.name })}>
                      Revoke
                    </Button>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Boundary>
      {creating && (
        <CreateTokenDialog
          onClose={() => setCreating(false)}
          onCreated={(t) => {
            setCreating(false)
            setCreated(t)
          }}
        />
      )}
      <ConfirmDialog
        open={!!toRevoke}
        onOpenChange={(o) => !o && setToRevoke(null)}
        title="Revoke this token?"
        description={
          <>
            Anything using <strong className="font-medium text-fg">{toRevoke?.name}</strong> stops working immediately. This cannot be undone.
          </>
        }
        confirmLabel="Revoke token"
        loading={revoke.isPending}
        error={revoke.isError ? errorMessage(revoke.error) : null}
        onConfirm={() =>
          toRevoke &&
          revoke.mutate(toRevoke.id, {
            onSuccess: () => {
              toast.success('Token revoked')
              setToRevoke(null)
            },
          })
        }
      />
    </Card>
  )
}

function CreateTokenDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (t: ApiTokenCreated) => void }) {
  const create = useCreateApiToken()
  const [name, setName] = useState('')
  const [scope, setScope] = useState<TokenScope>('read')
  const [error, setError] = useState<string | undefined>()
  const [formError, setFormError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    const err = validateTokenName(name)
    setError(err ?? undefined)
    if (err) return
    try {
      onCreated(await create.mutateAsync({ name: name.trim(), scope }))
    } catch (e2) {
      setFormError(errorMessage(e2, 'Could not create the token'))
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      size="sm"
      title="Create API token"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={create.isPending}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="create-token-form" loading={create.isPending}>
            Create token
          </Button>
        </>
      }
    >
      <form id="create-token-form" onSubmit={submit} noValidate className="space-y-4">
        <Field label="Name" required error={error} hint="For example “laptop CLI”.">
          {({ id, invalid, describedBy }) => <Input id={id} autoFocus value={name} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setName(e.target.value)} />}
        </Field>
        <Field label="Scope" hint={TOKEN_SCOPES.find((s) => s.value === scope)?.help}>
          {({ id, describedBy }) => (
            <Select id={id} aria-describedby={describedBy} value={scope} onChange={(e) => setScope(e.target.value as TokenScope)}>
              {TOKEN_SCOPES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <FormError message={formError} />
      </form>
    </Dialog>
  )
}

/** The secret, once. It lives only in this component's state and disappears when dismissed. */
function TokenReveal({ token, onDismiss }: { token: ApiTokenCreated; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false)
  const toast = useToast()
  async function copy() {
    try {
      await navigator.clipboard.writeText(token.token)
      setCopied(true)
    } catch {
      toast.error('Could not copy. Select the token and copy it by hand.')
    }
  }
  return (
    <div role="alert" className="border-b border-warning/40 bg-warning-subtle px-4 py-3" data-testid="token-reveal">
      <p className="text-[13px] font-semibold text-fg">Copy your new token now</p>
      <p className="mt-0.5 text-[12.5px] text-fg-2">
        <strong className="font-medium">{token.name}</strong> ({token.scope}). This is the only time it is shown. Foundry AI OS stores only a hash, so a lost token cannot be recovered. Create a new one instead.
      </p>
      <div className="mt-2 flex items-center gap-2">
        <input readOnly aria-label="New API token" value={token.token} onFocus={(e) => e.currentTarget.select()} className="h-8 min-w-0 flex-1 rounded-md border border-line-strong bg-canvas px-2.5 font-mono text-[12px] text-fg" />
        <Button size="sm" icon={copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />} onClick={() => void copy()}>
          {copied ? 'Copied' : 'Copy'}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDismiss}>
          I have saved it
        </Button>
      </div>
    </div>
  )
}

/* ----------------------------------- 2FA ----------------------------------- */

function TwoFactorCard({ enabled }: { enabled: boolean }) {
  const enroll = useEnrollTwoFactor()
  const [setup, setSetup] = useState<TwoFactorEnroll | null>(null)
  const [recovery, setRecovery] = useState<string[] | null>(null)
  const [disabling, setDisabling] = useState(false)

  return (
    <Card>
      <CardHeader
        title="Two-step verification"
        description="A code from an authenticator app, asked for after your password."
        actions={enabled ? <Badge tone="success">On</Badge> : <Badge tone="neutral">Off</Badge>}
      />
      <CardBody className="space-y-3">
        {recovery && <RecoveryCodes codes={recovery} onDismiss={() => setRecovery(null)} />}
        {setup ? (
          <EnrollFlow
            enroll={setup}
            onCancel={() => setSetup(null)}
            onEnabled={(r) => {
              setSetup(null)
              setRecovery(r.recovery_codes?.length ? r.recovery_codes : null)
            }}
          />
        ) : enabled ? (
          <>
            <p className="text-[13px] text-fg-2">Two-step verification is protecting this account. Recommended before you expose Foundry AI OS on the internet.</p>
            <Button variant="danger" icon={<ShieldCheck className="size-3.5" />} onClick={() => setDisabling(true)}>
              Turn off
            </Button>
          </>
        ) : (
          <>
            <p className="text-[13px] text-fg-2">Add an authenticator app (1Password, Authy, Google Authenticator and others) so a stolen password is not enough.</p>
            <FormError message={enroll.isError ? errorMessage(enroll.error, 'Could not start enrolment') : null} />
            <Button variant="primary" icon={<ShieldCheck className="size-3.5" />} loading={enroll.isPending} onClick={() => enroll.mutate(undefined, { onSuccess: setSetup })}>
              Set up two-step verification
            </Button>
          </>
        )}
      </CardBody>
      {disabling && <DisableDialog onClose={() => setDisabling(false)} />}
    </Card>
  )
}

function EnrollFlow({ enroll, onCancel, onEnabled }: { enroll: TwoFactorEnroll; onCancel: () => void; onEnabled: (r: TwoFactorVerifyResult) => void }) {
  const verify = useVerifyTwoFactor()
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | undefined>()

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!isTotpCode(code)) {
      setError('Enter the 6-digit code from your authenticator app')
      return
    }
    setError(undefined)
    try {
      const r = await verify.mutateAsync(normalizeTotp(code))
      if (r.enabled === false) setError('That code was not accepted. Check the time on your phone and try again.')
      else onEnabled(r)
    } catch (err) {
      setError(errorMessage(err, 'That code was not accepted. Try again.'))
    }
  }
  return (
    <div className="space-y-3">
      <ol className="list-decimal space-y-1 pl-5 text-[13px] text-fg-2">
        <li>Scan this code with your authenticator app.</li>
        <li>Enter the 6-digit code it shows to finish.</li>
      </ol>
      <div className="flex flex-wrap items-start gap-4">
        <QrCode value={enroll.otpauth_uri} label="QR code for your authenticator app" />
        <div className="min-w-0 flex-1 text-[12.5px] text-fg-2">
          <p>Cannot scan? Enter this key by hand:</p>
          <p className="mt-1 font-mono break-all text-[13px] text-fg" data-testid="totp-secret">
            {enroll.secret}
          </p>
        </div>
      </div>
      <form onSubmit={submit} noValidate className="flex flex-wrap items-end gap-2" aria-label="Verify authenticator code">
        <Field label="Code" required error={error} className="w-44">
          {({ id, invalid, describedBy }) => (
            <Input id={id} autoFocus inputMode="numeric" autoComplete="one-time-code" maxLength={7} placeholder="123456" value={code} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setCode(e.target.value)} />
          )}
        </Field>
        <Button type="submit" variant="primary" loading={verify.isPending}>
          Verify and turn on
        </Button>
        <Button variant="ghost" onClick={onCancel} disabled={verify.isPending}>
          Cancel
        </Button>
      </form>
    </div>
  )
}

function RecoveryCodes({ codes, onDismiss }: { codes: string[]; onDismiss: () => void }) {
  return (
    <div role="alert" className="rounded-md border border-warning/40 bg-warning-subtle p-3" data-testid="recovery-codes">
      <p className="text-[13px] font-semibold text-fg">Save your recovery codes</p>
      <p className="mt-0.5 text-[12.5px] text-fg-2">Each works once if you lose your authenticator. They are shown only now.</p>
      <ul className="mt-2 grid grid-cols-2 gap-1 font-mono text-[12.5px] text-fg">
        {codes.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>
      <Button size="sm" className="mt-2" onClick={onDismiss}>
        I have saved them
      </Button>
    </div>
  )
}

function DisableDialog({ onClose }: { onClose: () => void }) {
  const disable = useDisableTwoFactor()
  const toast = useToast()
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!password || !isTotpCode(code)) {
      setError('Enter your password and a current 6-digit code')
      return
    }
    try {
      await disable.mutateAsync({ code: normalizeTotp(code), password })
      toast.success('Two-step verification is off')
      onClose()
    } catch (err) {
      setError(errorMessage(err, 'Could not turn it off'))
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !disable.isPending && onClose()}
      size="sm"
      title="Turn off two-step verification?"
      description="Your account will be protected by the password alone."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={disable.isPending}>
            Cancel
          </Button>
          <Button variant="danger" type="submit" form="disable-2fa-form" loading={disable.isPending}>
            Turn off
          </Button>
        </>
      }
    >
      <form id="disable-2fa-form" onSubmit={submit} noValidate className="space-y-4">
        <Field label="Password" required>
          {({ id }) => <PasswordInput id={id} autoFocus autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />}
        </Field>
        <Field label="Authentication code" required>
          {({ id }) => <Input id={id} inputMode="numeric" autoComplete="one-time-code" maxLength={7} value={code} onChange={(e) => setCode(e.target.value)} />}
        </Field>
        <FormError message={error} />
      </form>
    </Dialog>
  )
}
