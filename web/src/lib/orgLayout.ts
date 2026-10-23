import type { Agent, Member } from '@/api/types'
import type { OrgIssue, OrgPerson } from './orgTree'

/** Id of the virtual root that represents the Founder, the human ("You"). */
export const HUMAN_ID = '__human__'

export interface LayoutNode {
  id: string
  /** null for the human root and for human members. */
  agent: Agent | null
  /** M7: set for a human member of the company (an agent node has `agent`; the You root has neither). */
  member?: Member | null
  parentId: string | null
  depth: number
  /** Centre x and top y, in px, in tree coordinates (before any scale). */
  x: number
  y: number
  issue?: OrgIssue
  /** Direct reports in the data (even if hidden by a collapse). */
  childCount: number
  /** All descendants hidden because this node is collapsed (0 when expanded). */
  hiddenCount: number
  collapsed: boolean
}

export interface LayoutEdge {
  id: string
  from: string
  to: string
}

export interface OrgLayout {
  nodes: LayoutNode[]
  edges: LayoutEdge[]
  width: number
  height: number
  nodeWidth: number
  nodeHeight: number
}

export interface LayoutOptions {
  /** Ids of nodes whose subtree is collapsed. */
  collapsed?: ReadonlySet<string>
  nodeWidth?: number
  nodeHeight?: number
  gapX?: number
  gapY?: number
  padding?: number
  /** Add the "You" root above the reporting roots. Default true. */
  withHuman?: boolean
}

interface Prepared {
  id: string
  agent: Agent | null
  member: Member | null
  issue?: OrgIssue
  children: Prepared[]
  childCount: number
  hiddenCount: number
  collapsed: boolean
  /** subtree width */
  w: number
}

function countDescendants(n: OrgPerson): number {
  let c = 0
  for (const k of n.children) c += 1 + countDescendants(k)
  return c
}

/**
 * Pure tidy-tree layout: leaves get one slot, a parent is centred over its first and last child, and
 * subtrees never overlap. Collapsed nodes are drawn as leaves and report how many people they hide.
 * Input is the already-sanitised tree from `buildOrgTree` (cycles/missing managers become flagged
 * roots there), so every agent appears exactly once.
 */
export function layoutOrg(roots: OrgPerson[], opts: LayoutOptions = {}): OrgLayout {
  const {
    collapsed = new Set<string>(),
    nodeWidth = 124,
    nodeHeight = 112,
    gapX = 20,
    gapY = 44,
    padding = 24,
    withHuman = true,
  } = opts

  const prepare = (n: OrgPerson): Prepared => {
    const isCollapsed = collapsed.has(n.id) && n.children.length > 0
    const kids = isCollapsed ? [] : n.children.map(prepare)
    return {
      id: n.id,
      agent: n.agent,
      member: n.member ?? null,
      issue: n.issue,
      children: kids,
      childCount: n.children.length,
      hiddenCount: isCollapsed ? countDescendants(n) : 0,
      collapsed: isCollapsed,
      w: 0,
    }
  }

  const measure = (p: Prepared): number => {
    if (p.children.length === 0) {
      p.w = nodeWidth
      return p.w
    }
    const total = p.children.reduce((s, c) => s + measure(c), 0) + gapX * (p.children.length - 1)
    p.w = Math.max(nodeWidth, total)
    return p.w
  }

  const prepared = roots.map(prepare)
  let forest: Prepared[]
  if (withHuman) {
    const human: Prepared = {
      id: HUMAN_ID,
      agent: null,
      member: null,
      children: prepared,
      childCount: prepared.length,
      hiddenCount: 0,
      collapsed: false,
      w: 0,
    }
    forest = [human]
  } else {
    forest = prepared
  }
  forest.forEach(measure)

  const nodes: LayoutNode[] = []
  const edges: LayoutEdge[] = []
  let maxDepth = 0

  const place = (p: Prepared, left: number, depth: number, parentId: string | null) => {
    maxDepth = Math.max(maxDepth, depth)
    let x: number
    if (p.children.length === 0) {
      x = left + p.w / 2
    } else {
      const total = p.children.reduce((s, c) => s + c.w, 0) + gapX * (p.children.length - 1)
      let cursor = left + (p.w - total) / 2
      const centres: number[] = []
      for (const c of p.children) {
        place(c, cursor, depth + 1, p.id)
        centres.push(cursor + c.w / 2)
        cursor += c.w + gapX
      }
      x = (centres[0]! + centres[centres.length - 1]!) / 2
    }
    nodes.push({
      id: p.id,
      agent: p.agent,
      member: p.member,
      parentId,
      depth,
      x,
      y: depth * (nodeHeight + gapY),
      issue: p.issue,
      childCount: p.childCount,
      hiddenCount: p.hiddenCount,
      collapsed: p.collapsed,
    })
    if (parentId) edges.push({ id: `${parentId}->${p.id}`, from: parentId, to: p.id })
  }

  let cursor = 0
  forest.forEach((p, i) => {
    place(p, cursor, 0, null)
    cursor += p.w + (i < forest.length - 1 ? gapX : 0)
  })

  const contentWidth = Math.max(cursor, nodeWidth)
  const contentHeight = nodes.length === 0 ? 0 : (maxDepth + 1) * nodeHeight + maxDepth * gapY
  for (const n of nodes) {
    n.x += padding
    n.y += padding
  }
  nodes.sort((a, b) => a.depth - b.depth || a.x - b.x)

  return {
    nodes,
    edges,
    width: contentWidth + padding * 2,
    height: contentHeight + padding * 2,
    nodeWidth,
    nodeHeight,
  }
}

/** Elbow connector from the bottom-centre of the parent to the top-centre of the child. */
export function edgePath(parent: LayoutNode, child: LayoutNode, nodeHeight: number, gapY: number, radius = 8): string {
  const x1 = parent.x
  const y1 = parent.y + nodeHeight
  const x2 = child.x
  const y2 = child.y
  const midY = y1 + gapY / 2
  if (Math.abs(x2 - x1) < 0.5) return `M ${x1} ${y1} V ${y2}`
  const dir = x2 > x1 ? 1 : -1
  const r = Math.min(radius, Math.abs(x2 - x1) / 2, gapY / 2)
  return [
    `M ${x1} ${y1}`,
    `V ${midY - r}`,
    `Q ${x1} ${midY} ${x1 + dir * r} ${midY}`,
    `H ${x2 - dir * r}`,
    `Q ${x2} ${midY} ${x2} ${midY + r}`,
    `V ${y2}`,
  ].join(' ')
}

/** Ids of every node that has reports (used for "collapse all"). */
export function expandableIds(roots: OrgPerson[]): string[] {
  const out: string[] = []
  const walk = (n: OrgPerson) => {
    if (n.children.length > 0) out.push(n.id)
    n.children.forEach(walk)
  }
  roots.forEach(walk)
  return out
}
