import { describe, expect, it } from 'vitest'
import { makeAgent } from '@/test/factories'
import { HUMAN_ID, edgePath, expandableIds, layoutOrg } from './orgLayout'
import { buildOrgTree } from './orgTree'

const founder = makeAgent({ id: 'f', name: 'Atlas', kind: 'founder', reports_to_human: true })
const mk = (id: string, manager: string | null, over = {}) => makeAgent({ id, name: id, manager_agent_id: manager, ...over })

const byId = (l: ReturnType<typeof layoutOrg>) => new Map(l.nodes.map((n) => [n.id, n]))

describe('layoutOrg', () => {
  it('puts the human root above the founder and links them', () => {
    const l = layoutOrg(buildOrgTree([founder]))
    const m = byId(l)
    expect(m.get(HUMAN_ID)!.depth).toBe(0)
    expect(m.get('f')!.depth).toBe(1)
    expect(m.get(HUMAN_ID)!.x).toBe(m.get('f')!.x)
    expect(l.edges).toEqual([{ id: `${HUMAN_ID}->f`, from: HUMAN_ID, to: 'f' }])
  })

  it('can omit the human root', () => {
    const l = layoutOrg(buildOrgTree([founder]), { withHuman: false })
    expect(l.nodes.map((n) => n.id)).toEqual(['f'])
    expect(l.edges).toEqual([])
  })

  it('renders an empty org as just the human root', () => {
    const l = layoutOrg([])
    expect(l.nodes.map((n) => n.id)).toEqual([HUMAN_ID])
    expect(l.width).toBeGreaterThan(0)
  })

  it('centres a parent over its children and never overlaps siblings', () => {
    const agents = [founder, mk('a', 'f'), mk('b', 'f'), mk('c', 'f'), mk('a1', 'a'), mk('a2', 'a')]
    const l = layoutOrg(buildOrgTree(agents), { nodeWidth: 100, gapX: 20 })
    const m = byId(l)
    const f = m.get('f')!
    const xs = ['a', 'b', 'c'].map((i) => m.get(i)!.x)
    expect(f.x).toBeCloseTo((xs[0]! + xs[2]!) / 2)
    const row = l.nodes.filter((n) => n.depth === 3).sort((p, q) => p.x - q.x)
    expect(row.map((n) => n.id)).toEqual(['a1', 'a2'])
    for (const depth of [2, 3]) {
      const sorted = l.nodes.filter((n) => n.depth === depth).sort((p, q) => p.x - q.x)
      for (let i = 1; i < sorted.length; i++) expect(sorted[i]!.x - sorted[i - 1]!.x).toBeGreaterThanOrEqual(100 + 20 - 0.001)
    }
    // children of 'a' are centred under it
    expect(m.get('a')!.x).toBeCloseTo((m.get('a1')!.x + m.get('a2')!.x) / 2)
  })

  it('keeps every node inside the reported bounds', () => {
    const agents = [founder, ...Array.from({ length: 7 }, (_, i) => mk(`s${i}`, 'f'))]
    const l = layoutOrg(buildOrgTree(agents))
    for (const n of l.nodes) {
      expect(n.x - l.nodeWidth / 2).toBeGreaterThanOrEqual(0)
      expect(n.x + l.nodeWidth / 2).toBeLessThanOrEqual(l.width + 0.001)
      expect(n.y + l.nodeHeight).toBeLessThanOrEqual(l.height + 0.001)
    }
  })

  it('collapses a subtree to a leaf and reports how many people it hides', () => {
    const agents = [founder, mk('a', 'f'), mk('a1', 'a'), mk('a2', 'a'), mk('a11', 'a1')]
    const open = layoutOrg(buildOrgTree(agents))
    const closed = layoutOrg(buildOrgTree(agents), { collapsed: new Set(['a']) })
    expect(open.nodes).toHaveLength(6)
    expect(closed.nodes.map((n) => n.id).sort()).toEqual([HUMAN_ID, 'a', 'f'].sort())
    const a = byId(closed).get('a')!
    expect(a.collapsed).toBe(true)
    expect(a.hiddenCount).toBe(3)
    expect(a.childCount).toBe(2)
    expect(closed.width).toBeLessThan(open.width)
    expect(closed.edges.some((e) => e.to === 'a1')).toBe(false)
  })

  it('ignores collapse requests for leaves', () => {
    const l = layoutOrg(buildOrgTree([founder, mk('a', 'f')]), { collapsed: new Set(['a']) })
    expect(byId(l).get('a')!.collapsed).toBe(false)
  })

  it('shows every agent exactly once even with a manager cycle', () => {
    const x = mk('x', 'y')
    const y = mk('y', 'x')
    const l = layoutOrg(buildOrgTree([founder, x, y]))
    const ids = l.nodes.map((n) => n.id).filter((i) => i !== HUMAN_ID).sort()
    expect(ids).toEqual(['f', 'x', 'y'])
    expect(new Set(l.nodes.map((n) => n.id)).size).toBe(l.nodes.length)
    expect(l.nodes.some((n) => n.issue === 'cycle')).toBe(true)
  })

  it('shows agents with a missing or absent manager as flagged roots under the human', () => {
    const orphan = mk('o', 'ghost')
    const loose = mk('l', null)
    const l = layoutOrg(buildOrgTree([founder, orphan, loose]))
    const m = byId(l)
    expect(m.get('o')!.issue).toBe('missing_manager')
    expect(m.get('l')!.issue).toBe('no_manager')
    expect(m.get('o')!.parentId).toBe(HUMAN_ID)
    expect(m.get('l')!.depth).toBe(1)
  })

  it('handles a self-managed agent', () => {
    const self = mk('s', 's')
    const l = layoutOrg(buildOrgTree([founder, self]))
    expect(l.nodes.filter((n) => n.id === 's')).toHaveLength(1)
  })

  it('is deterministic', () => {
    const agents = [founder, mk('b', 'f'), mk('a', 'f')]
    expect(layoutOrg(buildOrgTree(agents))).toEqual(layoutOrg(buildOrgTree([...agents].reverse())))
  })
})

describe('edgePath', () => {
  const p = { id: 'p', agent: null, parentId: null, depth: 0, x: 100, y: 0, childCount: 1, hiddenCount: 0, collapsed: false }
  it('is a straight line for vertically aligned nodes', () => {
    expect(edgePath(p, { ...p, id: 'c', x: 100, y: 150 }, 100, 50)).toBe('M 100 100 V 150')
  })
  it('elbows toward the child side', () => {
    const d = edgePath(p, { ...p, id: 'c', x: 200, y: 150 }, 100, 50)
    expect(d.startsWith('M 100 100')).toBe(true)
    expect(d.endsWith('V 150')).toBe(true)
    expect(d).toContain('H')
  })
})

describe('expandableIds', () => {
  it('lists only nodes with reports', () => {
    const tree = buildOrgTree([founder, mk('a', 'f'), mk('b', 'a')])
    expect(expandableIds(tree).sort()).toEqual(['a', 'f'])
  })
})
