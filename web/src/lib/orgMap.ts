import type { Task } from '@/api/types'
import { HUMAN_ID } from './orgLayout'
import type { OrgPerson } from './orgTree'
import { isCofounder } from './terms'

/**
 * Organization map (docs/specs/m20 §3): a radial layout. The AI Co-founder sits in the centre with the human Founder attached to
 * it; the people who report to it form the first ring, and their reports the next ring, clustered by manager with angles
 * allocated by subtree size. Everything here is pure: the same input always gives the same picture, and no two circles overlap.
 */

export type MapKind = 'human' | 'cofounder' | 'other'

export interface MapInput {
  id: string
  /** Who this person reports to (the human root is `HUMAN_ID`); null for the human root. A missing parent is tolerated. */
  parentId: string | null
  kind: MapKind
}

export interface MapNode {
  id: string
  kind: MapKind
  /** Reporting parent as given (may be the human). */
  parentId: string | null
  /** Centre of the circle, in px inside the box. */
  x: number
  y: number
  /** Radius in px. */
  r: number
  /** 0 centre (the Co-founder, and the Founder attached to it), 1 first ring, 2 outer ring, ... */
  ring: number
  hasReports: boolean
}

export interface MapEdge {
  id: string
  from: string
  to: string
}

export interface OrgMapLayout {
  nodes: MapNode[]
  edges: MapEdge[]
  width: number
  height: number
}

/** Circle diameters: Co-founder, managers, everyone else, the Founder. Steps down when the company is bigger. */
const SIZE_STEPS: Array<{ max: number; centre: number; manager: number; leaf: number; human: number }> = [
  { max: 20, centre: 56, manager: 44, leaf: 34, human: 30 },
  { max: 32, centre: 48, manager: 38, leaf: 28, human: 26 },
  { max: Infinity, centre: 44, manager: 34, leaf: 26, human: 24 },
]
/** If circles still collide at full size the whole map shrinks, down to this fraction. */
const SCALES = [1, 0.88, 0.76, 0.66, 0.58, 0.5]
/** Names are drawn under circles at least this wide (smaller ones show it in the hover card). */
export const LABEL_MIN_DIAMETER = 36
const LABEL_H = 15
const PAD = 6
const GAP = 3

interface Work {
  id: string
  kind: MapKind
  parentId: string | null
  /** Layout parent (reporting parent, or the centre when that is the human or unknown). */
  lp: string | null
  children: string[]
  depth: number
  weight: number
  angle: number
  x: number
  y: number
  r: number
  pinned: boolean
}

function chooseCentre(inputs: MapInput[]): MapInput | undefined {
  return inputs.find((i) => i.kind === 'cofounder') ?? inputs.find((i) => i.kind === 'human') ?? inputs.find((i) => i.parentId === null) ?? inputs[0]
}

function ellipsePerimeter(a: number, b: number): number {
  // Ramanujan's approximation.
  const h = ((a - b) * (a - b)) / ((a + b) * (a + b) || 1)
  return Math.PI * (a + b) * (1 + (3 * h) / (10 + Math.sqrt(4 - 3 * h)))
}

const round = (n: number): number => Math.round(n * 100) / 100

/**
 * Lay the people out inside a `width` x `height` box. Every input appears exactly once in the output. Deterministic: the order of
 * `inputs` breaks every tie.
 */
