import { useState, type FormEvent } from 'react'
import { ApiError, errorMessage, useConnectChannel } from '@/api'
import type { ChatChannel, ChatChannelCreate, ChatChannelKind } from '@/api/types'
import { Button, Field, FormError, Input } from '@/components/ui'
import { KIND_LABEL } from '@/lib/channels'

const FIELD_MAP: Record<string, string> = {
  token: 'token',
  'whatsapp.phone_number_id': 'phone',
  'whatsapp.access_token': 'access',
  'whatsapp.app_secret': 'secret',
}

/**
 * The credentials of one chat app. Secrets are password fields that the browser never autofills, are sent once, and are cleared
 * as soon as the server has stored them; nothing the server returns contains them. The server checks them against the platform
 * first, so a wrong token comes back as an error from the platform (no token echoed).
 */
export function ConnectForm({
  companyId,
  kind,
  blockedReason,
  onConnected,
}: {
  companyId: string
  kind: ChatChannelKind
  /** The server says this cannot be connected right now (WhatsApp without a public address). */
  blockedReason?: string | null
  onConnected: (channel: ChatChannel) => void
}) {
  const connect = useConnectChannel(companyId)
  const label = KIND_LABEL[kind] ?? kind
  const [token, setToken] = useState('')
  const [phone, setPhone] = useState('')
  const [access, setAccess] = useState('')
  const [secret, setSecret] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)

  const wa = kind === 'whatsapp'

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const local: Record<string, string> = {}
    if (wa) {
      if (!phone.trim()) local.phone = 'Enter the phone number ID'
      if (!access.trim()) local.access = 'Paste the access token'
      if (!secret.trim()) local.secret = 'Paste the app secret'
    } else if (!token.trim()) {
      local.token = kind === 'telegram' ? 'Paste the token from BotFather' : 'Paste the bot token'
    }
    setErrors(local)
    if (Object.keys(local).length) return

    const body: ChatChannelCreate = wa
      ? { kind, whatsapp: { phone_number_id: phone.trim(), access_token: access.trim(), app_secret: secret.trim() } }
      : { kind, token: token.trim() }
    try {
      const channel = await connect.mutateAsync(body)
      setToken('')
      setAccess('')
      setSecret('')
      onConnected(channel)
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const mapped: Record<string, string> = {}
        for (const [path, msg] of Object.entries(err.fieldErrors)) if (FIELD_MAP[path]) mapped[FIELD_MAP[path]] = msg
        setErrors(mapped)
        if (!Object.keys(mapped).length) setFormError(err.message)
      } else {
        setFormError(errorMessage(err))
      }
    }
  }

  const secretInput = (id: string, invalid: boolean, describedBy: string | undefined, value: string, set: (v: string) => void) => (
    <Input id={id} type="password" autoComplete="off" spellCheck={false} value={value} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set(e.target.value)} />
  )

  return (
    <form onSubmit={submit} noValidate className="space-y-3">
      {wa ? (
        <>
          <Field label="Phone number ID" required error={errors.phone}>
            {({ id, invalid, describedBy }) => <Input id={id} inputMode="numeric" autoComplete="off" value={phone} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setPhone(e.target.value)} />}
          </Field>
          <Field label="Access token" required error={errors.access} hint="The permanent System User token.">
            {({ id, invalid, describedBy }) => secretInput(id, invalid, describedBy, access, setAccess)}
          </Field>
          <Field label="App secret" required error={errors.secret} hint="Used to check that webhook calls really come from Meta.">
            {({ id, invalid, describedBy }) => secretInput(id, invalid, describedBy, secret, setSecret)}
          </Field>
        </>
      ) : (
        <Field label={`${label} bot token`} required error={errors.token} hint="Stored encrypted. It is never shown again.">
          {({ id, invalid, describedBy }) => secretInput(id, invalid, describedBy, token, setToken)}
        </Field>
      )}
      <FormError message={formError} />
      {blockedReason && <p className="text-[12.5px] text-warning">{blockedReason}</p>}
      <Button type="submit" variant="primary" loading={connect.isPending} disabled={!!blockedReason}>
        Connect {label}
      </Button>
    </form>
  )
}
