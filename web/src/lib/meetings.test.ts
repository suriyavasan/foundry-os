import { describe, expect, it } from 'vitest'
import { makeMeeting, participant } from '@/test/m21'
import { actionItemNote, isMeetingOpen, isPassMessage, meetingLine, meetingLinksFor, orderedParticipants, readOutcome, readProposals, roundLabel } from './meetings'

describe('meetingLine / roundLabel', () => {
  it('reads like the live-activity line, organizer first', () => {
    const m = makeMeeting({ participants: [participant({ agent_id: 'b', name: 'Raj', position: 2 }), participant({ agent_id: 'a', name: 'Ana', role: 'organizer', position: 0 }), participant({ agent_id: 'c', name: 'Lee', position: 1 })] })
    expect(meetingLine(m)).toBe('Meeting · Ana, Lee, Raj · Plan the launch · round 2/2')
  })
  it('never shows round 0 or a round past the last', () => {
    expect(roundLabel({ round: 0, max_rounds: 2 })).toBe('round 1/2')
    expect(roundLabel({ round: 5, max_rounds: 3 })).toBe('round 3/3')
  })
  it('puts the organizer ahead of a participant at the same position', () => {
    const ps = [participant({ agent_id: 'b', name: 'B', position: 0 }), participant({ agent_id: 'a', name: 'A', role: 'organizer', position: 0 })]
    expect(orderedParticipants(ps).map((p) => p.name)).toEqual(['A', 'B'])
  })
})

describe('isMeetingOpen', () => {
  it('is true until the meeting ends', () => {
    expect(['scheduled', 'running'].every((status) => isMeetingOpen({ status } as never))).toBe(true)
    expect(['concluded', 'cancelled', 'failed'].some((status) => isMeetingOpen({ status } as never))).toBe(false)
  })
})

describe('meetingLinksFor', () => {
  const ps = (...ids: string[]) => ids.map((id, i) => participant({ agent_id: id, name: id, position: i }))
  it('links every pair in a running meeting, once, in a stable order', () => {
    const links = meetingLinksFor([makeMeeting({ participants: ps('c', 'a', 'b') })])
    expect(links).toEqual([
      { a: 'a', b: 'b', kind: 'meeting' },
      { a: 'a', b: 'c', kind: 'meeting' },
      { a: 'b', b: 'c', kind: 'meeting' },
    ])
  })
  it('ignores meetings that are not running and de-duplicates pairs across meetings', () => {
    const links = meetingLinksFor([makeMeeting({ status: 'concluded', participants: ps('x', 'y') }), makeMeeting({ participants: ps('a', 'b') }), makeMeeting({ id: 'm2', participants: ps('b', 'a') })])
    expect(links).toEqual([{ a: 'a', b: 'b', kind: 'meeting' }])
  })
})

describe('readOutcome', () => {
  it('reads decisions and how each action item ended', () => {
    const o = readOutcome({
      decisions: [{ title: 'Launch on the 14th', rationale: 'Fits the press cycle' }, { title: '' }, 'junk'],
      action_items: [
        { id: 'i1', title: 'Write the post', owner_agent_id: 'a2', accepted: true, task_id: 't9' },
        { id: 'i2', title: 'Book a venue', owner_agent_id: 'f1', accepted: false },
        { id: 'i3', title: 'Buy ads', owner_agent_id: 'a2', accepted: false, refused: 'the open-task cap was reached' },
        { id: 'i4', title: 'Draft FAQ', owner_agent_id: 'a2', accepted: true },
        { title: '' },
      ],
    })
    expect(o.decisions).toEqual([{ title: 'Launch on the 14th', rationale: 'Fits the press cycle' }])
    expect(o.actionItems.map((i) => [i.title, i.state, actionItemNote(i)])).toEqual([
      ['Write the post', 'task', 'Task created'],
      ['Book a venue', 'not_agreed', 'Not agreed'],
      ['Buy ads', 'refused', 'No task: the open-task cap was reached'],
      ['Draft FAQ', 'agreed', 'Agreed'],
    ])
    expect(o.actionItems[0]!.taskId).toBe('t9')
  })
  it('degrades to nothing on an empty or odd outcome', () => {
    expect(readOutcome({})).toEqual({ decisions: [], actionItems: [] })
    expect(readOutcome(undefined)).toEqual({ decisions: [], actionItems: [] })
  })
})

describe('message proposals', () => {
  it('reads proposals and counts accept/decline', () => {
    expect(readProposals({ proposed: [{ id: 'p1', title: 'Do X', owner_agent_id: 'a2' }], accepted: ['p0'], declined: [] })).toEqual({ proposed: [{ id: 'p1', title: 'Do X', ownerAgentId: 'a2' }], accepted: 1, declined: 0 })
    expect(readProposals(null)).toEqual({ proposed: [], accepted: 0, declined: 0 })
  })
  it('recognises a pass', () => {
    expect(isPassMessage({ pass: true, auto: true })).toBe(true)
    expect(isPassMessage({})).toBe(false)
  })
})
