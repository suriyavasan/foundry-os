import { Archive, BookMarked } from 'lucide-react'
import { useMemo } from 'react'
import { ApiError, errorMessage, isNotDeployed, useArchiveReflection, useMe, useReflection, useReflections } from '@/api'
import type { Agent } from '@/api/types'
import { Badge, Button, Card, CardBody, CardHeader, EmptyState, ErrorState, LoadingState, Markdown, NotAvailable, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { useAgentMap, useCompanyRole } from '@/features/canvas/hooks'
import { formatDateTime, formatRelative } from '@/lib/format'
import { REACTION_PAST, REACTION_TONE, adoptionNotice, canReact, responseAuthor } from '@/lib/reflections'
import { AI_COFOUNDER_LABEL } from '@/lib/terms'
import { EvidenceChips, KindChip, StatusChip } from './ReflectionBadges'
import { ReactionBar } from './ReactionBar'

/** `?panel=reflection:<id>`: the reflection in full, the conversation about it, and its revisions. */
export function ReflectionThreadPanel({ reflectionId }: { reflectionId: string }) {
  const { companyId, openPanel } = useCanvas()
  const q = useReflection(companyId, reflectionId)
  const list = useReflections(companyId)
  const { map } = useAgentMap(companyId)
  const me = useMe()
  const role = useCompanyRole(companyId)
  const archive = useArchiveReflection(companyId)
  const toast = useToast()
  const d = q.data
  const canRespond = role !== 'viewer'
  const agentName = useMemo(() => (id: string) => (map.get(id) as Agent | undefined)?.name ?? null, [map])
  const missing = q.error instanceof ApiError && q.error.status === 404

  function doArchive() {
    archive.mutate(reflectionId, { onSuccess: () => toast.success('Archived'), onError: (e) => toast.error(errorMessage(e)) })
  }

  return (
    <PanelFrame
      title="Reflection"
      subtitle={d?.title}
      actions={
        d && d.status !== 'archived' && canRespond ? (
          <Button size="sm" icon={<Archive className="size-3.5" />} loading={archive.isPending} onClick={doArchive}>
            Archive
          </Button>
        ) : undefined
      }
    >
      {q.isPending ? (
        <Card>
          <LoadingState rows={6} label="Loading reflection" />
        </Card>
      ) : missing ? (
        <Card>
          {isNotDeployed(list.error) ? (
            <NotAvailable milestone="M10" title="Reflections are not built yet" description={`The ${AI_COFOUNDER_LABEL} shares reflections once this backend has the reflections feature.`} />
          ) : (
            <EmptyState title="Reflection not found" description="It may have been removed, or it belongs to another company." />
          )}
        </Card>
      ) : q.isError ? (
        <Card>
          <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        </Card>
      ) : d ? (
        <div className="space-y-4">
          <Card>
            <CardBody className="space-y-3">
              <div className="flex flex-wrap items-center gap-1.5">
                <KindChip kind={d.kind} />
                <StatusChip status={d.status} />
                <span className="ml-auto text-[12px] text-fg-3" title={formatDateTime(d.created_at)}>
                  {d.author_name ?? AI_COFOUNDER_LABEL} · {formatRelative(d.created_at)}
                </span>
              </div>
              <h3 className="text-[16px] leading-snug font-semibold text-fg">{d.title}</h3>
              <Markdown source={d.body} />
              {d.proposed_standard && (
                <div className="rounded-md border border-accent/40 bg-accent-subtle px-3 py-2">
                  <p className="text-[11.5px] font-medium tracking-wide text-accent-fg uppercase">Proposed standard</p>
                  <p className="mt-1 text-[13.5px] text-fg">{d.proposed_standard}</p>
                  {adoptionNotice(d) && <p className="mt-1.5 text-[12px] text-fg-2">{adoptionNotice(d)}</p>}
                </div>
              )}
              {d.standard_id && (
                <p className="flex items-center gap-2 text-[13px] text-success">
                  <BookMarked className="size-4" aria-hidden /> Adopted as a company standard.
                  <button type="button" className="text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'standards' })}>
                    Open standards
                  </button>
                </p>
              )}
              <EvidenceChips evidence={d.evidence} onOpen={openPanel} />
              {canReact(d) && canRespond && <ReactionBar companyId={companyId} reflection={d} canRespond={canRespond} />}
              {!canReact(d) && <p className="text-[12.5px] text-fg-3">This reflection is {d.status}; it takes no more reactions.</p>}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Conversation" description={`Your responses wake the ${AI_COFOUNDER_LABEL}, who answers or revises the reflection.`} />
            {d.responses.length === 0 ? (
              <EmptyState compact title="No responses yet" description="Endorse, challenge or refine above." />
            ) : (
              <ol aria-label="Responses" className="divide-y divide-line">
                {d.responses.map((r) => (
                  <li key={r.id} className="space-y-1.5 px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
                      <span className="font-medium text-fg">{r.author_name && r.author_type === 'agent' ? `${r.author_name} · ${AI_COFOUNDER_LABEL}` : responseAuthor(r, { meUserId: me.data?.user.id, agentName })}</span>
                      <Badge tone={REACTION_TONE[r.reaction]}>{r.author_type === 'human' ? REACTION_PAST[r.reaction] : r.reaction === 'comment' ? 'replied' : REACTION_PAST[r.reaction]}</Badge>
                      {(r.standard_id || r.reflection_status === 'adopted') && <Badge tone="success">Adopted as a standard</Badge>}
                      <span className="ml-auto text-[11.5px] text-fg-3" title={formatDateTime(r.created_at)}>
                        {formatRelative(r.created_at)}
                      </span>
                    </div>
                    {r.body ? <Markdown source={r.body} /> : <p className="text-[12.5px] text-fg-3">No note.</p>}
                  </li>
                ))}
              </ol>
            )}
          </Card>

          {d.revisions && d.revisions.length > 0 && (
            <Card>
              <CardHeader title="Revision history" description={`Earlier versions, from before the ${AI_COFOUNDER_LABEL} revised this reflection.`} />
              <ol aria-label="Revisions" className="divide-y divide-line">
                {d.revisions.map((rev, i) => (
                  <li key={rev.id ?? i} className="px-4 py-3">
                    <details>
                      <summary className="cursor-pointer text-[13px] text-fg">
                        {rev.title} <span className="text-fg-3">· {formatRelative(rev.created_at)}</span>
                      </summary>
                      <div className="mt-2 space-y-2">
                        <Markdown source={rev.body} />
                        {rev.proposed_standard && <p className="text-[12.5px] text-fg-2">Proposed standard then: {rev.proposed_standard}</p>}
                      </div>
                    </details>
                  </li>
                ))}
              </ol>
            </Card>
          )}
        </div>
      ) : null}
    </PanelFrame>
  )
}
