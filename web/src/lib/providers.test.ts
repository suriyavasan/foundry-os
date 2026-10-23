import { describe, expect, it } from 'vitest'
import {
  deriveIsLocal,
  describePrices,
  emptyProviderForm,
  formFromDetected,
  isFreeProvider,
  normalizeDetect,
  normalizeTestResult,
  parseContextLength,
  redactSecret,
  toCreateCredentialBody,
  toCreateProviderBody,
  validateProviderForm,
} from './providers'

const SECRET = 'sk-or-v1-abcdef0123456789'
const cloud = () => emptyProviderForm({ name: 'OpenRouter', kind: 'openrouter', default_model: 'qwen/qwen3', input_price: '0.15', output_price: '0.6', secret: SECRET })

describe('provider form validation', () => {
  it('accepts a local Ollama with just a name and URL', () => {
    expect(validateProviderForm(emptyProviderForm({ name: 'Local', kind: 'ollama', base_url: 'http://127.0.0.1:11434' }))).toEqual({})
  })
  it('requires a name, and a base URL for ollama and openai-compatible', () => {
    expect(validateProviderForm(emptyProviderForm({ kind: 'ollama' }))).toMatchObject({ name: expect.any(String), base_url: expect.any(String) })
    expect(validateProviderForm(emptyProviderForm({ name: 'x', kind: 'openai_compatible' })).base_url).toBeTruthy()
    expect(validateProviderForm({ ...cloud(), base_url: '' }).base_url).toBeUndefined()
  })
  it('rejects non-http URLs and credentials embedded in the URL', () => {
    expect(validateProviderForm(emptyProviderForm({ name: 'x', base_url: 'ftp://host' })).base_url).toBeTruthy()
    expect(validateProviderForm(emptyProviderForm({ name: 'x', base_url: 'not a url' })).base_url).toBeTruthy()
    expect(validateProviderForm(emptyProviderForm({ name: 'x', base_url: 'http://user:pw@host' })).base_url).toMatch(/credentials/)
  })
  it('requires an API key for cloud providers unless one is already saved', () => {
    expect(validateProviderForm({ ...cloud(), secret: '' }).secret).toBeTruthy()
    expect(validateProviderForm({ ...cloud(), secret: '' }, { hasCredential: true }).secret).toBeUndefined()
    expect(validateProviderForm({ ...cloud(), secret: 'has space' }).secret).toBeTruthy()
  })
  it('validates prices and context length', () => {
    expect(validateProviderForm({ ...cloud(), input_price: 'abc' }).input_price).toBeTruthy()
    expect(validateProviderForm({ ...cloud(), output_price: '-1' }).output_price).toBeTruthy()
    expect(validateProviderForm({ ...cloud(), input_price: '1e3' }).input_price).toBeTruthy()
    expect(validateProviderForm({ ...cloud(), context_length: '12.5' }).context_length).toBeTruthy()
    expect(validateProviderForm({ ...cloud(), context_length: '0' }).context_length).toBeTruthy()
    expect(parseContextLength('32,768')).toEqual({ value: 32768 })
    expect(parseContextLength('')).toEqual({ value: null })
  })
})

describe('provider request bodies', () => {
  it('converts USD per 1M tokens to exact integer micros', () => {
    const body = toCreateProviderBody(cloud(), 'cred-1')
    expect(body.input_price_per_mtok_micros).toBe(150_000)
    expect(body.output_price_per_mtok_micros).toBe(600_000)
    expect(toCreateProviderBody({ ...cloud(), input_price: '0.1', output_price: '3' }, null)).toMatchObject({ input_price_per_mtok_micros: 100_000, output_price_per_mtok_micros: 3_000_000 })
  })
  it('keeps unknown prices null, never zero', () => {
    const body = toCreateProviderBody({ ...cloud(), input_price: '', output_price: '' }, 'c')
    expect(body.input_price_per_mtok_micros).toBeNull()
    expect(body.output_price_per_mtok_micros).toBeNull()
  })
  it('never puts the secret in the provider body; it references the credential by id', () => {
    const body = toCreateProviderBody(cloud(), 'cred-1')
    expect(JSON.stringify(body)).not.toContain(SECRET)
    expect(body.credential_id).toBe('cred-1')
  })
  it('puts the secret only in the credential body, trimmed', () => {
    expect(toCreateCredentialBody({ ...cloud(), secret: `  ${SECRET}\n`, credential_name: 'main' })).toEqual({ name: 'main', kind: 'api_key', value: SECRET })
    expect(toCreateCredentialBody({ ...cloud(), credential_name: '' })?.name).toBe('OpenRouter-key')
    expect(toCreateCredentialBody({ ...cloud(), secret: '' })).toBeNull()
    expect(toCreateCredentialBody({ ...emptyProviderForm({ name: 'L', kind: 'ollama' }), secret: SECRET })).toBeNull() // Ollama takes no key
  })
  it('derives "local" from the kind and URL instead of asking', () => {
    expect(deriveIsLocal('ollama', '')).toBe(true)
    expect(deriveIsLocal('openai_compatible', 'http://localhost:8000/v1')).toBe(true)
    expect(deriveIsLocal('openai_compatible', 'https://api.example.com/v1')).toBe(false)
    expect(deriveIsLocal('openrouter', 'http://localhost:1')).toBe(false)
    expect(toCreateProviderBody(emptyProviderForm({ name: 'L', kind: 'ollama', base_url: 'http://127.0.0.1:11434' }), null).is_local).toBe(true)
  })
  it('scrubs the secret from messages', () => {
    expect(redactSecret(`bad key ${SECRET} rejected`, SECRET)).toBe('bad key [redacted] rejected')
    expect(redactSecret('abc', 'abc')).toBe('abc') // too short to be a real secret: nothing to scrub reliably
  })
})

