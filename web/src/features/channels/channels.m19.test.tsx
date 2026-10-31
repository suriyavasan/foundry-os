import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ChatChannel, ChatKindInfo, ChatLink } from '@/api/types'
import { ChannelsStep } from '@/features/onboarding/steps/ChannelsStep'
import { jsonResponse, mockApi } from '@/test/factories'
import { renderWithProviders } from '@/test/render'
import { ChatAppsSection } from './ChatAppsSection'

afterEach(() => vi.unstubAllGlobals())

const TOKEN = '123456:ABC-very-secret-bot-token'
const err = (status: number, code: string, message: string, details: unknown = null) => jsonResponse({ error: { code, message, details } }, status)

const kinds = (over: Record<string, Partial<ChatKindInfo>> = {}): ChatKindInfo[] => [
  { kind: 'telegram', label: 'Telegram', available: true, reason: null, needs_public_url: false, ...over.telegram },
  { kind: 'discord', label: 'Discord', available: true, reason: null, needs_public_url: false, ...over.discord },
  { kind: 'whatsapp', label: 'WhatsApp', available: true, reason: null, needs_public_url: true, ...over.whatsapp },
  { kind: 'slack', label: 'Slack', available: false, reason: 'Coming later', needs_public_url: false },
]
const link = (over: Partial<ChatLink> = {}): ChatLink => ({
  id: 'l1', channel_id: 'ch1', user_id: 'u1', user_name: 'Sam', display_name: 'sam_tg', notify: { replies: true, questions: true, approvals: true, alerts: true, digest: false },
  linked_at: '2026-10-30T10:00:00Z', last_inbound_at: null, window_open: null, ...over,
})
const channel = (over: Partial<ChatChannel> = {}): ChatChannel => ({
  id: 'ch1', kind: 'telegram', name: '@acme_bot', status: 'active', config: { bot_username: 'acme_bot' }, last_error: null, last_event_at: null,
  created_at: '2026-10-30T09:00:00Z', links: [], webhook_url: null, verify_token: null, invite_url: null, ...over,
})

interface State {
  channels: ChatChannel[]
  available?: ChatKindInfo[]
  listReply?: () => Response
  connect?: (body: Record<string, unknown>) => Response
  /** Called on every link-code request; return the code response. */
  linkCode?: () => Response
}

function backend(s: State) {
  const api = mockApi({
    '/me': { user: { id: 'u1', email: 's@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme', role: 'owner' }] },
    '/companies/c1/channels': () => (s.listReply ? s.listReply() : jsonResponse({ items: s.channels, available: s.available ?? kinds(), next_cursor: null })),
    'POST /companies/c1/channels': (_u: URL, init?: RequestInit) => (s.connect ?? (() => jsonResponse(channel(), 201)))(JSON.parse(String(init?.body))),
    'PATCH /companies/c1/channels/ch1': () => jsonResponse(channel()),
    'DELETE /companies/c1/channels/ch1': () => {
      s.channels = []
      return new Response(null, { status: 204 })
    },
    'POST /companies/c1/channels/ch1/link-code': () =>
      (s.linkCode ?? (() => jsonResponse({ code: 'K7M2Q9XA', expires_at: new Date(Date.now() + 600_000).toISOString(), deep_link: 'https://t.me/acme_bot?start=K7M2Q9XA', instructions: 'Open the chat and press Start.' })))(),
    'PATCH /companies/c1/channel-links/l1': (_u: URL, init?: RequestInit) => jsonResponse(link({ notify: { ...link().notify, ...JSON.parse(String(init?.body)).notify } })),
    'DELETE /companies/c1/channel-links/l1': () => new Response(null, { status: 204 }),
  })
  vi.stubGlobal('fetch', api.impl)
  return api
}

const renderSection = () => renderWithProviders(<ChatAppsSection companyId="c1" />)
const card = (name: string) => screen.findByRole('region', { name })

