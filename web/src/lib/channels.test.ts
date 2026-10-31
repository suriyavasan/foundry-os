import { describe, expect, it } from 'vitest'
import type { ChatChannel, ChatKindInfo, ChatLink } from '@/api/types'
import { formatCountdown, kindCards, linkMessage, newLinkOf, safeHttpsUrl, secondsLeft } from './channels'

const link = (over: Partial<ChatLink> = {}): ChatLink => ({
  id: 'l1', channel_id: 'ch1', user_id: 'u1', user_name: 'Sam', display_name: null, notify: {}, linked_at: '2026-10-30T10:00:00Z', last_inbound_at: null, window_open: null, ...over,
})
const channel = (over: Partial<ChatChannel> = {}): ChatChannel => ({
  id: 'ch1', kind: 'telegram', name: '@acme_bot', status: 'active', config: {}, last_error: null, last_event_at: null, created_at: '2026-10-30T10:00:00Z', links: [], webhook_url: null, verify_token: null, invite_url: null, ...over,
})
const info = (kind: string, over: Partial<ChatKindInfo> = {}): ChatKindInfo => ({ kind, label: kind, available: true, reason: null, needs_public_url: false, ...over })

describe('safeHttpsUrl', () => {
  it('only lets https links through', () => {
    expect(safeHttpsUrl('https://t.me/acme_bot?start=ABC')).toBe('https://t.me/acme_bot?start=ABC')
    expect(safeHttpsUrl('javascript:alert(1)')).toBeNull()
    expect(safeHttpsUrl('http://t.me/x')).toBeNull()
    expect(safeHttpsUrl('not a url')).toBeNull()
    expect(safeHttpsUrl(null)).toBeNull()
  })
})

describe('link code countdown', () => {
  it('counts down to zero and never goes negative', () => {
    const exp = '2026-10-30T10:10:00Z'
    const t0 = Date.parse('2026-10-30T10:00:00Z')
    expect(secondsLeft(exp, t0)).toBe(600)
    expect(secondsLeft(exp, t0 + 599_500)).toBe(1)
    expect(secondsLeft(exp, t0 + 700_000)).toBe(0)
    expect(secondsLeft('garbage', t0)).toBe(0)
  })
  it('formats m:ss', () => {
    expect(formatCountdown(600)).toBe('10:00')
    expect(formatCountdown(125)).toBe('2:05')
    expect(formatCountdown(-3)).toBe('0:00')
  })
  it('tells Discord to send "link CODE" and others "/start CODE"', () => {
    expect(linkMessage('discord', 'ABCD2345')).toBe('link ABCD2345')
    expect(linkMessage('telegram', 'ABCD2345')).toBe('/start ABCD2345')
  })
})

describe('newLinkOf', () => {
  it('finds a link of the person that was not there when the code was issued', () => {
    const known = new Set(['l1'])
    const ch = channel({ links: [link({ id: 'l1' }), link({ id: 'l2', user_id: 'u2' })] })
    expect(newLinkOf(ch, 'u1', known)).toBeUndefined() // l2 belongs to somebody else
    expect(newLinkOf({ ...ch, links: [...ch.links, link({ id: 'l3', display_name: 'sam_tg' })] }, 'u1', known)?.id).toBe('l3')
    expect(newLinkOf(undefined, 'u1', known)).toBeUndefined()
    expect(newLinkOf(ch, undefined, known)).toBeUndefined()
  })
})

describe('kindCards', () => {
  it('follows the API order and pairs connected channels with their kind', () => {
    const cards = kindCards([info('telegram'), info('slack', { available: false, reason: 'Coming later' })], [channel()])
    expect(cards.map((c) => [c.kind, !!c.channel])).toEqual([['telegram', true], ['slack', false]])
  })
  it('keeps a connected channel whose kind the API does not list', () => {
    const cards = kindCards([], [channel({ kind: 'discord' })])
    expect(cards).toHaveLength(1)
    expect(cards[0]!.info).toBeNull()
  })
})
