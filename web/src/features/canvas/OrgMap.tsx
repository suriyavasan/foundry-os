import clsx from 'clsx'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useAllTasks, useRunningMeetings } from '@/api'
import type { Agent, Member } from '@/api/types'
import { AgentAvatar, Avatar, MemberAvatar } from '@/components/avatar'
import { HoverCard, HoverCardTrigger, Tooltip } from '@/components/ui'
import { RING_STYLE, ringStateFor } from '@/lib/liveState'
import { HUMAN_ID } from '@/lib/orgLayout'
import { meetingLinksFor } from '@/lib/meetings'
import { LABEL_MIN_DIAMETER, collaborationLinks, layoutOrgMap, mapInputsFromTree, type MapNode } from '@/lib/orgMap'
import { OrgIssueText } from '@/lib/orgIssue'
import { buildPeopleTree } from '@/lib/orgPeople'
import { buildOrgTree, pruneInactive, type OrgIssue, type OrgPerson } from '@/lib/orgTree'
import { openTasksOfMember, roleLabel } from '@/lib/people'
import { YOU_FOUNDER_LABEL, displayRole } from '@/lib/terms'
import { AgentHoverCardContent } from './AgentHoverCard'
import { useCanvas } from './CanvasContext'
import { MemberHoverCardContent, managerName } from './MemberHoverCard'

/** What the map shows when its box has not been measured (first paint, tests): the smallest box it is designed for. */
const FALLBACK = { width: 360, height: 420 }

/** The people to draw: the reporting tree (with members when the backend has them), minus retired agents. */
export function useOrgRoots(agents: Agent[], members: Member[] | undefined, selfUserId: string | null | undefined): OrgPerson[] {
  return useMemo(() => pruneInactive(members ? buildPeopleTree(agents, members, { selfUserId }) : buildOrgTree(agents)), [agents, members, selfUserId])
}

function useBox<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [box, setBox] = useState(FALLBACK)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const read = () => {
      const w = el.clientWidth
      const h = el.clientHeight
      if (w > 0 && h > 0) setBox((b) => (b.width === w && b.height === h ? b : { width: w, height: h }))
    }
    read()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(read)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, box] as const
}

/**
 * The company as a map: the AI Co-founder in the middle with the Founder beside it, the people who report to it around them, and
 * their reports on an outer ring. Circles are people (illustrated faces, live-state rings); thin lines are reporting lines; dashed
 * accent lines join agents that depend on each other's in-progress work. Fits its box, no scrolling.
 */
export function OrgMap({
  roots,
  agents,
  members,
  ownerName,
  selfUserId,
  onOpenAgent,
  onOpenMember,
}: {
  roots: OrgPerson[]
  agents: Agent[]
  members?: Member[]
  ownerName?: string
  selfUserId?: string | null
  onOpenAgent: (id: string) => void
  onOpenMember?: (memberId: string) => void
}) {
  const { companyId } = useCanvas()
  const [ref, box] = useBox<HTMLDivElement>()
  const tasks = useAllTasks(companyId)
  const layout = useMemo(() => layoutOrgMap(mapInputsFromTree(roots), box), [roots, box])
  const people = useMemo(() => {
    const m = new Map<string, OrgPerson>()
    const walk = (p: OrgPerson) => {
      m.set(p.id, p)
      p.children.forEach(walk)
    }
    roots.forEach(walk)
    return m
  }, [roots])
  const byId = useMemo(() => new Map(layout.nodes.map((n) => [n.id, n])), [layout])
  // M21: a backend without meetings (or an error) simply yields no meeting lines.
  const meetings = useRunningMeetings(companyId)
  const links = useMemo(() => collaborationLinks(tasks.data ?? [], new Set(byId.keys()), meetingLinksFor(meetings.data ?? [])), [tasks.data, meetings.data, byId])
  const agentById = useMemo(() => new Map(agents.map((a) => [a.id, a])), [agents])
  const selfMember = members?.find((m) => m.user_id === selfUserId)

  return (
    <div ref={ref} role="group" aria-label="Organization map" className="relative h-full w-full overflow-hidden">
      <svg width={layout.width} height={layout.height} className="pointer-events-none absolute inset-0" aria-hidden>
        {layout.edges.map((e) => {
          const a = byId.get(e.from)
          const b = byId.get(e.to)
          if (!a || !b) return null
          return <line key={e.id} className="cos-edge" x1={a.x} y1={a.y} x2={b.x} y2={b.y} />
        })}
        {links.map((l) => {
          const a = byId.get(l.a)
          const b = byId.get(l.b)
          if (!a || !b) return null
          const live = l.kind === 'meeting' || (ringStateFor(agentById.get(l.a) ?? { status: 'active' }) === 'working' && ringStateFor(agentById.get(l.b) ?? { status: 'active' }) === 'working')
          // A slight curve keeps a collaboration line distinct from the straight reporting lines.
          const mx = (a.x + b.x) / 2
          const my = (a.y + b.y) / 2
          const nx = -(b.y - a.y) * 0.18
          const ny = (b.x - a.x) * 0.18
          return <path key={`${l.a}~${l.b}`} className="cos-collab" data-kind={l.kind} data-live={live || undefined} d={`M${a.x} ${a.y} Q${mx + nx} ${my + ny} ${b.x} ${b.y}`} />
        })}
      </svg>
      {layout.nodes.map((n) => {
        const person = people.get(n.id)
        const style = { position: 'absolute' as const, left: n.x - n.r, top: n.y - n.r, width: n.r * 2, height: n.r * 2 }
        if (n.id === HUMAN_ID) {
          const seed = selfMember?.id ?? selfUserId ?? 'founder'
          return (
            <div key={n.id} style={style} data-ring={n.ring} className="cos-msg-in">
              <Tooltip content={ownerName ? `${YOU_FOUNDER_LABEL} · ${ownerName}` : YOU_FOUNDER_LABEL}>
                <span role="img" aria-label={ownerName ? `${YOU_FOUNDER_LABEL}, ${ownerName}` : YOU_FOUNDER_LABEL} tabIndex={0} className="block rounded-full">
                  <Avatar family="human" size={n.r * 2} seed={seed} />
                </span>
              </Tooltip>
            </div>
          )
        }
        if (person?.agent) return <AgentCircle key={n.id} node={n} style={style} agent={person.agent} issue={person.issue} onOpen={() => onOpenAgent(n.id)} />
        if (person?.member)
          return (
            <MemberCircle
              key={n.id}
              node={n}
              style={style}
              member={person.member}
              issue={person.issue}
              managerLabel={managerName(person.member.manager_ref, agents, members ?? [])}
              onOpen={() => onOpenMember?.(person.member!.id)}
            />
          )
        return null
      })}
    </div>
  )
}

