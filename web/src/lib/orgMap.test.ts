import { describe, expect, it } from 'vitest'
import { makeTask } from '@/test/factories'
import { HUMAN_ID } from './orgLayout'
import { capLinks, collaborationLinks, dependencyLinks, layoutOrgMap, type MapInput, type MapNode, type OrgMapLayout } from './orgMap'

const W = 360
const H = 420

/** Cofounder `co` under the human, `managers` direct reports each with `perManager` reports. */
function company(managers: number, perManager: number, extraLeavesOnCofounder = 0): MapInput[] {
  const out: MapInput[] = [
    { id: HUMAN_ID, parentId: null, kind: 'human' },
    { id: 'co', parentId: HUMAN_ID, kind: 'cofounder' },
  ]
  for (let m = 0; m < managers; m++) {
    out.push({ id: `m${m}`, parentId: 'co', kind: 'other' })
    for (let r = 0; r < perManager; r++) out.push({ id: `m${m}r${r}`, parentId: `m${m}`, kind: 'other' })
  }
  for (let l = 0; l < extraLeavesOnCofounder; l++) out.push({ id: `l${l}`, parentId: 'co', kind: 'other' })
  return out
}

function expectFits(layout: OrgMapLayout, label: string) {
  const { nodes } = layout
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i]!
    expect(a.x - a.r, `${label}: ${a.id} left`).toBeGreaterThanOrEqual(-0.01)
    expect(a.x + a.r, `${label}: ${a.id} right`).toBeLessThanOrEqual(layout.width + 0.01)
    expect(a.y - a.r, `${label}: ${a.id} top`).toBeGreaterThanOrEqual(-0.01)
    expect(a.y + a.r, `${label}: ${a.id} bottom`).toBeLessThanOrEqual(layout.height + 0.01)
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j]!
      expect(Math.hypot(a.x - b.x, a.y - b.y), `${label}: ${a.id} overlaps ${b.id}`).toBeGreaterThanOrEqual(a.r + b.r - 0.02)
    }
  }
}

/** Small deterministic PRNG so the "random" shapes are the same on every run. */
function rng(seed: number) {
  let s = seed >>> 0
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0
    return s / 2 ** 32
  }
}

function randomCompany(n: number, seed: number): MapInput[] {
  const r = rng(seed)
  const out: MapInput[] = [
    { id: HUMAN_ID, parentId: null, kind: 'human' },
    { id: 'co', parentId: HUMAN_ID, kind: 'cofounder' },
  ]
  for (let i = 2; i < n; i++) {
    const parent = out[1 + Math.floor(r() * (out.length - 1))]!.id
    out.push({ id: `p${i}`, parentId: parent, kind: 'other' })
  }
  return out
}

