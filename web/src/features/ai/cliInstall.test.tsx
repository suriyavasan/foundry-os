import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi, page } from '@/test/factories'
import { makeStack } from '@/test/m17'
import { baseRoutes, renderCanvas } from '@/test/m5'

afterEach(() => vi.unstubAllGlobals())

const NPM = { method: 'npm', command: 'npm install -g @openai/codex', url: null, available: true, reason: null }
const SCRIPT = { method: 'script', command: 'Download https://chatgpt.com/codex/install.sh and run it with sh', url: 'https://chatgpt.com/codex/install.sh', available: true, reason: null }

function cli(id: string, name: string, over: Record<string, unknown> = {}, install: Record<string, unknown> = {}) {
  return {
    id,
    display_name: name,
    installed: false,
    binary_path: null,
    version: null,
    auth_modes: ['api_key'],
    auth: { mode: null, configured: false, credential_id: null, last_test: null },
    sandbox: { available: false, kind: 'none', detail: null },
    install: { available: true, methods: [SCRIPT], default_method: 'script', command: SCRIPT.command, requires: [], status: 'none', manual: null, ...install },
    login_hint: '',
    warnings: [],
    ...over,
  }
}

const routes = (items: unknown[], extra: Record<string, unknown> = {}, me?: unknown) => ({
  ...baseRoutes,
  ...(me ? { '/me': me } : {}),
  '/companies/c1/ai-setup': { ready: true, blocking_reason: null, stack_count: 0, clis: [] },
  '/companies/c1/ai-stack': makeStack([]),
  '/companies/c1/model-providers': page([]),
  '/companies/c1/model-providers/detect': { reachable: false, models: [] },
  '/cli-runtimes': { items },
  ...extra,
})

async function open(r: Record<string, unknown>) {
  const api = mockApi(r)
  vi.stubGlobal('fetch', api.impl)
  renderCanvas('/c/c1?panel=ai')
  const dialog = await screen.findByRole('dialog', { name: 'AI settings' })
  const section = await within(dialog).findByRole('region', { name: 'Coding tools on this computer' })
  return { api, dialog, section, user: userEvent.setup() }
}

