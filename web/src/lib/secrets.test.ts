import { describe, expect, it } from 'vitest'
import { rowsFromObject, rowsToObject } from '@/components/forms/KeyValueEditor'
import { looksLikeSecretKey } from './secrets'

describe('looksLikeSecretKey', () => {
  it.each(['api_key', 'API-KEY', 'client_secret', 'auth_token', 'password', 'Authorization_Bearer', 'apiKey', 'secretValue'])('flags %s', (k) => {
    expect(looksLikeSecretKey(k)).toBe(true)
  })
  it.each(['model', 'base_url', 'timeout_seconds', 'toolsets', 'max_tokens', 'keyboard_layout', 'tokenizer'])('allows %s', (k) => {
    expect(looksLikeSecretKey(k)).toBe(false)
  })
})

describe('runtime config rows', () => {
  it('round-trips strings and JSON values', () => {
    const obj = { mode: 'fast', retries: 3, tools: ['a'] }
    expect(rowsToObject(rowsFromObject(obj)).value).toEqual(obj)
  })
  it('reports secret keys, duplicates and bad JSON per row', () => {
    const { errors } = rowsToObject([
      { key: 'api_key', value: 'x', json: false },
      { key: 'a', value: '1', json: false },
      { key: 'a', value: '2', json: false },
      { key: 'n', value: '{oops', json: true },
    ])
    expect(Object.keys(errors).map(Number)).toEqual([0, 2, 3])
  })
})
