import { Fingerprint, Info, Pencil, Plus, Trash2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { errorMessage, useDeletePasskey, usePasskeys, useRegisterPasskey, useRenamePasskey } from '@/api'
import type { PasskeyInfo } from '@/api/types'
import { Badge, Boundary, Button, Card, CardBody, CardHeader, Dialog, Field, FormError, Input, useToast } from '@/components/ui'
import { PasswordInput } from '@/features/auth/PasswordFields'
import { isMissingEndpoint } from '@/lib/auth'
import { formatRelative } from '@/lib/format'
import { defaultPasskeyName, describePasskeyFailure, passkeyEnvironment, passkeyEnvironmentMessage, passkeyFailureText, PASSKEY_NAME_MAX, validatePasskeyName } from '@/lib/webauthn'

export const LAST_METHOD_MESSAGE = 'This is the only way to sign in to your account, so it cannot be removed. Add another passkey first.'

/**
 * Passkeys of the signed-in account: what they are, the list, add / rename / remove. Renders nothing when the backend has
 * no passkey endpoints (a 404), so an older server shows no passkey UI at all.
 */
export function PasskeysCard({ supportedOrigin, unsupportedReason }: { supportedOrigin: boolean | undefined; unsupportedReason?: 'ephemeral_origin' | null }) {
  const q = usePasskeys()
  const [adding, setAdding] = useState(false)
  const [renaming, setRenaming] = useState<PasskeyInfo | null>(null)
  const [removing, setRemoving] = useState<PasskeyInfo | null>(null)

  if (isMissingEndpoint(q.error)) return null

  // The list endpoint answered, so the backend has passkeys even if the session omitted the origin flag.
  const env = passkeyEnvironment({ passkey_supported_origin: supportedOrigin ?? true, passkey_unsupported_reason: unsupportedReason })
  const envMessage = passkeyEnvironmentMessage(env)

  return (
    <Card>
      <CardHeader
        title="Passkeys"
        description="Sign in with your fingerprint, face or device PIN instead of a password."
        actions={
          <Button size="sm" variant="primary" icon={<Plus className="size-3.5" />} disabled={env !== 'ready'} onClick={() => setAdding(true)}>
            Add a passkey
          </Button>
        }
      />
      <CardBody className="space-y-2 border-b border-line text-[12.5px] text-fg-2">
        <p>
          <strong className="font-medium text-fg">What is a passkey?</strong> A sign-in key kept on your device, in your password manager or in your phone’s keychain. It never leaves there and cannot be phished, so there is nothing to type or remember.
        </p>
        <p>Passkeys count as two-factor sign-in: signing in with one is already two steps (the device and your fingerprint, face or PIN), so you are not asked for an authenticator code.</p>
        {envMessage && (
          <p role="note" className="flex gap-2 rounded-md border border-line bg-raised p-2.5 text-fg-2">
            <Info className="mt-0.5 size-3.5 shrink-0 text-fg-3" aria-hidden />
            <span>{envMessage}</span>
          </p>
        )}
      </CardBody>
      <Boundary query={q} isEmpty={(d) => d.length === 0} empty={{ title: 'No passkeys yet', description: 'Add one to sign in without typing your password.' }}>
        {(items) => (
          <ul className="divide-y divide-line" aria-label="Your passkeys">
            {items.map((p) => (
              <li key={p.id} className="flex items-center gap-3 px-4 py-3">
                <Fingerprint className="size-4 shrink-0 text-fg-3" aria-hidden />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-[13px] font-medium text-fg">{p.name}</span>
                    {p.backed_up ? (
                      <Badge tone="info" title="Backed up by a password manager or cloud keychain, so it also works on your other devices">
                        Synced
                      </Badge>
                    ) : (
                      <Badge tone="neutral" title="Stays on one device">
                        This device only
                      </Badge>
                    )}
                  </div>
                  <div className="text-[11.5px] text-fg-3">
                    Added {formatRelative(p.created_at)} · {p.last_used_at ? `last used ${formatRelative(p.last_used_at)}` : 'never used'}
                  </div>
                </div>
                <Button size="sm" variant="ghost" icon={<Pencil className="size-3.5" />} aria-label={`Rename passkey ${p.name}`} onClick={() => setRenaming(p)}>
                  Rename
                </Button>
                <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} aria-label={`Remove passkey ${p.name}`} onClick={() => setRemoving(p)}>
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Boundary>
      {adding && <AddPasskeyDialog onClose={() => setAdding(false)} />}
      {renaming && <RenamePasskeyDialog passkey={renaming} onClose={() => setRenaming(null)} />}
      {removing && <RemovePasskeyDialog passkey={removing} onClose={() => setRemoving(null)} />}
    </Card>
  )
}

function AddPasskeyDialog({ onClose }: { onClose: () => void }) {
  const register = useRegisterPasskey()
  const toast = useToast()
  const [name, setName] = useState(() => defaultPasskeyName())
  const [fieldError, setFieldError] = useState<string | undefined>()
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    const invalid = validatePasskeyName(name)
    setFieldError(invalid ?? undefined)
    if (invalid) return
    setError(null)
    try {
      await register.mutateAsync({ name: name.trim() })
      toast.success('Passkey added')
      onClose()
    } catch (err) {
      const f = describePasskeyFailure(err, 'register')
      setError(passkeyFailureText(f, f.retryAfterSeconds ?? 0))
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !register.isPending && onClose()}
      size="sm"
      title="Add a passkey"
      description="Your browser or device will ask you to confirm with your fingerprint, face, PIN or security key."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={register.isPending}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="add-passkey-form" icon={<Fingerprint className="size-3.5" />} loading={register.isPending}>
            Create passkey
          </Button>
        </>
      }
    >
      <form id="add-passkey-form" onSubmit={submit} noValidate className="space-y-4">
        <Field label="Name" required error={fieldError} hint="So you can tell your passkeys apart, for example “Work laptop”.">
          {({ id, invalid, describedBy }) => <Input id={id} autoFocus maxLength={PASSKEY_NAME_MAX + 20} value={name} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setName(e.target.value)} />}
        </Field>
        <FormError message={error} />
      </form>
    </Dialog>
  )
}

function RenamePasskeyDialog({ passkey, onClose }: { passkey: PasskeyInfo; onClose: () => void }) {
  const rename = useRenamePasskey()
  const toast = useToast()
  const [name, setName] = useState(passkey.name)
  const [fieldError, setFieldError] = useState<string | undefined>()
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    const invalid = validatePasskeyName(name)
    setFieldError(invalid ?? undefined)
    if (invalid) return
    setError(null)
    try {
      await rename.mutateAsync({ id: passkey.id, name: name.trim() })
      toast.success('Passkey renamed')
      onClose()
    } catch (err) {
      setError(errorMessage(err, 'Could not rename the passkey'))
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !rename.isPending && onClose()}
      size="sm"
      title="Rename passkey"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={rename.isPending}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="rename-passkey-form" loading={rename.isPending}>
            Save name
          </Button>
        </>
      }
    >
      <form id="rename-passkey-form" onSubmit={submit} noValidate className="space-y-4">
        <Field label="Name" required error={fieldError}>
          {({ id, invalid, describedBy }) => <Input id={id} autoFocus maxLength={PASSKEY_NAME_MAX + 20} value={name} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setName(e.target.value)} />}
        </Field>
        <FormError message={error} />
      </form>
    </Dialog>
  )
}

