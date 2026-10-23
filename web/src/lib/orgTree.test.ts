import { describe, expect, it } from 'vitest'
import { makeAgent } from '@/test/factories'
import { buildOrgTree, flattenOrgTree, type OrgNode } from './orgTree'

const names = (nodes: OrgNode[]) => nodes.map((n) => n.agent.name)

describe('buildOrgTree', () => {
  it('nests agents under their managers, founder first', () => {
    const founder = makeAgent({ id: 'f', name: 'Atlas', kind: 'founder', reports_to_human: true })
    const lead = makeAgent({ id: 'l', name: 'Lead', kind: 'lead', manager_agent_id: 'f' })
    const s1 = makeAgent({ id: 's1', name: 'Bea', manager_agent_id: 'l' })
    const s2 = makeAgent({ id: 's2', name: 'Abe', manager_agent_id: 'l' })
    const tree = buildOrgTree([s1, s2, lead, founder])
    expect(names(tree)).toEqual(['Atlas'])
    expect(names(tree[0]!.children)).toEqual(['Lead'])
    expect(names(tree[0]!.children[0]!.children)).toEqual(['Abe', 'Bea'])
    expect(tree[0]!.children[0]!.children[0]!.depth).toBe(2)
    expect(tree[0]!.issue).toBeUndefined()
  })

  it('returns an empty tree for no agents', () => {
    expect(buildOrgTree([])).toEqual([])
  })

  it('flags agents with no manager and keeps them after human-reporting roots', () => {
    const founder = makeAgent({ id: 'f', name: 'Atlas', kind: 'founder', reports_to_human: true })
    const loose = makeAgent({ id: 'x', name: 'Loose' })
    const tree = buildOrgTree([loose, founder])
    expect(names(tree)).toEqual(['Atlas', 'Loose'])
    expect(tree[1]!.issue).toBe('no_manager')
  })

  it('treats a manager that is not in the list as a flagged root', () => {
    const orphan = makeAgent({ id: 'o', name: 'Orphan', manager_agent_id: 'ghost' })
    const kid = makeAgent({ id: 'k', name: 'Kid', manager_agent_id: 'o' })
    const tree = buildOrgTree([kid, orphan])
    expect(names(tree)).toEqual(['Orphan'])
    expect(tree[0]!.issue).toBe('missing_manager')
    expect(names(tree[0]!.children)).toEqual(['Kid'])
  })

  it('does not loop on a manager cycle and keeps every agent exactly once', () => {
    const a = makeAgent({ id: 'a', name: 'A', manager_agent_id: 'c' })
    const b = makeAgent({ id: 'b', name: 'B', manager_agent_id: 'a' })
    const c = makeAgent({ id: 'c', name: 'C', manager_agent_id: 'b' })
    const founder = makeAgent({ id: 'f', name: 'Atlas', kind: 'founder', reports_to_human: true })
    const tree = buildOrgTree([a, b, c, founder])
    const flat = flattenOrgTree(tree)
    expect(flat.map((n) => n.agent.id).sort()).toEqual(['a', 'b', 'c', 'f'])
    expect(tree.some((n) => n.issue === 'cycle')).toBe(true)
  })

  it('handles an agent that manages itself', () => {
    const me = makeAgent({ id: 's', name: 'Selfie', manager_agent_id: 's' })
    const tree = buildOrgTree([me])
    expect(tree).toHaveLength(1)
    expect(tree[0]!.issue).toBe('cycle')
    expect(tree[0]!.children).toEqual([])
  })

  it('orders siblings by kind then name', () => {
    const f = makeAgent({ id: 'f', name: 'Atlas', kind: 'founder', reports_to_human: true })
    const r = makeAgent({ id: 'r', name: 'Aaa', kind: 'reviewer', manager_agent_id: 'f' })
    const s = makeAgent({ id: 's', name: 'Zzz', kind: 'specialist', manager_agent_id: 'f' })
    const l = makeAgent({ id: 'l', name: 'Mmm', kind: 'lead', manager_agent_id: 'f' })
    expect(names(buildOrgTree([r, s, l, f])[0]!.children)).toEqual(['Mmm', 'Zzz', 'Aaa'])
  })
})