export function layoutOrgMap(inputs: MapInput[], size: { width: number; height: number }): OrgMapLayout {
  const { width, height } = size
  const empty: OrgMapLayout = { nodes: [], edges: [], width, height }
  if (inputs.length === 0) return empty
  const centreIn = chooseCentre(inputs)!
  const ids = new Set(inputs.map((i) => i.id))

  // The Founder is attached beside the Co-founder; with no Co-founder the Founder is the centre.
  const attachedId = centreIn.kind === 'cofounder' ? (inputs.find((i) => i.kind === 'human')?.id ?? null) : null

  const work = new Map<string, Work>()
  for (const i of inputs) {
    if (work.has(i.id)) continue
    work.set(i.id, { id: i.id, kind: i.kind, parentId: i.parentId, lp: null, children: [], depth: 0, weight: 1, angle: 0, x: 0, y: 0, r: 0, pinned: false })
  }
  const centreId = centreIn.id

  // Layout parents. Anything whose parent is the human, unknown or itself hangs off the centre; cycles are broken the same way.
  for (const w of work.values()) {
    if (w.id === centreId || w.id === attachedId) continue
    let p = w.parentId
    if (p === null || !ids.has(p) || p === w.id || p === attachedId) p = centreId
    w.lp = p
  }
  const reaches = (start: string): boolean => {
    const seen = new Set<string>()
    let cur: string | null = start
    while (cur !== null && cur !== centreId) {
      if (seen.has(cur)) return false
      seen.add(cur)
      cur = work.get(cur)?.lp ?? null
    }
    return cur === centreId
  }
  for (const w of work.values()) {
    if (w.id === centreId || w.id === attachedId) continue
    if (!reaches(w.id)) w.lp = centreId
  }
  for (const w of work.values()) {
    if (w.lp !== null) work.get(w.lp)!.children.push(w.id)
  }

  // Depth (ring) and subtree weight (leaf count).
  const order: Work[] = []
  const walk = (w: Work, depth: number) => {
    w.depth = depth
    order.push(w)
    for (const c of w.children) walk(work.get(c)!, depth + 1)
    w.weight = w.children.length === 0 ? 1 : w.children.reduce((s, c) => s + work.get(c)!.weight, 0)
  }
  const centre = work.get(centreId)!
  walk(centre, 0)
  const attached = attachedId ? work.get(attachedId)! : null
  if (attached) attached.depth = 0

  // Angles: each subtree owns a sector in proportion to its weight; a node sits in the middle of its sector.
  centre.angle = 0
  const allot = (w: Work, from: number, span: number) => {
    let cursor = from
    for (const cid of w.children) {
      const c = work.get(cid)!
      const part = span * (c.weight / w.weight)
      c.angle = cursor + part / 2
      allot(c, cursor, part)
      cursor += part
    }
  }
  allot(centre, -Math.PI / 2, Math.PI * 2)

  const total = work.size
  const step = SIZE_STEPS.find((s) => total <= s.max)!
  const maxDepth = order.reduce((m, w) => Math.max(m, w.depth), 0)

  let placed = false
  for (const scale of SCALES) {
    const diameter = (w: Work): number => {
      if (w.id === centreId) return step.centre * (centreIn.kind === 'cofounder' ? 1 : 0.9)
      if (w.id === attachedId) return step.human
      return (w.children.length > 0 ? step.manager : step.leaf) * scale
    }
    for (const w of work.values()) {
      w.r = Math.max(8, diameter(w) / 2)
      w.pinned = false
    }
    // Radii of the outermost ring decide how much room the rings have.
    const outerR = Math.max(...[...work.values()].filter((w) => w.depth === maxDepth && w.id !== centreId).map((w) => w.r), centre.r)
    const label = [...work.values()].some((w) => w.depth === maxDepth && w.r * 2 >= LABEL_MIN_DIAMETER) ? LABEL_H : 0
    const left = PAD + outerR
    const top = PAD + outerR
    const right = width - PAD - outerR
    const bottom = height - PAD - outerR - label
    const cx = (left + right) / 2
    const cy = (top + bottom) / 2
    const rxMax = Math.max(10, (right - left) / 2)
    const ryMax = Math.max(10, (bottom - top) / 2)

    centre.x = cx
    centre.y = cy
    centre.pinned = true
    if (attached) {
      const d = centre.r + attached.r + 8
      const a = (-135 * Math.PI) / 180
      attached.x = cx + d * Math.cos(a)
      attached.y = cy + d * Math.sin(a)
      attached.pinned = true
    }

    // Ring fractions: the first ring clears the centre, the last reaches the edge, rings in between are evenly spaced.
    const firstR = centre.r + Math.max(...[...work.values()].filter((w) => w.depth === 1).map((w) => w.r), 0) + 14
    const f1Min = Math.min(1, firstR / Math.max(1, Math.min(rxMax, ryMax)))
    const fraction = (depth: number): number => {
      if (maxDepth <= 1) return Math.max(f1Min, 0.62)
      const f1 = Math.max(f1Min, 0.4)
      return f1 + ((depth - 1) * (1 - f1)) / (maxDepth - 1)
    }
    const perRing = new Map<number, Work[]>()
    for (const w of order) {
      if (w.depth === 0) continue
      const list = perRing.get(w.depth) ?? []
      list.push(w)
      perRing.set(w.depth, list)
    }
    for (const [depth, list] of perRing) {
      const rx = rxMax * fraction(depth)
      const ry = ryMax * fraction(depth)
      const widest = Math.max(...list.map((w) => w.r * 2)) + GAP
      // A crowded ring staggers every other circle onto an inner track instead of squeezing them together.
      const crowded = list.length * widest > ellipsePerimeter(rx, ry)
      list.forEach((w, idx) => {
        const k = crowded && idx % 2 === 1 ? 0.8 : 1
        w.x = cx + rx * k * Math.cos(w.angle)
        w.y = cy + ry * k * Math.sin(w.angle)
      })
    }

    relax([...work.values()], { left: PAD, top: PAD, right: width - PAD, bottom: height - PAD - label })
    placed = !overlaps([...work.values()])
    if (placed) break
  }
  // Even at the smallest scale some pathological shapes (hundreds of people) cannot fit; the last attempt stands.
  void placed

  const nodes: MapNode[] = inputs
    .filter((i, idx) => inputs.findIndex((j) => j.id === i.id) === idx)
    .map((i) => {
      const w = work.get(i.id)!
      return { id: w.id, kind: w.kind, parentId: w.parentId, x: round(w.x), y: round(w.y), r: round(w.r), ring: w.depth, hasReports: w.children.length > 0 }
    })
  // Nodes are listed centre first, then ring by ring, so keyboard order reads centre -> rings.
  nodes.sort((a, b) => a.ring - b.ring || (a.id === attachedId ? 1 : 0) - (b.id === attachedId ? 1 : 0))
  const known = new Set(nodes.map((n) => n.id))
  const edges: MapEdge[] = []
  for (const n of nodes) {
    if (n.id === attachedId) continue
    if (n.id === centreId) {
      // The Founder is joined to the Co-founder they work with.
      if (attachedId) edges.push({ id: `${attachedId}>${centreId}`, from: attachedId, to: centreId })
      continue
    }
    const from = n.parentId && known.has(n.parentId) && n.parentId !== n.id ? n.parentId : work.get(n.id)!.lp
    if (from) edges.push({ id: `${from}>${n.id}`, from, to: n.id })
  }
  return { nodes, edges, width, height }
}