describe('Chat apps: states', () => {
  it('shows a card per kind and greys out the ones that come later, with the server reason', async () => {
    backend({ channels: [] })
    renderSection()
    const tg = await card('Telegram')
    expect(within(tg).getByText(/start a chat with @BotFather/)).toBeInTheDocument()
    expect(within(tg).getByText(/Send \/newbot/)).toBeInTheDocument()
    expect(within(await card('Discord')).getByText(/Reset Token/)).toBeInTheDocument()
    expect(within(await card('Discord')).getByText(/applications\.commands/)).toBeInTheDocument()
    expect(within(await card('WhatsApp')).getByText(/Phone number ID/, { selector: 'li' })).toBeInTheDocument()
    const slack = await card('Slack')
    expect(within(slack).getByText('Coming later', { selector: 'span' })).toBeInTheDocument()
    expect(within(slack).queryByRole('button')).not.toBeInTheDocument()
    expect(within(slack).queryByLabelText(/token/i)).not.toBeInTheDocument()
  })

  it('says "not built yet" on a server without chat apps', async () => {
    backend({ channels: [], listReply: () => err(404, 'not_found', 'Not found') })
    renderSection()
    expect(await screen.findByText(/Not built yet/)).toBeInTheDocument()
    expect(screen.getByText(/does not have chat apps yet/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Connect/ })).not.toBeInTheDocument()
  })

  it('tells a member that only owners and admins manage chat apps', async () => {
    backend({ channels: [], listReply: () => err(403, 'forbidden', 'Only owners and admins can see chat channels') })
    renderSection()
    expect(await screen.findByText('Only owners and admins manage chat apps')).toBeInTheDocument()
  })

  it('shows a loading state, then an error with a working retry', async () => {
    let fail = true
    backend({ channels: [], listReply: () => (fail ? err(500, 'internal_error', 'The channel service is down') : jsonResponse({ items: [], available: kinds(), next_cursor: null })) })
    renderSection()
    expect(screen.getByRole('status', { name: 'Loading chat apps' })).toBeInTheDocument()
    expect(await screen.findByText('The channel service is down')).toBeInTheDocument()
    fail = false
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await card('Telegram')).toBeInTheDocument()
  })

  it('has an empty state when the server offers nothing to connect', async () => {
    backend({ channels: [], available: [] })
    renderSection()
    expect(await screen.findByText('No chat apps on this server')).toBeInTheDocument()
  })
})