const ringClass = 'rounded-full outline-offset-2 focus-visible:outline-2 focus-visible:outline-accent'

/** The name under a circle wide enough to carry one; smaller circles show it in their hover card. */
function NameLabel({ node, name }: { node: MapNode; name: string }) {
  if (node.r * 2 < LABEL_MIN_DIAMETER) return null
  return (
    <span aria-hidden className="pointer-events-none absolute top-full left-1/2 mt-0.5 max-w-[92px] -translate-x-1/2 truncate text-[11px] leading-[14px] font-medium text-fg-2 [text-shadow:0_0_3px_var(--surface),0_0_3px_var(--surface)]">
      {name}
    </span>
  )
}

function AgentCircle({ node, style, agent, issue, onOpen }: { node: MapNode; style: React.CSSProperties; agent: Agent; issue?: OrgIssue; onOpen: () => void }) {
  const ring = ringStateFor(agent)
  const label = [agent.name, displayRole(agent), RING_STYLE[ring].label, agent.live?.summary, issue ? OrgIssueText[issue] : null].filter(Boolean).join(', ')
  return (
    <div style={style} data-ring={node.ring} className="cos-msg-in">
      <HoverCard openDelay={120} closeDelay={80}>
        <HoverCardTrigger asChild>
          <button type="button" aria-label={label} onClick={onOpen} className={clsx('block size-full', ringClass)}>
            <AgentAvatar agent={agent} size={node.r * 2} />
          </button>
        </HoverCardTrigger>
        <AgentHoverCardContent agent={agent} />
      </HoverCard>
      <NameLabel node={node} name={agent.name} />
    </div>
  )
}

function MemberCircle({ node, style, member, issue, managerLabel, onOpen }: { node: MapNode; style: React.CSSProperties; member: Member; issue?: OrgIssue; managerLabel: string | null; onOpen: () => void }) {
  const { companyId } = useCanvas()
  const tasks = useAllTasks(companyId)
  const open = tasks.data ? openTasksOfMember(tasks.data, member.id).length : 0
  const role = member.title ?? roleLabel(member.role)
  const label = [member.display_name, role, 'Human', open > 0 ? `${open} open task${open === 1 ? '' : 's'}` : null, issue ? OrgIssueText[issue] : null].filter(Boolean).join(', ')
  return (
    <div style={style} data-ring={node.ring} className="cos-msg-in">
      <HoverCard openDelay={120} closeDelay={80}>
        <HoverCardTrigger asChild>
          <button type="button" aria-label={label} onClick={onOpen} className={clsx('block size-full', ringClass)}>
            <MemberAvatar name={member.display_name} seed={member.id} size={node.r * 2} />
          </button>
        </HoverCardTrigger>
        <MemberHoverCardContent member={member} managerLabel={managerLabel} />
      </HoverCard>
      <NameLabel node={node} name={member.display_name} />
    </div>
  )
}
