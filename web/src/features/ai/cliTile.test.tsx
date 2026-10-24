import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CliRuntimeStatus } from '@/api/types'
import { jsonResponse, mockApi } from '@/test/factories'
import { makeCli } from '@/test/m10'
import { renderWithProviders } from '@/test/render'
import { CliTile } from './CliTile'

afterEach(() => vi.unstubAllGlobals())

function renderTile(cli: CliRuntimeStatus, routes: Record<string, unknown> = {}, props: { canManage?: boolean; onSelectForWork?: () => void; selectedForWork?: boolean } = {}) {
  const api = mockApi({ '/companies/c1/model-providers': { items: [], next_cursor: null }, ...routes })
  vi.stubGlobal('fetch', api.impl)
  renderWithProviders(<CliTile cli={cli} companyId="c1" canManage={props.canManage ?? true} onSelectForWork={props.onSelectForWork} selectedForWork={props.selectedForWork} />)
  return api
}

const installable = makeCli({ id: 'codex', display_name: 'Codex CLI', installed: false, binary_path: null, version: null, install: { available: true, command: 'npm install -g @openai/codex', requires: ['npm'] }, sandbox: { available: true, kind: 'os', detail: 'Seatbelt' } })

describe('CLI tile: install', () => {
  it('shows what is not installed and an Install button for the owner', () => {
    renderTile(installable)
    expect(screen.getByText('Not installed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Install' })).toBeInTheDocument()
    expect(screen.queryByText(/How it signs in/)).not.toBeInTheDocument() // nothing to sign in to yet
  })

  it('asks first, showing the exact command, and runs nothing until confirmed', async () => {
    const api = renderTile(installable, { 'POST /cli-runtimes/codex/install': { id: 'codex', status: 'running' }, '/cli-runtimes/codex/install-log': { id: 'codex', status: 'running', lines: ['added 1 package'] } })
    await userEvent.click(screen.getByRole('button', { name: 'Install' }))
    const dialog = await screen.findByRole('dialog', { name: 'Install Codex CLI?' })
    expect(within(dialog).getByLabelText('Install command')).toHaveTextContent(/^npm install -g @openai\/codex$/)
    expect(within(dialog).getByText(/does not use sudo/)).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)

    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)

    await userEvent.click(screen.getByRole('button', { name: 'Install' }))
    await userEvent.click(within(await screen.findByRole('dialog', { name: 'Install Codex CLI?' })).getByRole('button', { name: 'Run this command' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'POST' && c.path === '/cli-runtimes/codex/install')?.body).toEqual({ confirm: true }))
  })

  it('streams the install log after the confirmation', async () => {
    renderTile(installable, { 'POST /cli-runtimes/codex/install': { id: 'codex', status: 'running' }, '/cli-runtimes/codex/install-log': { id: 'codex', status: 'running', lines: ['npm http fetch', 'added 1 package'] } })
    await userEvent.click(screen.getByRole('button', { name: 'Install' }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Run this command' }))
    const log = await screen.findByLabelText('Codex CLI install log')
    await waitFor(() => expect(log).toHaveTextContent(/added 1 package/))
    expect(screen.getByText('Installing…')).toBeInTheDocument()
  })

  it('shows a refused install as an error and no log', async () => {
    renderTile(installable, { 'POST /cli-runtimes/codex/install': () => jsonResponse({ error: { code: 'forbidden', message: 'Only the installation owner can install tools', details: null } }, 403) })
    await userEvent.click(screen.getByRole('button', { name: 'Install' }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Run this command' }))
    expect(await screen.findByText(/Only the installation owner can install tools/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Codex CLI install log')).not.toBeInTheDocument()
  })

  it('does not offer Install to a non-owner, and says why', () => {
    renderTile(installable, {}, { canManage: false })
    expect(screen.queryByRole('button', { name: 'Install' })).not.toBeInTheDocument()
    expect(screen.getByText(/Only the installation owner can install tools/)).toBeInTheDocument()
  })

  it('without an install command it says what is needed and offers no Install', () => {
    renderTile(makeCli({ installed: false, install: { available: false, command: null, requires: ['npm'] } }))
    expect(screen.queryByRole('button', { name: 'Install' })).not.toBeInTheDocument()
    expect(screen.getByText(/cannot install it here \(it needs npm first\)/)).toBeInTheDocument()
  })
})

describe('CLI tile: status, sandbox and warnings', () => {
  it('shows the version, the binary and the sandbox kind', () => {
    renderTile(makeCli())
    expect(screen.getByText('Installed 2.1.292')).toBeInTheDocument()
    expect(screen.getByText('/opt/homebrew/bin/claude')).toBeInTheDocument()
    expect(screen.getByText('OS sandbox')).toBeInTheDocument()
  })

  it('says plainly when there is no sandbox', () => {
    renderTile(makeCli({ sandbox: { available: false, kind: 'none', detail: 'Docker is not running' } }))
    expect(screen.getByText('No sandbox')).toBeInTheDocument()
    expect(screen.getByText(/would run without shell access/)).toBeInTheDocument()
  })

  it('passes the server’s warnings through', () => {
    renderTile(makeCli({ warnings: ['Claude Code 2.x needs a recent Node'] }))
    expect(screen.getByText('Claude Code 2.x needs a recent Node')).toBeInTheDocument()
  })
})

describe('CLI tile: how it signs in', () => {
  it('API key is the recommended, preselected mode; the own-login warning is shown only for own login', async () => {
    renderTile(makeCli())
    const group = screen.getByRole('radiogroup', { name: 'Claude Code sign-in' })
    expect(within(group).getByRole('radio', { name: /API key \(recommended\)/ })).toBeChecked()
    expect(screen.queryByRole('note')).not.toBeInTheDocument()

    await userEvent.click(within(group).getByRole('radio', { name: /My own login/ }))
    const warning = screen.getByRole('note')
    expect(warning).toHaveTextContent(/Weaker isolation/)
    expect(warning).toHaveTextContent(/terms before using a personal or subscription login for automated work/)
    expect(screen.getByText(/Run `claude` in a terminal and sign in/)).toBeInTheDocument()
  })

  it('stores a key once through the credential endpoint, never in the auth call, and empties the field', async () => {
    const api = renderTile(makeCli(), {
      'POST /companies/c1/credentials': () => jsonResponse({ id: 'cr7', name: 'claude_code-key', kind: 'api_key' }, 201),
      'PUT /cli-runtimes/claude_code/auth': makeCli({ auth: { mode: 'api_key', configured: true, credential_id: 'cr7', last_test: null } }),
    })
    const field = screen.getByLabelText('Claude Code API key')
    expect(field).toHaveAttribute('type', 'password')
    await userEvent.type(field, 'sk-ant-secret-123')
    await userEvent.click(screen.getByRole('button', { name: 'Save key' }))
    await waitFor(() => expect(api.calls.find((c) => c.path === '/cli-runtimes/claude_code/auth')?.body).toEqual({ mode: 'api_key', credential_id: 'cr7' }))
    expect(api.calls.find((c) => c.path === '/companies/c1/credentials')?.body).toEqual({ name: 'claude_code-key', kind: 'api_key', value: 'sk-ant-secret-123' })
    expect(JSON.stringify(api.calls.find((c) => c.path === '/cli-runtimes/claude_code/auth'))).not.toContain('sk-ant-secret-123')
    expect(field).toHaveValue('')
  })

  it('never echoes the key in an error message', async () => {
    renderTile(makeCli(), { 'POST /companies/c1/credentials': () => jsonResponse({ error: { code: 'bad_request', message: 'Rejected the value sk-ant-secret-123', details: null } }, 400) })
    await userEvent.type(screen.getByLabelText('Claude Code API key'), 'sk-ant-secret-123')
    await userEvent.click(screen.getByRole('button', { name: 'Save key' }))
    expect(await screen.findByText(/Rejected the value \[redacted\]/)).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('sk-ant-secret-123')
  })

  it('shows that a key is saved without showing it, and allows replacing it', async () => {
    renderTile(makeCli({ auth: { mode: 'api_key', configured: true, credential_id: 'cr1', last_test: null } }))
    expect(screen.getByText(/A key is saved for this tool\. It cannot be shown again/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Claude Code API key')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Replace' }))
    expect(screen.getByLabelText('Claude Code API key')).toBeInTheDocument()
  })

  it('choosing own login calls the auth endpoint without a credential', async () => {
    const api = renderTile(makeCli(), { 'PUT /cli-runtimes/claude_code/auth': makeCli() })
    await userEvent.click(screen.getByRole('radio', { name: /My own login/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Use my own login' }))
    await waitFor(() => expect(api.calls.find((c) => c.path === '/cli-runtimes/claude_code/auth')?.body).toEqual({ mode: 'own_login' }))
  })

  it('OpenCode runs on one of the company’s model providers instead of a key of its own', async () => {
    const opencode = makeCli({ id: 'opencode', display_name: 'OpenCode', auth_modes: ['api_key'] })
    const api = renderTile(opencode, {
      '/companies/c1/model-providers': { items: [{ id: 'p1', name: 'OpenRouter', kind: 'openrouter', enabled: true, default_model: 'm', base_url: null, credential_id: 'c', context_length: 1, is_local: false, input_price_per_mtok_micros: null, output_price_per_mtok_micros: null }], next_cursor: null },
      'PUT /cli-runtimes/opencode/auth': opencode,
    })
    await screen.findByRole('option', { name: 'OpenRouter' })
    await userEvent.selectOptions(screen.getByLabelText('Run OpenCode on'), 'OpenRouter')
    await userEvent.type(screen.getByLabelText('Model'), 'openrouter/auto')
    await userEvent.click(screen.getByRole('button', { name: 'Use this provider' }))
    await waitFor(() => expect(api.calls.find((c) => c.path === '/cli-runtimes/opencode/auth')?.body).toEqual({ mode: 'api_key', model_provider_id: 'p1', model: 'openrouter/auto' }))
    expect(screen.queryByLabelText('OpenCode API key')).not.toBeInTheDocument()
  })
})

describe('CLI tile: test and use for work', () => {
  const configured = makeCli({ auth: { mode: 'api_key', configured: true, credential_id: 'cr1', last_test: null } })

  it('Test needs a configured sign-in, runs the endpoint and shows the result', async () => {
    const api = renderTile(configured, { 'POST /cli-runtimes/claude_code/test': { ok: true, detail: 'Replied OK in 4.1 s', tested_at: '2026-10-24T10:00:00Z' } })
    await userEvent.click(screen.getByRole('button', { name: 'Test' }))
    expect(await screen.findByText(/Test passed: Replied OK in 4\.1 s/)).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST' && c.path === '/cli-runtimes/claude_code/test')).toBe(true)
  })

  it('a failed test says so with the reason', async () => {
    renderTile(configured, { 'POST /cli-runtimes/claude_code/test': { ok: false, detail: 'Not logged in', tested_at: null } })
    await userEvent.click(screen.getByRole('button', { name: 'Test' }))
    expect(await screen.findByText(/Test failed: Not logged in/)).toBeInTheDocument()
  })

  it('Test is disabled until a sign-in is configured', () => {
    renderTile(makeCli())
    expect(screen.getByRole('button', { name: 'Test' })).toBeDisabled()
    expect(screen.getByText(/Choose how it signs in, then test it/)).toBeInTheDocument()
  })

  it('"Use for the work" is disabled until the test has passed', () => {
    renderTile(configured, {}, { onSelectForWork: vi.fn() })
    expect(screen.getByRole('radio', { name: /Use Claude Code for the work/ })).toBeDisabled()
    expect(screen.getByText(/test it first/)).toBeInTheDocument()
  })

  it('"Use for the work" is possible after a passing test', async () => {
    const onSelect = vi.fn()
    renderTile(makeCli({ auth: { mode: 'api_key', configured: true, credential_id: 'cr1', last_test: { ok: true, detail: 'OK', tested_at: null } } }), {}, { onSelectForWork: onSelect })
    const radio = screen.getByRole('radio', { name: /Use Claude Code for the work/ })
    expect(radio).toBeEnabled()
    await userEvent.click(radio)
    expect(onSelect).toHaveBeenCalled()
  })

  it('read-only for a non-owner: status only', () => {
    renderTile(configured, {}, { canManage: false })
    expect(screen.queryByRole('button', { name: 'Test' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save key' })).not.toBeInTheDocument()
    expect(screen.getByText('Claude Code')).toBeInTheDocument()
  })
})
