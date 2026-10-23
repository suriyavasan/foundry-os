import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/client'
import type { InvitationCreated } from '@/api/types'
import { describeInviteFailure, inviteLink, inviteOutcome, isRevocable, looksLikeInviteToken } from './invite'

const created = (over: Partial<InvitationCreated> = {}): InvitationCreated => ({
  id: 'i1',
  company_id: 'c1',
  email: 'ben@example.com',
  role: 'member',
  title: null,
  manager_ref: null,
  status: 'pending',
  expires_at: '2026-10-31T00:00:00Z',
  created_at: '2026-10-24T00:00:00Z',
  delivery: 'link',
  ...over,
})

describe('invitation link vs email', () => {
  it('shows the copyable link when the server reports delivery "link"', () => {
    const o = inviteOutcome(created({ delivery: 'link', invite_url: 'https://os.example.com/invite/abcdefghijklmnop' }), 'http://localhost:5173')
    expect(o).toMatchObject({ kind: 'link', link: 'https://os.example.com/invite/abcdefghijklmnop' })
    expect(o.text).toMatch(/No email provider is connected/)
    expect(o.text).toMatch(/nothing was sent/)
  })
  it('says "email sent" and shows no link when delivery is "email"', () => {
    const o = inviteOutcome(created({ delivery: 'email', token: 'secrettokensecrettoken' }), 'http://localhost:5173')
    expect(o.kind).toBe('email')
    expect(o.text).toBe('An invitation email was sent to ben@example.com.')
    expect('link' in o).toBe(false)
  })
  it('builds the link from the one-time token, or makes a relative url absolute', () => {
    expect(inviteLink({ token: 'abc_DEF-123456789012' }, 'http://localhost:5173/')).toBe('http://localhost:5173/invite/abc_DEF-123456789012')
    expect(inviteLink({ invite_url: '/invite/xyz' }, 'http://localhost:5173')).toBe('http://localhost:5173/invite/xyz')
    expect(inviteLink({}, 'http://localhost:5173')).toBeNull()
  })
  it('does not pretend when the server gave no link', () => {
    expect(inviteOutcome(created({ delivery: 'link' }), 'http://x').kind).toBe('link_missing')
  })
  it('only pending invitations can be revoked', () => {
    expect(isRevocable('pending')).toBe(true)
    for (const s of ['accepted', 'expired', 'revoked'] as const) expect(isRevocable(s)).toBe(false)
  })
})

describe('invitation tokens and failures', () => {
  it('checks the token looks plausible before calling the server', () => {
    expect(looksLikeInviteToken('abcdefghijklmnop')).toBe(true)
    for (const bad of [undefined, '', 'short', 'has space aaaaaaaaaaaa', 'a/b/c/d/e/f/g/h/i/j']) expect(looksLikeInviteToken(bad)).toBe(false)
  })
  it('gives one message for unknown, expired, used and revoked links', () => {
    const msgs = new Set([404, 410].map((s) => describeInviteFailure(new ApiError(s, 'x', 'x')).message))
    msgs.add(describeInviteFailure(new ApiError(400, 'invitation_expired', 'x')).message)
    expect(msgs.size).toBe(1)
    expect(describeInviteFailure(new ApiError(404, 'x', 'x')).kind).toBe('invalid')
  })
  it('maps account problems', () => {
    expect(describeInviteFailure(new ApiError(409, 'conflict', 'taken')).fields.username).toMatch(/taken/)
    expect(describeInviteFailure(new ApiError(403, 'email_mismatch', 'x')).kind).toBe('wrong_account')
    const v = describeInviteFailure(new ApiError(422, 'validation_error', 'bad', [{ loc: ['body', 'password'], msg: 'weak' }]))
    expect(v.fields).toEqual({ password: 'weak' })
  })
})
