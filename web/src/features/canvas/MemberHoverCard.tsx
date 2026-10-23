import { ArrowUpRight } from 'lucide-react'
import { useAllTasks } from '@/api'
import type { Agent, Member } from '@/api/types'
import { MemberAvatar } from '@/components/avatar'
import { Badge, HoverCardContent, MetaRow, SectionLabel } from '@/components/ui'
import { TaskStatusBadge } from '@/features/shared/badges'
import { openTasksOfMember, parsePersonRef, roleLabel } from '@/lib/people'
import { useCanvas } from './CanvasContext'

const MAX_TASKS = 5

/** Who a manager reference points at, in words. */
export function managerName(ref: string | null | undefined, agents: readonly Agent[], members: readonly Member[]): string | null {
  const r = parsePersonRef(ref)
  if (!r) return null
  if (r.type === 'agent') return agents.find((a) => a.id === r.id)?.name ?? null
  return members.find((m) => m.id === r.id)?.display_name ?? null
}

/** A human colleague at a glance: role, manager, and the open tasks assigned to them. */
export function MemberHoverCardContent({ member, managerLabel }: { member: Member; managerLabel: string | null }) {
  const { companyId, openPanel } = useCanvas()
  const tasks = useAllTasks(companyId)
  const open = tasks.data ? openTasksOfMember(tasks.data, member.id) : []

  return (
    <HoverCardContent align="center" side="bottom">
      <div className="flex items-start gap-3">
        <MemberAvatar name={member.display_name} size="lg" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[14px] font-semibold text-fg">{member.display_name}</div>
          <div className="truncate text-[12px] text-fg-3">{member.title ?? `@${member.username}`}</div>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <Badge tone="accent">Human</Badge>
            <Badge tone="neutral">{roleLabel(member.role)}</Badge>
          </div>
        </div>
      </div>

      <div className="mt-3 space-y-1">
        <MetaRow label="Reports to">{managerLabel ?? 'You'}</MetaRow>
        <MetaRow label="Username">@{member.username}</MetaRow>
      </div>

      <div className="mt-3 rounded-md border border-line bg-raised px-2.5 py-2">
        <SectionLabel>Open tasks</SectionLabel>
        {tasks.isPending ? (
          <p className="mt-1 text-[12.5px] text-fg-3">Loading…</p>
        ) : tasks.isError ? (
          <p className="mt-1 text-[12.5px] text-fg-3">Tasks are not available from this backend yet.</p>
        ) : open.length === 0 ? (
          <p className="mt-1 text-[12.5px] text-fg-2">Nothing assigned right now.</p>
        ) : (
          <ul className="mt-1 space-y-1.5">
            {open.slice(0, MAX_TASKS).map((t) => (
              <li key={t.id} className="flex items-center gap-2">
                <button type="button" onClick={() => openPanel({ kind: 'task', id: t.id })} className="min-w-0 flex-1 truncate text-left text-[12.5px] font-medium text-accent-fg hover:underline">
                  {t.title}
                </button>
                <TaskStatusBadge status={t.status} />
              </li>
            ))}
            {open.length > MAX_TASKS && <li className="text-[11.5px] text-fg-3">and {open.length - MAX_TASKS} more</li>}
          </ul>
        )}
      </div>

      <button
        type="button"
        onClick={() => openPanel({ kind: 'team' })}
        className="mt-3 inline-flex w-full items-center justify-between gap-1 rounded-md border border-line-strong bg-raised px-2.5 py-1.5 text-[12.5px] font-medium text-fg hover:bg-hover"
      >
        Open team
        <ArrowUpRight className="size-3.5 shrink-0" aria-hidden />
      </button>
    </HoverCardContent>
  )
}
