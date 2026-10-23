import { useMemo } from 'react'
import { isNotDeployed, useActivity, useRoleTemplates, useSkills, useTasks } from '@/api'
import { Avatar } from '@/components/avatar'
import { Badge, Boundary, Button, SectionLabel, Widget } from '@/components/ui'
import { formatRelative } from '@/lib/format'
import { ROLE_FAMILY_LABEL } from '@/lib/roleFamily'
import { collapseHrActivity, openHiringRequests, skillsByFamily, summarizeLibrary } from '@/lib/skills'
import { useCanvas } from '../CanvasContext'
import { useTick } from '../hooks'

/** Skills & HR at a glance: library health, who is being hired, and what HR did recently. */
export function SkillsWidget({ className }: { className?: string }) {
  const { companyId, openPanel } = useCanvas()
  const skills = useSkills(companyId)
  const templates = useRoleTemplates(companyId)
  const hiring = useTasks(companyId, { kind: 'hiring' })
  const activity = useActivity(companyId, {}, 100)
  useTick(30_000)

  const summary = useMemo(() => summarizeLibrary(skills.data ?? []), [skills.data])
  const families = useMemo(() => skillsByFamily(skills.data ?? [], templates.data ?? []), [skills.data, templates.data])
  const requests = useMemo(() => openHiringRequests(hiring.data ?? []), [hiring.data])
  const recent = useMemo(() => collapseHrActivity((activity.data?.pages ?? []).flatMap((p) => p.items)), [activity.data])

  return (
    <Widget title="Skills" subtitle="Library & HR" onExpand={() => openPanel({ kind: 'skills' })} expandLabel="Open skills" className={className}>
      <Boundary
        query={skills}
        milestone="M4"
        unavailableText="The skill library and the HR agent arrive with Skills & HR."
        isEmpty={(d) => d.length === 0}
        empty={{
          title: 'No skills yet',
          description: 'The built-in library is imported by the background worker.',
          action: (
            <Button size="sm" onClick={() => openPanel({ kind: 'skills' })}>
              Open skills
            </Button>
          ),
        }}
      >
        {() => (
          <div className="divide-y divide-line">
            <div className="grid grid-cols-3 gap-2 px-3.5 py-3">
              <Count label="Active" value={summary.active} />
              <Count label="Quarantined" value={summary.quarantined} tone={summary.quarantined > 0 ? 'danger' : undefined} />
              <Count label="Need review" value={Math.max(0, summary.flagged - summary.quarantined)} tone={summary.flagged - summary.quarantined > 0 ? 'warning' : undefined} />
            </div>
            {summary.quarantined > 0 && (
              <div role="status" className="flex items-center justify-between gap-2 bg-danger-subtle px-3.5 py-2 text-[12.5px] text-fg">
                <span>
                  {summary.quarantined} skill{summary.quarantined === 1 ? ' is' : 's are'} quarantined and unusable until reviewed.
                </span>
                <Button size="sm" onClick={() => openPanel({ kind: 'skills' })}>
                  Review
                </Button>
              </div>
            )}

            <section aria-label="Open hiring requests" className="px-3.5 py-2.5">
              <div className="flex items-center justify-between">
                <SectionLabel>Open hiring requests</SectionLabel>
                {hiring.isSuccess && <Badge tone={requests.length ? 'info' : 'neutral'}>{requests.length}</Badge>}
              </div>
              {hiring.isPending ? (
                <p className="mt-1 text-[12.5px] text-fg-3">Loading…</p>
              ) : hiring.isError ? (
                <p className="mt-1 text-[12.5px] text-fg-3">{isNotDeployed(hiring.error) ? 'Hiring requests arrive with the work engine.' : 'Could not load hiring requests.'}</p>
              ) : requests.length === 0 ? (
                <p className="mt-1 text-[12.5px] text-fg-3">None. HR is not waiting on any hire.</p>
              ) : (
                <ul className="mt-1 space-y-0.5">
                  {requests.slice(0, 3).map((t) => (
                    <li key={t.id}>
                      <button type="button" className="flex w-full items-baseline justify-between gap-2 py-0.5 text-left text-[12.5px] hover:underline" onClick={() => openPanel({ kind: 'task', id: t.id })}>
                        <span className="truncate text-fg">{t.title}</span>
                        <span className="shrink-0 text-[11.5px] text-fg-3">{formatRelative(t.created_at)}</span>
                      </button>
                    </li>
                  ))}
                  {requests.length > 3 && <li className="text-[11.5px] text-fg-3">and {requests.length - 3} more</li>}
                </ul>
              )}
            </section>

            <section aria-label="Skills by role family" className="px-3.5 py-2.5">
              <SectionLabel>By role family</SectionLabel>
              {templates.isSuccess && families.length > 0 ? (
                <ul className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-1">
                  {families.map(({ family, count }) => (
                    <li key={family} className="flex items-center gap-1.5 text-[12.5px]" title={`${count} active skills ship with the ${ROLE_FAMILY_LABEL[family]} role templates`}>
                      <Avatar family={family} size="xs" />
                      <span className="min-w-0 flex-1 truncate text-fg-2">{ROLE_FAMILY_LABEL[family]}</span>
                      <span className="text-fg tabular-nums">{count}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-1 text-[12.5px] text-fg-3">{templates.isPending ? 'Loading…' : 'Role templates did not report any skills.'}</p>
              )}
            </section>

            <section aria-label="Recent HR activity" className="px-3.5 py-2.5">
              <SectionLabel>Recent HR activity</SectionLabel>
              {activity.isPending ? (
                <p className="mt-1 text-[12.5px] text-fg-3">Loading…</p>
              ) : activity.isError ? (
                <p className="mt-1 text-[12.5px] text-fg-3">Could not load activity.</p>
              ) : recent.length === 0 ? (
                <p className="mt-1 text-[12.5px] text-fg-3">No hires or skill installs yet.</p>
              ) : (
                <ul className="mt-1 space-y-1">
                  {recent.map((e) => (
                    <li key={e.key} className="flex items-baseline justify-between gap-2 text-[12.5px]">
                      <span className={`min-w-0 truncate ${e.tone === 'danger' ? 'text-danger' : 'text-fg-2'}`} title={e.text}>
                        {e.text}
                      </span>
                      <time dateTime={e.at} className="shrink-0 text-[11.5px] text-fg-3">
                        {formatRelative(e.at)}
                      </time>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        )}
      </Boundary>
    </Widget>
  )
}

function Count({ label, value, tone }: { label: string; value: number; tone?: 'danger' | 'warning' }) {
  return (
    <div className="min-w-0">
      <div className={`text-[20px] leading-6 font-semibold tabular-nums ${tone === 'danger' ? 'text-danger' : tone === 'warning' ? 'text-warning' : 'text-fg'}`}>{value}</div>
      <div className="truncate text-[11.5px] text-fg-3">{label}</div>
    </div>
  )
}