describe('Chat apps: connecting', () => {
  it('asks for the token before calling the server', async () => {
    const api = backend({ channels: [] })
    renderSection()
    await userEvent.click(within(await card('Telegram')).getByRole('button', { name: 'Connect Telegram' }))
    expect(await screen.findByText('Paste the token from BotFather')).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
  })

  it('shows the platform error for a bad token, then connects, and never renders the secret again', async () => {
    let first = true
    const s: State = {
      channels: [],
      connect: () => {
        if (first) {
          first = false
          return err(422, 'validation_error', 'Telegram rejected that token', { reason: 'invalid_credentials', kind: 'telegram' })
        }
        s.channels = [channel()]
        return jsonResponse(channel(), 201)
      },
    }
    const api = backend(s)
    const u = userEvent.setup()
    renderSection()
    const tg = await card('Telegram')
    const input = within(tg).getByLabelText(/Telegram bot token/)
    expect(input).toHaveAttribute('type', 'password')
    expect(input).toHaveAttribute('autocomplete', 'off')
    await u.type(input, TOKEN)
    await u.click(within(tg).getByRole('button', { name: 'Connect Telegram' }))
    expect(await screen.findByText('Telegram rejected that token')).toBeInTheDocument()
    expect(screen.queryByText(new RegExp(TOKEN))).not.toBeInTheDocument()

    await u.click(within(tg).getByRole('button', { name: 'Connect Telegram' }))
    expect(await screen.findByText('Connected')).toBeInTheDocument()
    expect(screen.getByText('@acme_bot')).toBeInTheDocument()
    const posts = api.calls.filter((c) => c.method === 'POST' && c.path === '/companies/c1/channels')
    expect(posts).toHaveLength(2)
    expect(posts[1]!.body).toEqual({ kind: 'telegram', token: TOKEN })
    // The form is gone and the secret is nowhere on the page.
    expect(screen.queryByLabelText(/Telegram bot token/)).not.toBeInTheDocument()
    expect(screen.queryByDisplayValue(TOKEN)).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain(TOKEN)
  })

  it('maps field errors from the server onto the field', async () => {
    backend({ channels: [], connect: () => err(422, 'validation_error', 'Invalid request', [{ loc: ['body', 'token'], msg: 'String should have at most 512 characters' }]) })
    const u = userEvent.setup()
    renderSection()
    const tg = await card('Telegram')
    await u.type(within(tg).getByLabelText(/Telegram bot token/), 'x')
    await u.click(within(tg).getByRole('button', { name: 'Connect Telegram' }))
    expect(await screen.findByText('String should have at most 512 characters')).toBeInTheDocument()
  })

  it('explains a WhatsApp that needs a public address and does not let you submit', async () => {
    backend({ channels: [], available: kinds({ whatsapp: { reason: 'Needs a public https address for Meta\'s webhook (Settings → Reachability)' } }) })
    renderSection()
    const wa = await card('WhatsApp')
    expect(within(wa).getByText(/Needs a public https address/)).toBeInTheDocument()
    expect(within(wa).getByRole('button', { name: 'Connect WhatsApp' })).toBeDisabled()
  })

  it('shows the WhatsApp webhook URL and verify token once, with a warning, and drops the token after you dismiss it', async () => {
    const s: State = {
      channels: [],
      connect: () => {
        s.channels = [channel({ id: 'wa1', kind: 'whatsapp', name: '+1 555 0100', webhook_url: 'https://acme.example/api/webhooks/whatsapp/wa1' })]
        return jsonResponse({ ...s.channels[0], verify_token: 'verify-once-xyz' }, 201)
      },
    }
    const api = backend(s)
    const u = userEvent.setup()
    renderSection()
    const wa = await card('WhatsApp')
    for (const label of ['Access token', 'App secret']) expect(within(wa).getByLabelText(new RegExp(label))).toHaveAttribute('type', 'password')
    await u.type(within(wa).getByLabelText(/Phone number ID/), '1099')
    await u.type(within(wa).getByLabelText(/Access token/), 'EAAG-secret-access')
    await u.type(within(wa).getByLabelText(/App secret/), 'app-secret-123')
    await u.click(within(wa).getByRole('button', { name: 'Connect WhatsApp' }))

    expect(await screen.findByText('verify-once-xyz')).toBeInTheDocument()
    expect(screen.getByText('https://acme.example/api/webhooks/whatsapp/wa1')).toBeInTheDocument()
    expect(screen.getByText(/shown only once/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Copy Verify token' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Copy Callback URL' })).toBeInTheDocument()
    expect(screen.getByText(/within 24 hours of your last message/)).toBeInTheDocument()
    const post = api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/channels')!
    expect(post.body).toEqual({ kind: 'whatsapp', whatsapp: { phone_number_id: '1099', access_token: 'EAAG-secret-access', app_secret: 'app-secret-123' } })
    expect(document.body.textContent).not.toContain('EAAG-secret-access')
    expect(document.body.textContent).not.toContain('app-secret-123')

    await u.click(screen.getByRole('button', { name: 'I have copied it' }))
    expect(screen.queryByText('verify-once-xyz')).not.toBeInTheDocument()
    expect(screen.getByText('https://acme.example/api/webhooks/whatsapp/wa1')).toBeInTheDocument() // the URL stays, the token does not
  })
})

