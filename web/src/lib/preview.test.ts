import { describe, expect, it } from 'vitest'
import { isPreviewable, prettyPreview } from './preview'

describe('isPreviewable', () => {
  it.each(['text/plain', 'text/markdown', 'text/html; charset=utf-8', 'application/json', 'application/vnd.api+json', 'application/markdown'])('previews %s', (m) => {
    expect(isPreviewable(m)).toBe(true)
  })
  it.each(['application/pdf', 'image/png', 'application/zip', 'application/octet-stream'])('does not preview %s', (m) => {
    expect(isPreviewable(m)).toBe(false)
  })
})
describe('prettyPreview', () => {
  it('formats valid JSON and leaves invalid JSON alone', () => {
    expect(prettyPreview('{"a":1}', 'application/json')).toBe('{\n  "a": 1\n}')
    expect(prettyPreview('{oops', 'application/json')).toBe('{oops')
    expect(prettyPreview('# hi', 'text/markdown')).toBe('# hi')
  })
})
