import clsx from 'clsx'
import { AlertTriangle, ChevronDown, ChevronRight, ChevronsDownUp, ChevronsUpDown, Scan } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Agent } from '@/api/types'
import { AgentAvatar, Avatar } from '@/components/avatar'
import { HoverCard, HoverCardTrigger, IconButton, Tooltip } from '@/components/ui'
import { RING_STYLE, ringStateFor } from '@/lib/liveState'
import { HUMAN_ID, edgePath, expandableIds, layoutOrg, type LayoutNode } from '@/lib/orgLayout'
import { buildOrgTree, pruneInactive, type OrgIssue } from '@/lib/orgTree'
import { shortRole } from '@/lib/roleFamily'
import { AgentHoverCardContent } from './AgentHoverCard'

const NODE_W = 128
const NODE_H = 120
const GAP_X = 16
const GAP_Y = 40

const ISSUE_TEXT: Record<OrgIssue, string> = {
  no_manager: 'No manager assigned',
  missing_manager: 'Manager is not in this company',
  cycle: 'Part of a reporting cycle',
}

const STATE_TEXT_CLASS: Partial<Record<string, string>> = {
  working: 'text-accent-fg',
  blocked: 'text-warning',
  failed: 'text-danger',
  proposed: 'text-info',
  paused: 'text-fg-3',
  offline: 'text-fg-3',
}

/**
 * The company as a tree of circular avatars: You -> AI Founder -> reporting lines.
 * Layout is computed (lib/orgLayout) and drawn with absolutely positioned nodes over SVG connectors,
 * so it centres when narrow, scrolls when wide, and "Fit" scales it to the available width.
 */
