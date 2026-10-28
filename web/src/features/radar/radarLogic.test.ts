import { describe, expect, it } from 'vitest'
import { buildCommands, filterCommands } from '@/lib/commands'
import { parsePanel, withPanel } from '@/lib/panels'
import { makeRadarSource } from './radarFixtures'
import { disabledReason, engagementText, evidenceCount, fetchStatus, hostOf, ideaActions, intervalLabel, isHttpUrl, isRadarTab, priorityLabel, readVia, safeHref } from './radarLogic'

describe('safeHref', () => {
  it('lets only http and https through', () => {
    expect(safeHref('https://example.com/a?b=1')).toBe('https://example.com/a?b=1')
    expect(safeHref('http://example.com')).toBe('http://example.com/')
    for (const bad of ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'data:text/html,<b>x</b>', 'vbscript:x', '//evil.test/x', '/relative', 'not a url', '', null, undefined]) {
      expect(safeHref(bad as string)).toBeNull()
    }
  })
  it('validates what the owner types as a feed address', () => {
    expect(isHttpUrl(' https://example.com/feed.xml ')).toBe(true)
    expect(isHttpUrl('ftp://example.com/feed')).toBe(false)
    expect(isHttpUrl('example.com')).toBe(false)
  })
  it('shows a host without www', () => {
    expect(hostOf('https://www.example.com/x')).toBe('example.com')
    expect(hostOf('nonsense')).toBe('nonsense')
  })
})

describe('labels', () => {
  it('describes how a source is read', () => {
    expect(readVia(makeRadarSource({ access_method: 'rss' }))).toBe('RSS feed')
    expect(readVia(makeRadarSource({ read_via: 'web_search', access_method: 'search_engine_only' }))).toBe('Read by agents through web search, not fetched')
  })
  it('maps fetch statuses and unknown ones honestly', () => {
    expect(fetchStatus('robots_disallowed')).toEqual({ label: 'Blocked by robots.txt', tone: 'warning' })
    expect(fetchStatus(null).label).toBe('Not fetched yet')
    expect(fetchStatus('something_new').label).toBe('something new')
  })
  it('explains why a source is off', () => {
    expect(disabledReason('terms')).toMatch(/terms/)
    expect(disabledReason(null)).toBeNull()
  })
  it('formats intervals, engagement, priority and evidence', () => {
    expect(intervalLabel(21_600)).toBe('every 6 h')
    expect(intervalLabel(86_400)).toBe('daily')
    expect(intervalLabel(3_600)).toBe('hourly')
    expect(engagementText({ points: 1, comments: 20_000, junk: '<b>x</b>' })).toBe('1 point · 20,000 comments')
    expect(engagementText({ points: 'many' })).toBeNull()
    expect(priorityLabel(60)).toBe('Very high')
    expect(priorityLabel(2)).toBe('Low')
    expect(evidenceCount({ item_ids: ['a'], artifact_ids: [], task_ids: ['t'], urls: ['u', 'v'] })).toBe(4)
  })
  it('offers idea actions only to people who may decide, and only where they apply', () => {
    expect(ideaActions({ status: 'open' }, true)).toEqual(['accept', 'reject'])
    expect(ideaActions({ status: 'open' }, false)).toEqual([])
    expect(ideaActions({ status: 'rejected' }, true)).toEqual(['reopen'])
    expect(ideaActions({ status: 'accepted' }, true)).toEqual([])
    expect(ideaActions({ status: 'done' }, true)).toEqual([])
  })
  it('knows its tabs', () => {
    expect(isRadarTab('ideas')).toBe(true)
    expect(isRadarTab('wallet')).toBe(false)
    expect(isRadarTab(null)).toBe(false)
  })
})

describe('registration', () => {
  it('parses the radar panel and round-trips it', () => {
    expect(parsePanel('?panel=radar')).toEqual({ kind: 'radar' })
    expect(parsePanel(withPanel('', { kind: 'radar' }))).toEqual({ kind: 'radar' })
    expect(parsePanel('?panel=radar:1')).toBeNull()
  })
  it('is reachable from the command palette by its name and its subject words', () => {
    const cmds = buildCommands({ agents: [], tasks: [], companyStatus: 'active', workEngine: true })
    expect(cmds.find((c) => c.id === 'panel:radar')).toMatchObject({ label: 'Open Radar', action: { type: 'panel', panel: { kind: 'radar' } } })
    for (const q of ['radar', 'competitors', 'self-critique']) expect(filterCommands(cmds, q).map((c) => c.id)).toContain('panel:radar')
  })
})
