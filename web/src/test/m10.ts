import type { AiSetupStatus, CliRuntimeStatus, CommandExchange, CompanyStandard, ProviderTest, Reflection, ReflectionDetail, ReflectionResponse } from '@/api/types'
import { jsonResponse, page } from './factories'

export { jsonResponse, page }

export const CHECK_NAMES = ['reachable', 'auth', 'tool_calling', 'context', 'model_exists'] as const

export function makeProviderTest(over: Partial<ProviderTest> & { failing?: string } = {}): ProviderTest {
  const { failing, ...rest } = over
  return {
    ok: !failing,
    served_context: 65_536,
    latency_ms: 310,
    tested_at: '2026-10-24T10:00:00Z',
    checks: CHECK_NAMES.map((name) => ({ name, ok: name !== failing, detail: name === failing ? `${name} failed: nope` : `${name} is fine` })),
    ...rest,
  }
}

export function makeAiSetup(over: Partial<AiSetupStatus> = {}): AiSetupStatus {
  return {
    ready: false,
    blocking_reason: 'no_tested_provider',
    management: { provider_id: null, provider_name: null, kind: null, model: null, last_test: null },
    work_mode: 'api',
    work_runtime: null,
    clis: [],
    ...over,
  }
}

export function readyAiSetup(over: Partial<AiSetupStatus> = {}): AiSetupStatus {
  return makeAiSetup({
    ready: true,
    blocking_reason: null,
    management: { provider_id: 'p1', provider_name: 'OpenRouter', kind: 'openrouter', model: 'anthropic/claude-sonnet-4.5', last_test: makeProviderTest() },
    ...over,
  })
}

export function makeCli(over: Partial<CliRuntimeStatus> = {}): CliRuntimeStatus {
  return {
    id: 'claude_code',
    display_name: 'Claude Code',
    installed: true,
    binary_path: '/opt/homebrew/bin/claude',
    version: '2.1.292',
    auth_modes: ['api_key', 'own_login'],
    auth: { mode: null, configured: false, credential_id: null, last_test: null },
    sandbox: { available: true, kind: 'os', detail: 'Seatbelt sandbox' },
    install: { available: false, command: null, requires: ['npm'] },
    login_hint: 'Run `claude` in a terminal and sign in',
    warnings: [],
    ...over,
  }
}

export function makeReflection(over: Partial<Reflection> = {}): Reflection {
  const id = over.id ?? 'rf1'
  return {
    id,
    company_id: 'c1',
    author_agent_id: 'f1',
    author_name: 'Atlas',
    kind: 'observation',
    title: 'Reviews keep asking for sources',
    body: 'Three **reviews** in a row asked the researcher to cite sources.',
    evidence: [{ type: 'task', id: 't1', label: 'Market report' }],
    proposed_standard: null,
    status: 'open',
    response_count: 0,
    created_at: '2026-10-24T09:00:00Z',
    updated_at: '2026-10-24T09:00:00Z',
    ...over,
  }
}

export function makeReflectionDetail(over: Partial<ReflectionDetail> = {}): ReflectionDetail {
  return { ...makeReflection(over), responses: [], ...over }
}

export function makeResponse(over: Partial<ReflectionResponse> = {}): ReflectionResponse {
  return { id: 'rs1', reflection_id: 'rf1', author_type: 'human', author_id: 'u', reaction: 'challenge', body: 'Not every deliverable needs sources.', created_at: '2026-10-24T09:30:00Z', ...over }
}

export function makeStandard(over: Partial<CompanyStandard> = {}): CompanyStandard {
  return {
    id: 'st1',
    company_id: 'c1',
    title: 'Cite every statistic',
    statement: 'Every number in a deliverable names its source.',
    source_reflection_id: 'rf1',
    adopted_by_user_id: 'u',
    adopted_at: '2026-10-24T09:00:00Z',
    status: 'active',
    applies_to: [],
    ...over,
  }
}

export function makeExchange(over: Partial<CommandExchange> = {}): CommandExchange {
  return {
    id: 'ex1',
    input: '/status',
    input_kind: 'command',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    status: 'done',
    output: { kind: 'text', title: 'Status', text: 'Everything is running.' },
    note_id: null,
    run_id: null,
    actions: [],
    ...over,
  }
}