export function OrgTree({
  agents,
  ownerName,
  onOpenAgent,
  className,
  matches,
  showInactive = false,
}: {
  agents: Agent[]
  ownerName?: string
  onOpenAgent: (id: string) => void
  className?: string
  /** Agents failing the predicate are drawn dimmed (filters keep the structure intact). */
  matches?: (a: Agent) => boolean
  /** Keep retired/rejected agents in the chart. */
  showInactive?: boolean
}) {
  const roots = useMemo(() => (showInactive ? buildOrgTree(agents) : pruneInactive(buildOrgTree(agents))), [agents, showInactive])
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())
  const [fit, setFit] = useState(false)
  const scroller = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)

  useEffect(() => {
    const el = scroller.current
    if (!el) return
    setWidth(el.clientWidth)
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => setWidth(el.clientWidth))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const layout = useMemo(
    () => layoutOrg(roots, { collapsed, nodeWidth: NODE_W, nodeHeight: NODE_H, gapX: GAP_X, gapY: GAP_Y, padding: 20 }),
    [roots, collapsed],
  )
  const byId = useMemo(() => new Map(layout.nodes.map((n) => [n.id, n])), [layout])
  const scale = fit && width > 0 ? Math.max(0.4, Math.min(1, width / layout.width)) : 1
  // "Collapse all" keeps the first level of the team visible: top-level roots stay open.
  const collapsible = useMemo(() => {
    const rootIds = new Set(roots.map((r) => r.agent.id))
    return expandableIds(roots).filter((id) => !rootIds.has(id))
  }, [roots])

  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  return (
    <div className={clsx('relative h-full', className)}>
      <div className="absolute top-2 right-2 z-10 flex items-center gap-0.5 rounded-md border border-line bg-surface/95 p-0.5">
        <Tooltip content={fit ? 'Actual size' : 'Fit to width'}>
          <IconButton label={fit ? 'Show actual size' : 'Fit chart to width'} aria-pressed={fit} className={clsx('size-7', fit && 'bg-active text-fg')} onClick={() => setFit((f) => !f)}>
            <Scan className="size-3.5" aria-hidden />
          </IconButton>
        </Tooltip>
        <Tooltip content="Collapse all">
          <IconButton label="Collapse all teams" className="size-7" disabled={collapsible.length === 0} onClick={() => setCollapsed(new Set(collapsible))}>
            <ChevronsDownUp className="size-3.5" aria-hidden />
          </IconButton>
        </Tooltip>
        <Tooltip content="Expand all">
          <IconButton label="Expand all teams" className="size-7" disabled={collapsed.size === 0} onClick={() => setCollapsed(new Set())}>
            <ChevronsUpDown className="size-3.5" aria-hidden />
          </IconButton>
        </Tooltip>
      </div>

      <div ref={scroller} className="h-full overflow-auto" role="tree" aria-label="Organization chart">
        <div style={{ width: layout.width * scale, height: layout.height * scale, margin: '0 auto' }}>
          <div style={{ width: layout.width, height: layout.height, transform: `scale(${scale})`, transformOrigin: '0 0', position: 'relative' }}>
            <svg width={layout.width} height={layout.height} className="pointer-events-none absolute inset-0" aria-hidden>
              {layout.edges.map((e) => {
                const p = byId.get(e.from)
                const c = byId.get(e.to)
                return p && c ? <path key={e.id} className="cos-edge" d={edgePath(p, c, NODE_H, GAP_Y)} /> : null
              })}
            </svg>
            {layout.nodes.map((n) =>
              n.agent ? (
                <AgentNode key={n.id} node={n} dim={matches ? !matches(n.agent!) : false} onOpen={() => onOpenAgent(n.id)} onToggle={() => toggle(n.id)} />
              ) : (
                <HumanNode key={HUMAN_ID} node={n} ownerName={ownerName} />
              ),
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

const slot = (n: LayoutNode) => ({ position: 'absolute' as const, left: n.x - NODE_W / 2, top: n.y, width: NODE_W, height: NODE_H })

function HumanNode({ node, ownerName }: { node: LayoutNode; ownerName?: string }) {
  return (
    <div role="treeitem" aria-level={1} aria-selected={false} style={slot(node)} className="flex flex-col items-center px-1">
      <Avatar family="human" size="xl" />
      <span className="mt-1.5 max-w-full truncate text-[12.5px] font-semibold text-fg">You</span>
      <span className="max-w-full truncate text-[11.5px] text-fg-3">{ownerName ? `Co-founder · ${ownerName}` : 'Human co-founder'}</span>
    </div>
  )
}

function AgentNode({ node, dim, onOpen, onToggle }: { node: LayoutNode; dim: boolean; onOpen: () => void; onToggle: () => void }) {
  const agent = node.agent!
  const ring = ringStateFor(agent)
  const stateWord = ring === 'idle' ? null : RING_STYLE[ring].label
  const label = [agent.name, agent.role_title, RING_STYLE[ring].label, agent.live?.summary].filter(Boolean).join(', ')
  return (
    <div role="treeitem" aria-level={node.depth} aria-selected={false} aria-expanded={node.childCount > 0 ? !node.collapsed : undefined} style={slot(node)} className={dim ? 'opacity-35' : undefined}>
      <HoverCard openDelay={120} closeDelay={80}>
        <HoverCardTrigger asChild>
          <button
            type="button"
            aria-label={label}
            onClick={onOpen}
            className="group flex h-full w-full flex-col items-center rounded-md px-1 pt-0.5 text-center transition-colors hover:bg-hover/60 focus-visible:bg-hover/60"
          >
            <AgentAvatar agent={agent} size="xl" />
            <span className="mt-1.5 flex max-w-full items-center gap-1 text-[12.5px] leading-4 font-semibold text-fg">
              <span className="truncate">{agent.name}</span>
              {node.issue && <AlertTriangle className="size-3 shrink-0 text-warning" aria-label={ISSUE_TEXT[node.issue]} />}
            </span>
            <span className="max-w-full truncate text-[11.5px] leading-4 text-fg-3">{shortRole(agent.role_title, 20)}</span>
            {stateWord && <span className={clsx('text-[10.5px] leading-4 font-medium tracking-wide uppercase', STATE_TEXT_CLASS[ring])}>{stateWord}</span>}
          </button>
        </HoverCardTrigger>
        <AgentHoverCardContent agent={agent} />
      </HoverCard>
      {node.childCount > 0 && (
        <button
          type="button"
          onClick={onToggle}
          aria-label={node.collapsed ? `Expand ${agent.name}'s team (${node.hiddenCount} hidden)` : `Collapse ${agent.name}'s team`}
          aria-expanded={!node.collapsed}
          className="absolute left-1/2 z-[1] flex h-[18px] min-w-[18px] -translate-x-1/2 items-center justify-center gap-0.5 rounded-full border border-line-strong bg-surface px-1 text-[10.5px] font-medium text-fg-2 transition-colors hover:bg-hover hover:text-fg"
          style={{ top: NODE_H - 9 }}
        >
          {node.collapsed ? (
            <>
              <ChevronRight className="size-2.5" aria-hidden />+{node.hiddenCount}
            </>
          ) : (
            <ChevronDown className="size-2.5" aria-hidden />
          )}
        </button>
      )}
    </div>
  )
}