describe('Coding tools on this computer', () => {
  it('lists every CLI with installed state and version', async () => {
    const { section } = await open(routes([cli('cursor', 'Cursor CLI'), cli('codex', 'Codex CLI', { installed: true, version: 'codex-cli 0.164.0' })]))
    const cursor = await within(section).findByRole('listitem', { name: 'Cursor CLI' })
    expect(within(cursor).getByText('Not installed')).toBeInTheDocument()
    expect(within(cursor).getByRole('button', { name: 'Install Cursor CLI' })).toBeInTheDocument()
    const codex = within(section).getByRole('listitem', { name: 'Codex CLI' })
    expect(within(codex).getByText('Installed')).toBeInTheDocument()
    expect(within(codex).getByText('codex-cli 0.164.0')).toBeInTheDocument()
    expect(within(codex).queryByRole('button', { name: /Install/ })).toBeNull()
  })

  it('shows the script confirm text with the host and sends method script', async () => {
    const { api, section, user } = await open(routes([cli('cursor', 'Cursor CLI', {}, { methods: [{ ...SCRIPT, command: 'Download https://cursor.com/install and run it with bash', url: 'https://cursor.com/install' }] })], { 'POST /cli-runtimes/cursor/install': { id: 'cursor', status: 'running', lines: [] } }))
    await user.click(await within(section).findByRole('button', { name: 'Install Cursor CLI' }))
    const confirm = await screen.findByRole('dialog', { name: /^Install / })
    expect(within(confirm).getByText(/downloads the vendor's official installer from cursor\.com and runs it on this computer\. Foundry records its SHA-256/)).toBeInTheDocument()
    expect(within(confirm).getByLabelText('Install command')).toHaveTextContent('Download https://cursor.com/install and run it with bash')
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false) // nothing before the confirm
    await user.click(within(confirm).getByRole('button', { name: 'Download and run installer' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'POST' && c.path === '/cli-runtimes/cursor/install')?.body).toEqual({ confirm: true, method: 'script' }))
  })

  it('offers a method choice when npm and the script can both run', async () => {
    const { api, section, user } = await open(
      routes([cli('codex', 'Codex CLI', {}, { methods: [NPM, SCRIPT], default_method: 'npm', command: NPM.command })], { 'POST /cli-runtimes/codex/install': { id: 'codex', status: 'running', lines: [] } }),
    )
    const npm = await within(section).findByRole('radio', { name: /npm/ })
    expect(npm).toBeChecked()
    await user.click(within(section).getByRole('radio', { name: /Vendor's installer/ }))
    await user.click(within(section).getByRole('button', { name: 'Install Codex CLI' }))
    const confirm = await screen.findByRole('dialog', { name: /^Install / })
    expect(within(confirm).getByText(/from chatgpt\.com/)).toBeInTheDocument()
    await user.click(within(confirm).getByRole('button', { name: 'Download and run installer' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'POST' && c.path === '/cli-runtimes/codex/install')?.body).toEqual({ confirm: true, method: 'script' }))
  })

  it('install -> live log -> status flips to installed', async () => {
    let installed = false
    let polls = 0
    const r = routes([], {
      '/cli-runtimes': () => jsonResponse({ items: [cli('antigravity', 'Antigravity CLI', installed ? { installed: true, version: '1.3.3' } : {})] }),
      'POST /cli-runtimes/antigravity/install': { id: 'antigravity', status: 'running', lines: [] },
      '/cli-runtimes/antigravity/install-log': () => {
        polls += 1
        if (polls >= 2) installed = true
        return jsonResponse({ id: 'antigravity', status: polls >= 2 ? 'succeeded' : 'running', lines: ['installing agy'], exit_code: polls >= 2 ? 0 : null, sha256: 'a'.repeat(64) })
      },
    })
    const { section, user } = await open(r)
    await user.click(await within(section).findByRole('button', { name: 'Install Antigravity CLI' }))
    await user.click(within(await screen.findByRole('dialog', { name: /^Install / })).getByRole('button', { name: 'Download and run installer' }))
    expect(await within(section).findByLabelText('Antigravity CLI install log')).toHaveTextContent('installing agy')
    expect(await within(section).findByText('Install finished', undefined, { timeout: 4000 })).toBeInTheDocument()
    expect(within(section).getByText(/Installer SHA-256: a{64}/)).toBeInTheDocument()
    expect(await within(section).findByText('Installed')).toBeInTheDocument()
  })

  it('a non-owner sees status only, no Install button', async () => {
    const me = { user: { id: 'u', email: 'a@x', display_name: 'Ada' }, memberships: [{ company_id: 'c1', company_name: 'Acme Labs', role: 'admin' }] }
    const { section } = await open(routes([cli('cursor', 'Cursor CLI')], {}, me))
    const row = await within(section).findByRole('listitem', { name: 'Cursor CLI' })
    expect(within(row).getByText('Not installed')).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: /Install/ })).toBeNull()
    expect(within(row).getByText('Only the installation owner can install tools.')).toBeInTheDocument()
  })

  it('when no method can run it shows the reason and the manual command', async () => {
    const methods = [{ ...SCRIPT, command: 'Download https://cursor.com/install and run it with bash', available: false, reason: 'bash was not found on this machine' }]
    const { section } = await open(routes([cli('cursor', 'Cursor CLI', {}, { available: false, methods, default_method: null, manual: 'curl https://cursor.com/install -fsS | bash' })]))
    const row = await within(section).findByRole('listitem', { name: 'Cursor CLI' })
    expect(within(row).queryByRole('button', { name: /Install/ })).toBeNull()
    expect(within(row).getByText(/bash was not found on this machine/)).toBeInTheDocument()
    expect(within(row).getByLabelText('Manual install command')).toHaveTextContent('curl https://cursor.com/install -fsS | bash')
  })

  it('handles an error and a server without the endpoint', async () => {
    const err = await open(routes([], { '/cli-runtimes': () => jsonResponse({ error: { code: 'internal', message: 'boom', details: null } }, 500) }))
    expect(await within(err.section).findByText('Could not load this data', undefined, { timeout: 5000 })).toBeInTheDocument()
  })

  it('says only the installation owner when the server answers 403', async () => {
    const { section } = await open(routes([], { '/cli-runtimes': () => jsonResponse({ error: { code: 'forbidden', message: 'no', details: null } }, 403) }))
    expect(await within(section).findByText(/Only the installation owner can see and install coding tools/)).toBeInTheDocument()
  })
})