/** Push overlapping circles apart (the centre and the attached Founder stay put) and keep every circle inside the box. */
function relax(items: Work[], box: { left: number; top: number; right: number; bottom: number }): void {
  const clamp = (w: Work) => {
    w.x = Math.min(Math.max(w.x, box.left + w.r), Math.max(box.left + w.r, box.right - w.r))
    w.y = Math.min(Math.max(w.y, box.top + w.r), Math.max(box.top + w.r, box.bottom - w.r))
  }
  items.forEach(clamp)
  for (let iter = 0; iter < 160; iter++) {
    let moved = false
    for (let i = 0; i < items.length; i++) {
      for (let j = i + 1; j < items.length; j++) {
        const a = items[i]!
        const b = items[j]!
        if (a.pinned && b.pinned) continue
        let dx = b.x - a.x
        let dy = b.y - a.y
        let d = Math.hypot(dx, dy)
        const want = a.r + b.r + GAP
        if (d >= want) continue
        if (d < 1e-6) {
          // Exactly stacked: separate along a direction derived from the indices, so the result is deterministic.
          const t = ((i * 7 + j * 13) % 360) * (Math.PI / 180)
          dx = Math.cos(t)
          dy = Math.sin(t)
          d = 1
        }
        const push = want - d
        const ux = dx / d
        const uy = dy / d
        const wa = a.pinned ? 0 : b.pinned ? 1 : 0.5
        const wb = b.pinned ? 0 : a.pinned ? 1 : 0.5
        a.x -= ux * push * wa
        a.y -= uy * push * wa
        b.x += ux * push * wb
        b.y += uy * push * wb
        moved = true
      }
    }
    items.forEach((w) => !w.pinned && clamp(w))
    if (!moved) break
  }
}

