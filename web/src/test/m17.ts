import type { AiConnection, AiOption, AiStackData } from '@/lib/aiStack'

export function makeConn(over: Partial<AiConnection> & { state?: AiConnection['status']['state'] } = {}): AiConnection {
  const { state, ...rest } = over
  return {
    id: 'k1',
    position: 0,
    rank_label: 'Primary',
    label: 'Anthropic API',
    engine: 'api',
    option: 'anthropic_api',
    provider_kind: 'anthropic',
    model_provider_id: 'p1',
    model: 'claude-sonnet-4-5',
    cli_id: null,
    auth_mode: null,
    credential_id: null,
    cli_provider_id: null,
    use_for: 'everything',
    share: 2,
    enabled: true,
    status: { state: state ?? 'ready', message: null, last_test: null, signed_in: null, account: null, paused_until: null },
    runs_24h: 0,
    max_concurrency: null,
    home_dir: null,
    created_at: '2026-10-28T10:00:00Z',
    ...rest,
  }
}

export const OPTIONS: AiOption[] = [
  { option: 'claude_plan', label: 'Claude, your plan', group: 'plan', engine: 'cli', cli_id: 'claude_code', provider_kind: null, available: true, reason: null, needs_install: false, install: null, warning: 'Personal plans assume ordinary individual use. An API key is the safe choice for heavy autonomy.' },
  { option: 'cursor_plan', label: 'Cursor, your plan', group: 'plan', engine: 'cli', cli_id: 'cursor', provider_kind: null, available: false, reason: 'Already connected', needs_install: false, install: null, warning: null },
  { option: 'anthropic_api', label: 'Anthropic API', group: 'api', engine: 'api', cli_id: null, provider_kind: 'anthropic', available: true, reason: null, needs_install: false, install: null, warning: null },
  { option: 'openrouter', label: 'OpenRouter', group: 'api', engine: 'api', cli_id: null, provider_kind: 'openrouter', available: true, reason: null, needs_install: false, install: null, warning: null },
  { option: 'cursor_api', label: 'Cursor API key', group: 'api', engine: 'cli', cli_id: 'cursor', provider_kind: null, available: true, reason: null, needs_install: false, install: null, warning: null },
  { option: 'codex_api', label: 'Codex (OpenAI key)', group: 'api', engine: 'cli', cli_id: 'codex', provider_kind: null, available: true, reason: null, needs_install: false, install: null, warning: null },
  { option: 'ollama', label: 'Ollama (local)', group: 'local', engine: 'api', cli_id: null, provider_kind: 'ollama', available: false, reason: 'Not found on this computer', needs_install: false, install: null, warning: null },
]

export function makeStack(items: AiConnection[] = [], over: Partial<AiStackData> = {}): AiStackData {
  return {
    items,
    ready: items.length > 0,
    blocking_reason: items.length > 0 ? null : 'no_ai',
    summary: { heavy: items.map((c) => ({ connection_id: c.id, label: c.label, percent: 100, backup: false })), everyday: items.map((c) => ({ connection_id: c.id, label: c.label, percent: 100, backup: false })) },
    options: OPTIONS,
    ...over,
  }
}

export const loginStatus = (state: 'idle' | 'running' | 'succeeded' | 'failed' = 'idle', over: Record<string, unknown> = {}) => ({
  signed_in: false,
  auth_method: null,
  email: null,
  checked_at: '2026-10-28T10:00:00Z',
  config_dir_ok: true,
  login: { state, started_at: null, finished_at: null, url: null, message: null },
  ...over,
})
