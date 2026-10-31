import { describe, expect, it } from 'vitest'
import type { AiSetupStatus, CliRuntimeStatus, ProviderTest } from '@/api/types'
import {
  aiGateState,
  cliBrief,
  blockingReasonText,
  canTestProvider,
  cardFor,
  cliStatusLine,
  filterModels,
  installCommandOf,
  loaderSteps,
  normalizeAiSetup,
  normalizeCli,
  normalizeCliTest,
  normalizeInstallLog,
  normalizeModelList,
  normalizeProviderTest,
  providerFormFor,
  sandboxSummary,
  testPassed,
} from './aiSetup'

function cli(over: Partial<CliRuntimeStatus> = {}): CliRuntimeStatus {
  return {
    id: 'claude_code',
    display_name: 'Claude Code',
    installed: true,
    binary_path: '/opt/homebrew/bin/claude',
    version: '2.1.292',
    auth_modes: ['api_key', 'own_login'],
    auth: { mode: 'api_key', configured: true, credential_id: 'cr1', last_test: { ok: true, detail: 'OK', tested_at: '2026-10-24T10:00:00Z' } },
    sandbox: { available: true, kind: 'os', detail: 'Seatbelt' },
    install: { available: false, command: null, requires: ['npm'] },
    login_hint: 'Run `claude` in a terminal',
    warnings: [],
    ...over,
  }
}

const passing: ProviderTest = {
  ok: true,
  served_context: 65536,
  latency_ms: 420,
  tested_at: '2026-10-24T10:00:00Z',
  checks: ['reachable', 'auth', 'tool_calling', 'context', 'model_exists'].map((name) => ({ name, ok: true, detail: `${name} fine` })),
}

describe('AI gate rule', () => {
  const status = (ready: boolean): AiSetupStatus => ({ ready, blocking_reason: ready ? null : 'no_ai', stack_count: 0, clis: [] })
  it('blocks only on an explicit ready:false', () => {
    expect(aiGateState({ isPending: true })).toBe('loading')
    expect(aiGateState({ isPending: false, data: status(false) })).toBe('blocked')
    expect(aiGateState({ isPending: false, data: status(true) })).toBe('open')
  })
  it('does not block when the status could not be read (an older backend answers 404, or it errored)', () => {
    expect(aiGateState({ isPending: false, data: undefined })).toBe('open')
    expect(aiGateState({ isPending: false, data: null })).toBe('open')
  })
  it('says what blocks it, in plain words', () => {
    expect(blockingReasonText('no_ai')).toMatch(/No AI yet/)
    expect(blockingReasonText('no_tested_ai')).toMatch(/Test your AI/)
    expect(blockingReasonText('no_heavy_ai')).toMatch(/heavy lifting/)
    expect(blockingReasonText('no_everyday_ai')).toMatch(/everyday work/)
    expect(blockingReasonText('something_new')).toMatch(/not ready/)
  })
  it('normalises the status and refuses a body that is not one', () => {
    expect(normalizeAiSetup({ nope: true })).toBeNull()
    expect(normalizeAiSetup(null)).toBeNull()
    const n = normalizeAiSetup({ ready: false, blocking_reason: 'no_tested_ai', stack_count: 2, clis: [{ id: 'codex' }, 7] })!
    expect(n.ready).toBe(false)
    expect(n.blocking_reason).toBe('no_tested_ai')
    expect(n.stack_count).toBe(2)
    expect(n.clis.map((c) => c.id)).toEqual(['codex'])
    expect(normalizeAiSetup({ ready: true, blocking_reason: 'legacy_reason' })).toMatchObject({ blocking_reason: null, stack_count: 0, clis: [] })
  })
})