/**
 * Removing a passkey may need the password again (`reauth_required`): the first try is sent without it, and a refusal
 * for that reason turns the dialog into a password prompt that retries the same removal. A 409 means it is the last way in.
 */
function RemovePasskeyDialog({ passkey, onClose }: { passkey: PasskeyInfo; onClose: () => void }) {
  const remove = useDeletePasskey()
  const toast = useToast()
  const [needPassword, setNeedPassword] = useState(false)
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (needPassword && !password) {
      setError('Enter your password to remove this passkey')
      return
    }
    setError(null)
    try {
      await remove.mutateAsync({ id: passkey.id, ...(needPassword ? { password } : null) })
      toast.success('Passkey removed')
      onClose()
    } catch (err) {
      const f = describePasskeyFailure(err, 'register')
      if (f.kind === 'reauth' && !needPassword) {
        setNeedPassword(true)
        return
      }
      setPassword('')
      if (f.kind === 'last_method') setError(LAST_METHOD_MESSAGE)
      else if (f.kind === 'reauth') setError('That password was not accepted. Try again.')
      else setError(passkeyFailureText(f, f.retryAfterSeconds ?? 0) ?? 'Could not remove the passkey')
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !remove.isPending && onClose()}
      size="sm"
      title="Remove this passkey?"
      description={needPassword ? 'Confirm it is you.' : undefined}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={remove.isPending}>
            Cancel
          </Button>
          <Button variant="danger" type="submit" form="remove-passkey-form" loading={remove.isPending}>
            Remove passkey
          </Button>
        </>
      }
    >
      <form id="remove-passkey-form" onSubmit={submit} noValidate className="space-y-4">
        <p className="text-[13px] text-fg-2">
          <strong className="font-medium text-fg">{passkey.name}</strong> can no longer be used to sign in. Delete it from your password manager or device as well if you no longer want it there.
        </p>
        {needPassword && (
          <Field label="Password" required>
            {({ id }) => <PasswordInput id={id} autoFocus autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />}
          </Field>
        )}
        <FormError message={error} />
      </form>
    </Dialog>
  )
}