describe('Chat apps: linking', () => {
  it('shows the code, the deep link and a countdown, then "Linked as" once the bot has seen the message', async () => {
    const s: State = { channels: [channel()] }
    const api = backend(s)
    const u = userEvent.setup()
    renderSection()
    const tg = await card('Telegram')
    expect(await within(tg).findByText(/Nobody is linked yet/)).toBeInTheDocument()
    await u.click(within(tg).getByRole('button', { name: 'Link my account' }))

    expect(await within(tg).findByText('K7M2Q9XA')).toBeInTheDocument()
    const open = within(tg).getByRole('link', { name: 'Open Telegram' })
    expect(open).toHaveAttribute('href', 'https://t.me/acme_bot?start=K7M2Q9XA')
    expect(open).toHaveAttribute('rel', expect.stringContaining('noopener'))
    expect(within(tg).getByText('Waiting for your message…')).toBeInTheDocument()
    expect(within(tg).getByText(/Expires in/)).toHaveTextContent(/Expires in (9:5\d|10:00)/)
    expect(api.calls.filter((c) => c.path.endsWith('/link-code'))).toHaveLength(1)

    // The person sends the code in Telegram; the next poll of the channel shows their link.
    s.channels = [channel({ links: [link({ id: 'l9', display_name: 'sam_on_telegram' })] })]
    expect(await within(tg).findByText(/Linked as/, {}, { timeout: 6000 })).toHaveTextContent('Linked as sam_on_telegram')
    expect(within(tg).queryByText('Waiting for your message…')).not.toBeInTheDocument()
    expect(within(tg).queryByText('K7M2Q9XA')).not.toBeInTheDocument()
  }, 15_000)

  it('does not mistake somebody else\'s link for the one being made', async () => {
    const s: State = { channels: [channel({ links: [link({ id: 'l1' })] })] }
    backend(s)
    const u = userEvent.setup()
    renderSection()
    const tg = await card('Telegram')
    await u.click(await within(tg).findByRole('button', { name: 'Link another account' }))
    await within(tg).findByText('Waiting for your message…')
    s.channels = [channel({ links: [link({ id: 'l1' }), link({ id: 'l2', user_id: 'u2', display_name: 'someone_else' })] })]
    await new Promise((r) => setTimeout(r, 3500))
    expect(within(tg).queryByText(/Linked as/)).not.toBeInTheDocument()
    expect(within(tg).getByText('Waiting for your message…')).toBeInTheDocument()
  }, 15_000)

  it('tells Discord users to DM "link CODE" instead of showing an open button, and offers the invite link', async () => {
    backend({
      channels: [channel({ id: 'ch1', kind: 'discord', name: 'Foundry#1234', invite_url: 'https://discord.com/oauth2/authorize?client_id=1&scope=bot%20applications.commands' })],
      linkCode: () => jsonResponse({ code: 'DISC2345', expires_at: new Date(Date.now() + 600_000).toISOString(), deep_link: null, instructions: 'DM the bot' }),
    })
    const u = userEvent.setup()
    renderSection()
    const dc = await card('Discord')
    expect(await within(dc).findByRole('link', { name: /Invite the bot to a server/ })).toHaveAttribute('href', expect.stringContaining('discord.com/oauth2/authorize'))
    await u.click(within(dc).getByRole('button', { name: 'Link my account' }))
    expect(await within(dc).findByText('link DISC2345')).toBeInTheDocument()
    expect(within(dc).queryByRole('link', { name: /Open Discord/ })).not.toBeInTheDocument()
  })

  it('only follows an https deep link', async () => {
    backend({
      channels: [channel()],
      linkCode: () => jsonResponse({ code: 'EVIL2345', expires_at: new Date(Date.now() + 600_000).toISOString(), deep_link: 'javascript:alert(1)', instructions: '' }),
    })
    const u = userEvent.setup()
    renderSection()
    const tg = await card('Telegram')
    await u.click(await within(tg).findByRole('button', { name: 'Link my account' }))
    expect(await within(tg).findByText('EVIL2345')).toBeInTheDocument()
    expect(within(tg).queryByRole('link', { name: 'Open Telegram' })).not.toBeInTheDocument()
  })

  it('offers a new code once the old one has expired', async () => {
    let n = 0
    backend({
      channels: [channel()],
      linkCode: () => jsonResponse({ code: n++ === 0 ? 'OLDCODE2' : 'NEWCODE3', expires_at: new Date(Date.now() + (n === 1 ? 1500 : 600_000)).toISOString(), deep_link: null, instructions: '' }),
    })
    const u = userEvent.setup()
    renderSection()
    const tg = await card('Telegram')
    await u.click(await within(tg).findByRole('button', { name: 'Link my account' }))
    expect(await within(tg).findByText('This code expired.', {}, { timeout: 4000 })).toBeInTheDocument()
    await u.click(within(tg).getByRole('button', { name: 'Get a new code' }))
    expect(await within(tg).findByText('NEWCODE3')).toBeInTheDocument()
  }, 15_000)

  it('shows the server\'s reason when a code cannot be issued', async () => {
    backend({ channels: [channel()], linkCode: () => err(409, 'conflict', 'This chat app is not running; fix or enable it first', { reason: 'channel_not_active' }) })
    const u = userEvent.setup()
    renderSection()
    await u.click(await within(await card('Telegram')).findByRole('button', { name: 'Link my account' }))
    expect(await screen.findByText('This chat app is not running; fix or enable it first')).toBeInTheDocument()
  })
})