function overlaps(items: Work[]): boolean {
  for (let i = 0; i < items.length; i++) {
    for (let j = i + 1; j < items.length; j++) {
      const a = items[i]!
      const b = items[j]!
      if (Math.hypot(a.x - b.x, a.y - b.y) < a.r + b.r - 0.01) return true
    }
  }
  return false
}

/* --------------------------------- collaboration lines --------------------------------- */

export interface CollabLink {
  /** Two ends, ordered so a pair appears once. */
  a: string
  b: string
  kind: 'dependency' | 'meeting'
}

/** At most this many extra lines touch one circle, so a busy company stays readable. */
export const MAX_COLLAB_PER_NODE = 5

/**
 * Lines between agents that depend on each other right now: an in-progress task that depends on another in-progress task
 * owned by someone else. Uses `depends_on` only when the backend reports it; without it there are simply no lines.
 */
export function dependencyLinks(tasks: readonly Task[]): CollabLink[] {
  const active = new Map<string, Task>()
  for (const t of tasks) if (t.status === 'in_progress' && t.assignee_agent_id) active.set(t.id, t)
  const seen = new Set<string>()
  const out: CollabLink[] = []
  for (const t of active.values()) {
    for (const depId of t.depends_on ?? []) {
      const dep = active.get(depId)
      if (!dep || dep.assignee_agent_id === t.assignee_agent_id) continue
      const [a, b] = [t.assignee_agent_id!, dep.assignee_agent_id!].sort()
      const key = `${a}|${b}`
      if (seen.has(key)) continue
      seen.add(key)
      out.push({ a: a!, b: b!, kind: 'dependency' })
    }
  }
  return out.sort((x, y) => (x.a + x.b).localeCompare(y.a + y.b))
}

/** Keep every pair once and at most `max` lines per circle (the first in order win). */
export function capLinks(links: readonly CollabLink[], present: ReadonlySet<string>, max: number = MAX_COLLAB_PER_NODE): CollabLink[] {
  const count = new Map<string, number>()
  const seen = new Set<string>()
  const out: CollabLink[] = []
  for (const l of links) {
    if (l.a === l.b || !present.has(l.a) || !present.has(l.b)) continue
    const key = [l.a, l.b].sort().join('|')
    if (seen.has(key)) continue
    if ((count.get(l.a) ?? 0) >= max || (count.get(l.b) ?? 0) >= max) continue
    seen.add(key)
    count.set(l.a, (count.get(l.a) ?? 0) + 1)
    count.set(l.b, (count.get(l.b) ?? 0) + 1)
    out.push(l)
  }
  return out
}

/**
 * Dependency lines plus the lines of meetings that are running (see `meetingLinksFor` in lib/meetings). Meetings come first so a
 * live meeting is never crowded out by the per-circle cap.
 */
export function collaborationLinks(tasks: readonly Task[], present: ReadonlySet<string>, meetings: readonly CollabLink[] = []): CollabLink[] {
  return capLinks([...meetings, ...dependencyLinks(tasks)], present)
}

/* --------------------------------- from the people tree --------------------------------- */

/** Flatten the reporting tree (roots report to the Founder) into layout inputs. */
export function mapInputsFromTree(roots: readonly OrgPerson[]): MapInput[] {
  const out: MapInput[] = [{ id: HUMAN_ID, parentId: null, kind: 'human' }]
  let cofounderSeen = false
  const walk = (p: OrgPerson, parent: string) => {
    const co = !cofounderSeen && !!p.agent && isCofounder(p.agent)
    if (co) cofounderSeen = true
    out.push({ id: p.id, parentId: parent, kind: co ? 'cofounder' : 'other' })
    p.children.forEach((c) => walk(c, p.id))
  }
  roots.forEach((r) => walk(r, HUMAN_ID))
  return out
}
