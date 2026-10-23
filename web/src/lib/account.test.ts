import { describe, expect, it } from 'vitest'
import type { ApiToken, SessionInfo } from '@/api/types'
import { TOKEN_SCOPES, activeTokens, describeUserAgent, sortSessions, validateTokenName } from './account'

describe('account helpers', () => {
  it('offers read, write and admin scopes with explanations', () => {
    expect(TOKEN_SCOPES.map((s) => s.value)).toEqual(['read', 'write', 'admin'])
    expect(TOKEN_SCOPES.every((s) => s.help.length > 10)).toBe(true)
  })
  it('validates token names', () => {
    expect(validateTokenName(' ')).toMatch(/Name the token/)
    expect(validateTokenName('x'.repeat(81))).toMatch(/at most 80/)
    expect(validateTokenName('laptop')).toBeNull()
  })
  it('describes user agents plainly', () => {
    expect(describeUserAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36')).toBe('Chrome on macOS')
    expect(describeUserAgent('Mozilla/5.0 (Windows NT 10.0) Gecko/20100101 Firefox/121.0')).toBe('Firefox on Windows')
    expect(describeUserAgent('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1')).toBe('Safari on iOS')
    expect(describeUserAgent('python-httpx/0.27')).toBe('Command line')
    expect(describeUserAgent(null)).toBe('Unknown device')
  })
  it('lists the current session first, then the most recent', () => {
    const s = (id: string, current: boolean, seen: string): SessionInfo => ({ id, current, created_at: seen, last_seen_at: seen, expires_at: seen, ip: null, user_agent: null })
    expect(sortSessions([s('old', false, '2026-10-01'), s('cur', true, '2026-09-01'), s('new', false, '2026-10-20')]).map((x) => x.id)).toEqual(['cur', 'new', 'old'])
  })
  it('hides revoked tokens', () => {
    const t = (id: string, revoked_at: string | null): ApiToken => ({ id, name: id, scope: 'read', created_at: 'x', last_used_at: null, revoked_at })
    expect(activeTokens([t('a', null), t('b', '2026-10-01')]).map((x) => x.id)).toEqual(['a'])
  })
})