describe('layoutOrgMap', () => {
  it('is deterministic: the same input gives the same positions', () => {
    const input = company(4, 5)
    const a = layoutOrgMap(input, { width: W, height: H })
    const b = layoutOrgMap(input.map((x) => ({ ...x })), { width: W, height: H })
    expect(b).toEqual(a)
  })

  it('puts the Co-founder in the centre with the Founder attached beside it, and every person exactly once', () => {
    const input = company(3, 2)
    const l = layoutOrgMap(input, { width: W, height: H })
    expect(l.nodes).toHaveLength(input.length)
    expect(new Set(l.nodes.map((n) => n.id)).size).toBe(input.length)
    const co = l.nodes.find((n) => n.id === 'co')!
    const human = l.nodes.find((n) => n.id === HUMAN_ID)!
    expect(co.ring).toBe(0)
    expect(human.ring).toBe(0)
    expect(Math.abs(co.x - W / 2)).toBeLessThan(1)
    expect(Math.hypot(co.x - human.x, co.y - human.y)).toBeLessThan(co.r + human.r + 12)
    expect(human.r).toBeLessThan(co.r)
    // the Founder is joined to the Co-founder
    expect(l.edges).toContainEqual({ id: `${HUMAN_ID}>co`, from: HUMAN_ID, to: 'co' })
  })

  it('assigns rings by depth: managers on the first ring, their reports on the next, sorted centre first', () => {
    const l = layoutOrgMap(company(3, 3), { width: W, height: H })
    const by = (id: string) => l.nodes.find((n) => n.id === id)!
    expect(by('m0').ring).toBe(1)
    expect(by('m0r0').ring).toBe(2)
    expect(l.nodes.map((n) => n.ring)).toEqual([...l.nodes.map((n) => n.ring)].sort((a, b) => a - b))
    const centre = by('co')
    const dist = (n: MapNode) => Math.hypot(n.x - centre.x, n.y - centre.y)
    const mean = (ns: MapNode[]) => ns.reduce((s, n) => s + dist(n), 0) / ns.length
    expect(mean(l.nodes.filter((n) => n.ring === 2))).toBeGreaterThan(mean(l.nodes.filter((n) => n.ring === 1)))
  })

  it('sizes circles by role: Co-founder 56, managers 44, others 34 (one step smaller above 20 people)', () => {
    const small = layoutOrgMap(company(2, 2), { width: W, height: H })
    const r = (id: string, l = small) => l.nodes.find((n) => n.id === id)!.r * 2
    expect(r('co')).toBe(56)
    expect(r('m0')).toBe(44)
    expect(r('m0r0')).toBe(34)
    const big = layoutOrgMap(company(5, 5), { width: W, height: H })
    expect(r('co', big)).toBeLessThan(56)
    expect(r('m0', big)).toBeLessThan(44)
    expect(r('m0r0', big)).toBeLessThan(34)
  })

  it('clusters reports around their manager', () => {
    const l = layoutOrgMap(company(4, 4), { width: W, height: H })
    const managers = l.nodes.filter((n) => n.ring === 1)
    let near = 0
    let all = 0
    for (const rep of l.nodes.filter((n) => n.ring === 2)) {
      const closest = managers.reduce((best, m) => (Math.hypot(m.x - rep.x, m.y - rep.y) < Math.hypot(best.x - rep.x, best.y - rep.y) ? m : best))
      all++
      if (closest.id === rep.parentId) near++
    }
    expect(near / all).toBeGreaterThan(0.75)
  })

  it('never overlaps circles or leaves the box, for up to 40 people in 360 x 420', () => {
    const shapes: Array<[string, MapInput[]]> = [
      ['one person', [{ id: HUMAN_ID, parentId: null, kind: 'human' }]],
      ['founder and co-founder', company(0, 0)],
      ['balanced 40', company(6, 5, 0).slice(0, 39).concat([{ id: 'x', parentId: 'co', kind: 'other' }])],
      ['flat 39 under the co-founder', company(0, 0, 38)],
      ['4 teams of 9', company(4, 8, 2).slice(0, 40)],
      ['deep chain', [{ id: HUMAN_ID, parentId: null, kind: 'human' }, { id: 'co', parentId: HUMAN_ID, kind: 'cofounder' }, ...Array.from({ length: 12 }, (_, i): MapInput => ({ id: `c${i}`, parentId: i === 0 ? 'co' : `c${i - 1}`, kind: 'other' }))]],
    ]
    for (const [name, input] of shapes) expectFits(layoutOrgMap(input, { width: W, height: H }), name)
    for (let n = 2; n <= 40; n++) for (const seed of [1, 2, 3]) expectFits(layoutOrgMap(randomCompany(n, seed * 100 + n), { width: W, height: H }), `random n=${n} seed=${seed}`)
  })

  it('also fits a wide box and a small one', () => {
    expectFits(layoutOrgMap(company(3, 4), { width: 560, height: 380 }), 'wide')
    expectFits(layoutOrgMap(company(2, 3), { width: 300, height: 340 }), 'small')
  })

  it('without a Co-founder the Founder is the centre; unknown, cyclic and self managers are placed, never lost', () => {
    const l = layoutOrgMap(
      [
        { id: HUMAN_ID, parentId: null, kind: 'human' },
        { id: 'a', parentId: HUMAN_ID, kind: 'other' },
        { id: 'b', parentId: 'ghost', kind: 'other' },
        { id: 'c', parentId: 'd', kind: 'other' },
        { id: 'd', parentId: 'c', kind: 'other' },
        { id: 'e', parentId: 'e', kind: 'other' },
      ],
      { width: W, height: H },
    )
    expect(l.nodes).toHaveLength(6)
    expect(l.nodes.find((n) => n.id === HUMAN_ID)!.ring).toBe(0)
    expect(l.nodes.filter((n) => n.id !== HUMAN_ID).every((n) => n.ring >= 1)).toBe(true)
    expectFits(l, 'odd')
  })

  it('handles an empty input', () => {
    expect(layoutOrgMap([], { width: W, height: H }).nodes).toEqual([])
  })
})

describe('collaboration lines', () => {
  const t = (id: string, agent: string, deps: string[] = [], status: 'in_progress' | 'done' = 'in_progress') => makeTask({ id, assignee_agent_id: agent, depends_on: deps, status })

  it('links agents whose in-progress tasks depend on each other, once per pair', () => {
    const links = dependencyLinks([t('t1', 'a', ['t2']), t('t2', 'b'), t('t3', 'b', ['t1'])])
    expect(links).toEqual([{ a: 'a', b: 'b', kind: 'dependency' }])
  })

  it('ignores finished tasks, the same agent, and tasks with no reported dependencies', () => {
    expect(dependencyLinks([t('t1', 'a', ['t2']), t('t2', 'b', [], 'done')])).toEqual([])
    expect(dependencyLinks([t('t1', 'a', ['t2']), t('t2', 'a')])).toEqual([])
    expect(dependencyLinks([t('t1', 'a'), t('t2', 'b')])).toEqual([])
  })

  it('draws at most 5 extra lines per circle and only between people on the map', () => {
    const links = Array.from({ length: 8 }, (_, i) => ({ a: 'hub', b: `n${i}`, kind: 'dependency' as const }))
    const present = new Set(['hub', ...links.map((l) => l.b)])
    const capped = capLinks(links, present)
    expect(capped).toHaveLength(5)
    expect(capLinks([{ a: 'x', b: 'ghost', kind: 'dependency' }], new Set(['x']))).toEqual([])
  })

  it('has no lines without tasks or meetings', () => {
    expect(collaborationLinks([], new Set())).toEqual([])
  })

  it('adds meeting lines first, so the per-circle cap never crowds out a live meeting', () => {
    const meetings = [{ a: 'a', b: 'b', kind: 'meeting' as const }]
    const present = new Set(['a', 'b', 'c'])
    expect(collaborationLinks([t('t1', 'a', ['t2']), t('t2', 'c')], present, meetings).map((l) => l.kind)).toEqual(['meeting', 'dependency'])
    const hub = Array.from({ length: 6 }, (_, i) => ({ a: 'hub', b: `n${i}`, kind: 'meeting' as const }))
    expect(collaborationLinks([], new Set(['hub', ...hub.map((l) => l.b)]), hub)).toHaveLength(5)
  })
})
