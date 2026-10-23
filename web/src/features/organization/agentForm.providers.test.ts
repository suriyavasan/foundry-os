import { describe, expect, it } from 'vitest'
import type { ModelProvider } from '@/api/types'
import { providerOptions } from './agentForm'

const p = (over: Partial<ModelProvider>): ModelProvider => ({
  id: 'p1', name: 'Local Ollama', kind: 'ollama', base_url: null, credential_id: null, default_model: 'qwen2.5:7b', context_length: null, is_local: true, enabled: true, input_price_per_mtok_micros: null, output_price_per_mtok_micros: null, ...over,
})

describe('providerOptions', () => {
  it('lists enabled providers by name and marks local ones', () => {
    expect(providerOptions([p({}), p({ id: 'p2', name: 'OpenRouter', kind: 'openrouter', is_local: false })], '')).toEqual([
      { value: 'p1', label: 'Local Ollama · local', defaultModel: 'qwen2.5:7b' },
      { value: 'p2', label: 'OpenRouter', defaultModel: 'qwen2.5:7b' },
    ].sort((a, b) => a.label.localeCompare(b.label)))
  })
  it('hides disabled providers unless one is the current value', () => {
    const disabled = p({ id: 'p3', name: 'Off', enabled: false })
    expect(providerOptions([p({}), disabled], '').map((o) => o.value)).toEqual(['p1'])
    expect(providerOptions([p({}), disabled], 'p3').map((o) => o.label)).toContain('Off · local (disabled)')
  })
  it('keeps a legacy free-text value instead of silently dropping it', () => {
    const out = providerOptions([p({})], 'anthropic')
    expect(out.at(-1)).toMatchObject({ value: 'anthropic', unregistered: true })
    expect(out.at(-1)!.label).toMatch(/not a registered provider/)
  })
})