describe('display helpers', () => {
  it('describes prices honestly', () => {
    expect(describePrices({ input_price_per_mtok_micros: 150_000, output_price_per_mtok_micros: 600_000 })).toBe('$0.15 in / $0.60 out per 1M')
    expect(describePrices({ input_price_per_mtok_micros: null, output_price_per_mtok_micros: null })).toBe('Unpriced')
    expect(describePrices({ input_price_per_mtok_micros: 0, output_price_per_mtok_micros: null })).toContain('unknown')
  })
  it('calls only zero-priced non-local providers free (null is unknown)', () => {
    expect(isFreeProvider({ is_local: false, input_price_per_mtok_micros: 0, output_price_per_mtok_micros: 0 })).toBe(true)
    expect(isFreeProvider({ is_local: false, input_price_per_mtok_micros: null, output_price_per_mtok_micros: null })).toBe(false)
    expect(isFreeProvider({ is_local: true, input_price_per_mtok_micros: 0, output_price_per_mtok_micros: 0 })).toBe(false) // shown as "local" instead
  })
  it('pre-fills the form from a detected model', () => {
    expect(formFromDetected('http://127.0.0.1:11434', { name: 'qwen2.5:7b', size_bytes: 1, context_length: 32768 })).toMatchObject({ kind: 'ollama', base_url: 'http://127.0.0.1:11434', default_model: 'qwen2.5:7b', context_length: '32768' })
  })
})

describe('response normalisation', () => {
  it('reads detect results in several shapes', () => {
    expect(normalizeDetect({ reachable: true, base_url: 'http://127.0.0.1:11434', models: [{ name: 'qwen2.5:7b', size: 4_700_000_000 }, 'olmo2:13b', { junk: 1 }] })).toEqual({
      reachable: true,
      base_url: 'http://127.0.0.1:11434',
      models: [
        { name: 'qwen2.5:7b', size_bytes: 4_700_000_000, context_length: null },
        { name: 'olmo2:13b', size_bytes: null, context_length: null },
      ],
      message: null,
    })
    expect(normalizeDetect({ items: [{ model: 'a' }] }).models.map((m) => m.name)).toEqual(['a'])
    expect(normalizeDetect({ available: false, error: 'connection refused' })).toMatchObject({ reachable: false, message: 'connection refused', models: [] })
    expect(normalizeDetect(null)).toMatchObject({ reachable: false, models: [] })
  })
  it('passes server-written test warnings through verbatim and de-duplicates them', () => {
    const w = 'Ollama serves qwen2.5:7b with a 4096-token context, less than the 32768 configured.'
    const r = normalizeTestResult({ ok: true, message: 'Model answered', latency_ms: 812, model: 'qwen2.5:7b', warnings: [w], context_warning: w })
    expect(r).toEqual({ ok: true, message: 'Model answered', warnings: [w], latency_ms: 812, model: 'qwen2.5:7b' })
  })
  it('treats a missing success flag as a failure, not a pass', () => {
    expect(normalizeTestResult({}).ok).toBe(false)
    expect(normalizeTestResult({ status: 'ok' }).ok).toBe(true)
    expect(normalizeTestResult({ success: false, error: 'bad key' })).toMatchObject({ ok: false, message: 'bad key' })
  })
})
