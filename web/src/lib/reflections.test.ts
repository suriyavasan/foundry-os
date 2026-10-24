import { describe, expect, it } from 'vitest'
import type { CompanyStandard, Reflection } from '@/api/types'
import {
  ADOPTION_NOTICE,
  adoptionNotice,
  canReact,
  emptyStandardForm,
  endorsementAdopts,
  evidenceChips,
  parseAppliesTo,
  reactionResultText,
  reflectionExcerpt,
  responseAuthor,
  sendLabel,
  sortReflections,
  splitStandards,
  toStandardBody,
  validateReaction,
  validateStandardForm,
} from './reflections'

const refl = (over: Partial<Reflection> = {}): Reflection => ({
  id: 'r1',
  company_id: 'c1',
  author_agent_id: 'f1',
  kind: 'observation',
  title: 'Reviews keep asking for sources',
  body: 'Three **reviews** in a row asked for sources.',
  evidence: [],
  proposed_standard: null,
  status: 'open',
  created_at: '2026-10-24T09:00:00Z',
  updated_at: '2026-10-24T09:00:00Z',
  ...over,
})

describe('reactions', () => {
  it('endorsing a standard proposal adopts it, and the UI says so', () => {
    const proposal = refl({ kind: 'standard_proposal', proposed_standard: 'Cite every statistic.' })
    expect(endorsementAdopts(proposal)).toBe(true)
    expect(adoptionNotice(proposal)).toBe(ADOPTION_NOTICE)
    expect(ADOPTION_NOTICE).toMatch(/adopts this as a company standard/)
    expect(sendLabel('endorse', true)).toBe('Endorse and adopt as a standard')
    expect(reactionResultText('endorse', true)).toMatch(/Adopted as a company standard/)
  })

  it('endorsing anything else, or an already adopted proposal, adopts nothing', () => {
    expect(endorsementAdopts(refl())).toBe(false)
    expect(adoptionNotice(refl())).toBeNull()
    expect(endorsementAdopts(refl({ kind: 'standard_proposal', proposed_standard: 'x', status: 'adopted' }))).toBe(false)
    expect(endorsementAdopts(refl({ kind: 'standard_proposal', proposed_standard: '  ' }))).toBe(false)
    expect(sendLabel('endorse', false)).toBe('Endorse')
    expect(reactionResultText('endorse', false)).toBe('Endorsed.')
    expect(reactionResultText('challenge', false)).toMatch(/AI Co-founder will answer or revise/)
  })

  it('only open, revised and endorsed reflections take reactions', () => {
    for (const s of ['open', 'revised', 'endorsed'] as const) expect(canReact({ status: s })).toBe(true)
    for (const s of ['adopted', 'rejected', 'archived'] as const) expect(canReact({ status: s })).toBe(false)
  })

  it('challenge, refine and comment need words; an endorsement may be silent', () => {
    expect(validateReaction('endorse', '  ')).toEqual({ ok: true, body: '', message: null })
    expect(validateReaction('challenge', ' ')).toMatchObject({ ok: false, message: expect.stringMatching(/disagree/) })
    expect(validateReaction('refine', '')).toMatchObject({ ok: false, message: expect.stringMatching(/should change/) })
    expect(validateReaction('comment', '')).toMatchObject({ ok: false })
    expect(validateReaction('refine', ' shorter ')).toEqual({ ok: true, body: 'shorter', message: null })
    expect(validateReaction('comment', 'x'.repeat(4001)).ok).toBe(false)
  })

  it('names who responded: the signed-in human as You · Founder, an agent by name', () => {
    expect(responseAuthor({ author_type: 'human', author_id: 'u1' }, { meUserId: 'u1' })).toBe('You · Founder')
    expect(responseAuthor({ author_type: 'human', author_id: 'u2' }, { meUserId: 'u1' })).toBe('Team member')
    expect(responseAuthor({ author_type: 'agent', author_id: 'f1' }, { agentName: () => 'Atlas' })).toBe('Atlas · AI Co-founder')
    expect(responseAuthor({ author_type: 'agent', author_id: null }, {})).toBe('AI Co-founder')
  })
})

describe('listing helpers', () => {
  it('sorts newest first', () => {
    const list = [refl({ id: 'a', created_at: '2026-10-24T08:00:00Z' }), refl({ id: 'b', created_at: '2026-10-24T10:00:00Z' }), refl({ id: 'c', created_at: '2026-10-24T09:00:00Z' })]
    expect(sortReflections(list).map((r) => r.id)).toEqual(['b', 'c', 'a'])
  })

  it('previews markdown as plain text and cuts on a word', () => {
    expect(reflectionExcerpt('# Title\n\nSome **bold** and `code` and [a link](http://x).')).toBe('Title Some bold and code and a link.')
    const long = 'word '.repeat(100)
    const cut = reflectionExcerpt(long, 50)
    expect(cut.length).toBeLessThanOrEqual(51)
    expect(cut.endsWith('…')).toBe(true)
  })

  it('evidence chips link to the panel of their record when there is one', () => {
    const chips = evidenceChips([
      { type: 'task', id: 't1', label: 'Draft the plan' },
      { type: 'artifact', id: 'a1', label: null },
      { type: 'run', id: 'r1', label: 'A run' },
    ])
    expect(chips[0]).toMatchObject({ typeLabel: 'Task', label: 'Draft the plan', panel: { kind: 'task', id: 't1' } })
    expect(chips[1]).toMatchObject({ label: 'Artifact', panel: { kind: 'artifact', id: 'a1' } })
    expect(chips[2]!.panel).toBeNull() // no run panel: plain text, not a dead button
    expect(evidenceChips(undefined)).toEqual([])
  })
})

describe('standards', () => {
  const std = (over: Partial<CompanyStandard>): CompanyStandard => ({ id: 's', company_id: 'c1', title: 'T', statement: 'S', source_reflection_id: null, adopted_by_user_id: 'u1', adopted_at: '2026-10-24T09:00:00Z', status: 'active', applies_to: [], ...over })

  it('splits active from retired, newest adoption first', () => {
    const r = splitStandards([std({ id: 'a', adopted_at: '2026-10-01T00:00:00Z' }), std({ id: 'b', adopted_at: '2026-10-05T00:00:00Z' }), std({ id: 'c', status: 'retired' })])
    expect(r.active.map((s) => s.id)).toEqual(['b', 'a'])
    expect(r.retired.map((s) => s.id)).toEqual(['c'])
  })

  it('validates the form and builds the body', () => {
    expect(validateStandardForm({ title: '', statement: '', appliesTo: '' })).toEqual({ title: 'Give the standard a short title', statement: 'Write the rule itself, in one or two sentences' })
    expect(validateStandardForm({ title: 'a'.repeat(121), statement: 'b'.repeat(501), appliesTo: '' })).toMatchObject({ title: expect.stringMatching(/at most 120/), statement: expect.stringMatching(/at most 500/) })
    expect(toStandardBody({ title: ' Cite ', statement: ' Name the source. ', appliesTo: 'Research, Marketing team,, research' })).toEqual({ title: 'Cite', statement: 'Name the source.', applies_to: ['research', 'marketing-team'] })
    expect(toStandardBody({ title: 'T', statement: 'S', appliesTo: '' })).toEqual({ title: 'T', statement: 'S' })
    expect(parseAppliesTo('a, b\nc')).toEqual(['a', 'b', 'c'])
    expect(emptyStandardForm(std({ title: 'X', statement: 'Y', applies_to: ['r', 's'] }))).toEqual({ title: 'X', statement: 'Y', appliesTo: 'r, s' })
  })
})
