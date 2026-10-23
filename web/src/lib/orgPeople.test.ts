import { describe, expect, it } from 'vitest'
import { makeAgent, makeMember } from '@/test/factories'
import { buildPeopleTree } from './orgPeople'
import { flattenOrgTree, pruneInactive, type OrgPerson } from './orgTree'
import { HUMAN_ID, layoutOrg } from './orgLayout'

const label = (n: OrgPerson) => n.agent?.name ?? n.member?.display_name ?? '?'
const names = (nodes: OrgPerson[]) => nodes.map(label)

const atlas = makeAgent({ id: 'f', name: 'Atlas', kind: 'founder', reports_to_human: true })
const lead = makeAgent({ id: 'l', name: 'Lena', kind: 'lead', manager_agent_id: 'f' })

describe('buildPeopleTree', () => {
  it('without members it is the agents-only tree', () => {
    const tree = buildPeopleTree([atlas, lead], [])
    expect(names(tree)).toEqual(['Atlas'])
    expect(names(tree[0]!.children)).toEqual(['Lena'])
    expect(tree[0]!.member).toBeNull()
  })

  it('places a human under an agent manager and an agent under a human manager', () => {
    const maya = makeMember({ id: 'm1', display_name: 'Maya', manager_ref: 'agent:f' })
    const spec = makeAgent({ id: 's', name: 'Sol', manager_ref: 'member:m1', manager_agent_id: null })
    const tree = buildPeopleTree([atlas, spec], [maya])
    expect(names(tree)).toEqual(['Atlas'])
    const mayaNode = tree[0]!.children[0]!
    expect(label(mayaNode)).toBe('Maya')
    expect(mayaNode.member?.id).toBe('m1')
    expect(mayaNode.id).toBe('member:m1')
    expect(names(mayaNode.children)).toEqual(['Sol'])
    expect(mayaNode.children[0]!.depth).toBe(2)
  })

  it('mixes managers: humans under humans, humans under agents, humans reporting to the Founder', () => {
    const boss = makeMember({ id: 'm1', display_name: 'Boss', manager_ref: null })
    const report = makeMember({ id: 'm2', display_name: 'Report', manager_ref: 'member:m1' })
    const underAgent = makeMember({ id: 'm3', display_name: 'Cara', manager_ref: 'agent:l' })
    const tree = buildPeopleTree([atlas, lead], [boss, report, underAgent])
    expect(names(tree)).toEqual(['Atlas', 'Boss'])
    expect(names(tree[1]!.children)).toEqual(['Report'])
    expect(names(tree[0]!.children[0]!.children)).toEqual(['Cara'])
    expect(tree.every((r) => r.issue === undefined)).toBe(true)
  })

  it('treats the signed-in Founder as the You root, not as a node', () => {
    const self = makeMember({ id: 'ms', user_id: 'me', display_name: 'Founder Sam', role: 'owner' })
    const mine = makeMember({ id: 'm1', display_name: 'Maya', manager_ref: 'member:ms' })
    const robot = makeAgent({ id: 'r', name: 'Robo', manager_ref: 'member:ms', manager_agent_id: null })
    const tree = buildPeopleTree([robot], [self, mine], { selfUserId: 'me' })
    expect(names(tree).sort()).toEqual(['Maya', 'Robo'])
    expect(tree.every((r) => r.issue === undefined)).toBe(true)
    expect(flattenOrgTree(tree).some((n) => n.member?.id === 'ms')).toBe(false)
  })

  it('flags, but keeps, people whose manager is not in the chart', () => {
    const viewer = makeMember({ id: 'v', role: 'viewer', display_name: 'Vee' })
    const orphan = makeMember({ id: 'm1', display_name: 'Orla', manager_ref: 'agent:ghost' })
    const kid = makeAgent({ id: 'k', name: 'Kid', manager_ref: 'member:v', manager_agent_id: null })
    const tree = buildPeopleTree([atlas, kid], [viewer, orphan])
    expect(names(tree)).toEqual(['Atlas', 'Orla', 'Kid'])
    expect(tree.find((n) => label(n) === 'Orla')!.issue).toBe('missing_manager')
    expect(tree.find((n) => label(n) === 'Kid')!.issue).toBe('missing_manager')
    expect(flattenOrgTree(tree).some((n) => n.member?.id === 'v')).toBe(false)
  })

  it('breaks a human/agent reporting cycle and still shows everyone once', () => {
    const a = makeMember({ id: 'm1', display_name: 'Ann', manager_ref: 'agent:x' })
    const x = makeAgent({ id: 'x', name: 'Xan', manager_ref: 'member:m1', manager_agent_id: null })
    const flat = flattenOrgTree(buildPeopleTree([x], [a]))
    expect(flat).toHaveLength(2)
    expect(flat.some((n) => n.issue === 'cycle')).toBe(true)
  })

  it('shows every person exactly once and drops removed members', () => {
    const gone = makeMember({ id: 'g', status: 'removed' })
    const ms = Array.from({ length: 4 }, (_, i) => makeMember({ id: `m${i}`, display_name: `P${i}`, manager_ref: i === 0 ? null : `member:m${i - 1}` }))
    const flat = flattenOrgTree(buildPeopleTree([atlas, lead], [...ms, gone]))
    expect(flat.map((n) => n.id).sort()).toEqual(['f', 'l', 'member:m0', 'member:m1', 'member:m2', 'member:m3'])
  })

  it('sorts humans after the AI Co-founder and before leads, then by name', () => {
    const b = makeMember({ id: 'b', display_name: 'Bea', manager_ref: 'agent:f' })
    const a = makeMember({ id: 'a', display_name: 'Abe', manager_ref: 'agent:f' })
    const tree = buildPeopleTree([atlas, lead], [b, a])
    expect(names(tree[0]!.children)).toEqual(['Abe', 'Bea', 'Lena'])
  })

  it('works with prune and layout (human nodes carry their member)', () => {
    const retired = makeAgent({ id: 'r', name: 'Old', status: 'retired', manager_ref: 'agent:f', manager_agent_id: null })
    const maya = makeMember({ id: 'm1', display_name: 'Maya', manager_ref: 'agent:r' })
    const tree = pruneInactive(buildPeopleTree([atlas, retired], [maya]))
    expect(names(tree[0]!.children)).toEqual(['Maya'])
    const layout = layoutOrg(tree)
    expect(layout.nodes.map((n) => n.id)).toContain(HUMAN_ID)
    expect(layout.nodes.find((n) => n.id === 'member:m1')?.member?.display_name).toBe('Maya')
    expect(layout.nodes.find((n) => n.id === 'member:m1')?.agent).toBeNull()
  })
})