describe('provider test -> multi-step loader', () => {
  it('maps real checks to real rows plus a final Ready row', () => {
    const failing: ProviderTest = { ...passing, ok: false, checks: passing.checks.map((c) => (c.name === 'tool_calling' ? { ...c, ok: false, detail: 'The model ignored the tool' } : c)) }
    const steps = loaderSteps(failing, false)
    expect(steps.map((s) => s.label)).toEqual(['Reachable', 'Authenticated', 'Tool calling', 'Context window', 'Model available', 'Ready'])
    expect(steps.map((s) => s.state)).toEqual(['passed', 'passed', 'failed', 'passed', 'passed', 'failed'])
    expect(steps[2]!.detail).toBe('The model ignored the tool')
    expect(loaderSteps(passing, false).at(-1)!.state).toBe('passed')
  })

  it('shows what the server did not check as "unchecked", never as passed', () => {
    const partial: ProviderTest = { ...passing, ok: false, checks: passing.checks.slice(0, 2) }
    const steps = loaderSteps(partial, false)
    expect(steps.map((s) => s.state)).toEqual(['passed', 'passed', 'unchecked', 'unchecked', 'unchecked', 'failed'])
  })

  it('while the request is in flight nothing is claimed: the first row spins, the rest wait', () => {
    expect(loaderSteps(null, true).map((s) => s.state)).toEqual(['running', 'pending', 'pending', 'pending', 'pending', 'pending'])
    expect(loaderSteps(null, false).every((s) => s.state === 'pending')).toBe(true)
  })

  it('keeps an extra check the server reports', () => {
    const extra: ProviderTest = { ...passing, checks: [...passing.checks, { name: 'streaming', ok: true, detail: null }] }
    const steps = loaderSteps(extra, false)
    expect(steps.map((s) => s.name)).toContain('streaming')
    expect(steps.find((s) => s.name === 'streaming')!.label).toBe('Streaming')
  })

  it('trusts a test only when every check passed', () => {
    expect(testPassed(passing)).toBe(true)
    expect(testPassed({ ...passing, ok: false })).toBe(false)
    expect(testPassed({ ...passing, checks: [...passing.checks, { name: 'x', ok: false, detail: null }] })).toBe(false)
    expect(testPassed(null)).toBe(false)
  })

  it('normalises both the M10 shape and the older one', () => {
    expect(normalizeProviderTest({ ok: true, checks: [{ name: 'reachable', ok: true, detail: 'x' }, { nope: 1 }], served_context: 32768, latency_ms: 10, tested_at: 't' })).toEqual({
      ok: true,
      checks: [{ name: 'reachable', ok: true, detail: 'x' }],
      served_context: 32768,
      latency_ms: 10,
      tested_at: 't',
    })
    expect(normalizeProviderTest({ status: 'error', message: 'bad', checked_at: 'c', context_length_served: 8192 })).toMatchObject({ ok: false, checks: [], served_context: 8192, tested_at: 'c' })
    expect(normalizeProviderTest(null)).toMatchObject({ ok: false, checks: [] })
  })
})

describe('provider cards and models', () => {
  it('lists four providers, OpenAI-compatible needing a base URL and Ollama separate', () => {
    expect(cardFor('openai_compatible').needsBaseUrl).toBe(true)
    expect(cardFor('openrouter').needsKey).toBe(true)
    expect(cardFor('ollama').needsKey).toBe(false)
  })

  it('the form for a card carries the secret only where a key belongs', () => {
    const f = providerFormFor(cardFor('openrouter'), { baseUrl: 'http://x', model: 'm', secret: 'sk-1' })
    expect(f).toMatchObject({ kind: 'openrouter', base_url: '', default_model: 'm', secret: 'sk-1' })
    const o = providerFormFor(cardFor('ollama'), { baseUrl: 'http://127.0.0.1:11434', model: 'qwen2.5:7b', secret: 'ignored' })
    expect(o).toMatchObject({ kind: 'ollama', base_url: 'http://127.0.0.1:11434', secret: '' })
  })

  it('a test needs a model, a base URL where required, and a key unless one is saved', () => {
    const or = cardFor('openrouter')
    expect(canTestProvider(or, { baseUrl: '', model: '', secret: 'k', hasCredential: false })).toBe(false)
    expect(canTestProvider(or, { baseUrl: '', model: 'm', secret: '', hasCredential: false })).toBe(false)
    expect(canTestProvider(or, { baseUrl: '', model: 'm', secret: 'k', hasCredential: false })).toBe(true)
    expect(canTestProvider(or, { baseUrl: '', model: 'm', secret: '', hasCredential: true })).toBe(true)
    expect(canTestProvider(cardFor('openai_compatible'), { baseUrl: '', model: 'm', secret: '', hasCredential: false })).toBe(false)
    expect(canTestProvider(cardFor('openai_compatible'), { baseUrl: 'http://h/v1', model: 'm', secret: '', hasCredential: false })).toBe(true)
  })

  it('reads model lists in the usual shapes and filters them', () => {
    expect(normalizeModelList({ items: [{ id: 'a/b', context_length: 200000 }, 'c', { name: 'd' }, 5, { id: 'a/b' }] })).toEqual([
      { id: 'a/b', context_length: 200000 },
      { id: 'c', context_length: null },
      { id: 'd', context_length: null },
    ])
    expect(normalizeModelList({ data: [{ id: 'x' }] })).toEqual([{ id: 'x', context_length: null }])
    expect(normalizeModelList(['x', 'y'])).toHaveLength(2)
    expect(normalizeModelList({})).toEqual([])
    const list = normalizeModelList(['anthropic/claude', 'openai/gpt', 'meta/claude-like', 'claude-3'])
    // names that start with the query come first, then names that merely contain it, each in the provider's order
    expect(filterModels(list, 'claude').map((m) => m.id)).toEqual(['claude-3', 'anthropic/claude', 'meta/claude-like'])
    expect(filterModels(list, '')).toHaveLength(4)
    expect(filterModels(list, 'zzz')).toEqual([])
  })
})