describe('Chat apps: connected channel', () => {
  it('shows a failing bot\'s last error and lets you retry', async () => {
    const api = backend({ channels: [channel({ status: 'error', last_error: 'Telegram rejected the token (401). Reconnect with a new one.' })] })
    renderSection()
    expect(await screen.findByText('Needs attention')).toBeInTheDocument()
    expect(screen.getByText(/Telegram rejected the token \(401\)/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Retry connection' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PATCH' && c.path === '/companies/c1/channels/ch1')).toBe(true))
    expect(api.calls.find((c) => c.method === 'PATCH')!.body).toEqual({ enabled: true })
  })

  it('saves a notification toggle with PATCH and only for your own link', async () => {
    const api = backend({ channels: [channel({ links: [link(), link({ id: 'l2', user_id: 'u2', user_name: 'Robin', display_name: 'robin' })] })] })
    const u = userEvent.setup()
    renderSection()
    const mine = await screen.findByRole('listitem', { name: 'Linked account sam_tg' })
    const alerts = within(mine).getByRole('switch', { name: 'Alerts to sam_tg' })
    expect(alerts).toHaveAttribute('aria-checked', 'true')
    expect(within(mine).getByRole('switch', { name: 'Daily digest to sam_tg' })).toHaveAttribute('aria-checked', 'false')
    await u.click(alerts)
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PATCH' && c.path === '/companies/c1/channel-links/l1')).toBe(true))
    expect(api.calls.find((c) => c.path === '/companies/c1/channel-links/l1')!.body).toEqual({ notify: { alerts: false } })
    // Somebody else's link: no toggles (the server would refuse), but an admin can unlink it.
    const other = screen.getByRole('listitem', { name: 'Linked account robin' })
    expect(within(other).queryByRole('switch')).not.toBeInTheDocument()
    expect(within(other).getByRole('button', { name: 'Unlink robin' })).toBeInTheDocument()
  })

  it('unlinks an account', async () => {
    const api = backend({ channels: [channel({ links: [link()] })] })
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: 'Unlink sam_tg' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'DELETE' && c.path === '/companies/c1/channel-links/l1')).toBe(true))
  })

  it('removes a channel only after a confirmation', async () => {
    const api = backend({ channels: [channel()] })
    const u = userEvent.setup()
    renderSection()
    await u.click(await screen.findByRole('button', { name: 'Remove Telegram' }))
    const dialog = await screen.findByRole('dialog', { name: 'Remove Telegram?' })
    expect(api.calls.some((c) => c.method === 'DELETE')).toBe(false)
    await u.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.calls.some((c) => c.method === 'DELETE')).toBe(false)

    await u.click(screen.getByRole('button', { name: 'Remove Telegram' }))
    await u.click(within(await screen.findByRole('dialog', { name: 'Remove Telegram?' })).getByRole('button', { name: 'Remove Telegram' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'DELETE' && c.path === '/companies/c1/channels/ch1')).toBe(true))
    // The card goes back to the connect form.
    expect(await within(await card('Telegram')).findByRole('button', { name: 'Connect Telegram' })).toBeInTheDocument()
  })
})

describe('Onboarding step: Chat apps', () => {
  it('can be skipped without connecting anything', async () => {
    const api = backend({ channels: [] })
    const onDone = vi.fn()
    renderWithProviders(<ChannelsStep companyId="c1" onDone={onDone} />)
    expect(await screen.findByRole('heading', { name: 'Talk to your company from your phone' })).toBeInTheDocument()
    await card('Telegram')
    await userEvent.click(screen.getByRole('button', { name: 'Skip for now' }))
    expect(onDone).toHaveBeenCalledTimes(1)
    expect(api.calls.filter((c) => c.method !== 'GET')).toEqual([])
  })

  it('says Continue once something is connected', async () => {
    backend({ channels: [channel()] })
    const onDone = vi.fn()
    renderWithProviders(<ChannelsStep companyId="c1" onDone={onDone} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Continue' }))
    expect(onDone).toHaveBeenCalledTimes(1)
  })

  it('shows the not-built state and still lets the person move on', async () => {
    backend({ channels: [], listReply: () => err(404, 'not_found', 'Not found') })
    const onDone = vi.fn()
    renderWithProviders(<ChannelsStep companyId="c1" onDone={onDone} />)
    expect(await screen.findByText(/Not built yet/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Skip for now' }))
    expect(onDone).toHaveBeenCalled()
  })
})