describe('CLI summary', () => {
  it('briefs a CLI in one phrase for the summary line', () => {
    const base = { id: 'codex', display_name: 'Codex', installed: true, version: null, auth: { mode: null, configured: false, credential_id: null, last_test: null } } as unknown as CliRuntimeStatus
    expect(cliBrief({ ...base, installed: false })).toBe('not installed')
    expect(cliBrief(base)).toBe('installed, not tested')
    expect(cliBrief({ ...base, auth: { ...base.auth, last_test: { ok: true, detail: null, tested_at: null } } })).toBe('ready')
    expect(cliBrief({ ...base, auth: { ...base.auth, last_test: { ok: false, detail: null, tested_at: null } } })).toBe('test failed')
  })
})

describe('CLI tile vocabulary', () => {
  it('status line: not installed, installed, ready, failed', () => {
    expect(cliStatusLine(cli({ installed: false }))).toEqual({ label: 'Not installed', tone: 'neutral' })
    expect(cliStatusLine(cli({ auth: { mode: null, configured: false, credential_id: null, last_test: null } })).label).toBe('Installed 2.1.292')
    expect(cliStatusLine(cli()).label).toBe('Ready 2.1.292')
    expect(cliStatusLine(cli({ auth: { mode: 'api_key', configured: true, credential_id: 'c', last_test: { ok: false, detail: null, tested_at: null } } })).tone).toBe('danger')
  })

  it('sandbox: OS and Docker are green, none is a warning that says why', () => {
    expect(sandboxSummary({ available: true, kind: 'os', detail: 'Seatbelt' })).toMatchObject({ label: 'OS sandbox', tone: 'success' })
    expect(sandboxSummary({ available: true, kind: 'docker', detail: null })).toMatchObject({ label: 'Docker sandbox' })
    expect(sandboxSummary({ available: false, kind: 'none', detail: 'Docker is not running' })).toMatchObject({ label: 'No sandbox', tone: 'warning', help: 'Docker is not running' })
  })

  it('the install command is exactly what the server gave, and only when installing is possible', () => {
    expect(installCommandOf(cli({ install: { available: true, command: 'npm install -g @openai/codex', requires: ['npm'] } }))).toBe('npm install -g @openai/codex')
    expect(installCommandOf(cli({ install: { available: false, command: 'npm install -g x', requires: ['npm'] } }))).toBeNull()
    expect(installCommandOf(cli({ install: { available: true, command: null, requires: [] } }))).toBeNull()
  })

  it('reads a CLI status defensively', () => {
    expect(normalizeCli(null)).toBeNull()
    expect(normalizeCli({})).toBeNull()
    const n = normalizeCli({ id: 'codex', display_name: 'Codex', installed: true, auth_modes: ['api_key', 'weird'], auth: { mode: 'own_login', configured: true, last_test: { ok: true, detail: 'fine' } }, sandbox: { available: true, kind: 'os' }, install: { available: true, command: 'npm i -g x', requires: ['npm', 3] }, warnings: ['w', 4] })!
    expect(n.auth_modes).toEqual(['api_key'])
    expect(n.auth.last_test).toEqual({ ok: true, detail: 'fine', tested_at: null })
    expect(n.install.requires).toEqual(['npm'])
    expect(n.warnings).toEqual(['w'])
  })

  it('reads a CLI test result as the result itself or as a status carrying it', () => {
    expect(normalizeCliTest({ ok: false, detail: 'timeout', tested_at: 't' })).toEqual({ ok: false, detail: 'timeout', tested_at: 't' })
    expect(normalizeCliTest({ id: 'x', auth: { last_test: { ok: true, detail: 'OK' } } })).toEqual({ ok: true, detail: 'OK', tested_at: null })
    expect(normalizeCliTest({})).toBeNull()
  })

  it('reads the install log in the usual spellings', () => {
    expect(normalizeInstallLog({ status: 'running', lines: ['a', 'b'] })).toEqual({ state: 'running', lines: ['a', 'b'], exit_code: null, method: null, sha256: null })
    expect(normalizeInstallLog({ status: 'none', lines: [] }).state).toBe('idle')
    expect(normalizeInstallLog({ state: 'done', log: 'x\ny\n', exit_code: 0 })).toMatchObject({ state: 'succeeded', lines: ['x', 'y'] })
    expect(normalizeInstallLog({ done: true, exit_code: 1 }).state).toBe('failed')
    expect(normalizeInstallLog(null).state).toBe('idle')
  })
})
