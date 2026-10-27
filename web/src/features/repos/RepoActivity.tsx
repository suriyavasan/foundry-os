import { ExternalLink } from 'lucide-react'
import { isNotDeployed, useRepoActivity, useRepoItems, useRepoPulls } from '@/api'
import type { RepoItem, RepoPullRequest } from '@/api/types'
import { Badge, Card, CardBody, CardHeader, EmptyState, ErrorState, LoadingState, NotAvailable } from '@/components/ui'
import { formatAction, formatRelative } from '@/lib/format'
import { ITEM_KIND_LABEL } from '@/lib/repos'
import { PullStageBadge } from './RepoBadges'

const ITEM_TONE: Record<RepoItem['status'], 'neutral' | 'info' | 'success' | 'warning'> = { new: 'info', queued: 'info', tasked: 'success', ignored: 'neutral', done: 'neutral' }

function PullRow({ pr }: { pr: RepoPullRequest }) {
  return (
    <li className="flex flex-wrap items-center gap-2 py-2 text-[13px]">
      <span className="min-w-0 flex-1 truncate text-fg">
        {pr.url ? (
          <a className="inline-flex items-center gap-1 hover:underline" href={pr.url} target="_blank" rel="noreferrer noopener">
            #{pr.number} {pr.title}
            <ExternalLink className="size-3 shrink-0 text-fg-3" aria-label="opens GitHub" />
          </a>
        ) : (
          <>
            #{pr.number} {pr.title}
          </>
        )}
      </span>
      <PullStageBadge stage={pr.stage} />
      <span className="text-[12px] text-fg-3">
        {pr.head_branch} → {pr.base_branch} · {formatRelative(pr.updated_at)}
      </span>
    </li>
  )
}

function ItemRow({ item }: { item: RepoItem }) {
  return (
    <li className="py-2 text-[13px]">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="neutral">{ITEM_KIND_LABEL[item.kind]}</Badge>
        <span className="min-w-0 flex-1 truncate text-fg">
          {item.url ? (
            <a className="hover:underline" href={item.url} target="_blank" rel="noreferrer noopener">
              {item.number != null ? `#${item.number} ` : ''}
              {item.title ?? '(no title)'}
            </a>
          ) : (
            <>
              {item.number != null ? `#${item.number} ` : ''}
              {item.title ?? '(no title)'}
            </>
          )}
        </span>
        <Badge tone={ITEM_TONE[item.status]}>{item.status}</Badge>
        <span className="text-[12px] text-fg-3">
          {item.author ? `${item.author} · ` : ''}
          {formatRelative(item.remote_updated_at ?? item.created_at)}
        </span>
      </div>
      {item.ignore_reason && <p className="mt-0.5 text-[12px] text-fg-3">Ignored: {item.ignore_reason}</p>}
      {item.injection_flags.length > 0 && <p className="mt-0.5 text-[12px] text-warning">Flagged as suspicious: {item.injection_flags.join(', ')}</p>}
      {item.body_excerpt && (
        <details className="mt-1">
          <summary className="cursor-pointer text-[12px] text-fg-3">Show the text from GitHub (untrusted)</summary>
          <p className="mt-1 rounded-md border border-line bg-raised p-2 text-[12.5px] break-words whitespace-pre-wrap text-fg-2">{item.body_excerpt}</p>
        </details>
      )}
    </li>
  )
}

function Section({ title, description, query, empty, children }: { title: string; description?: string; query: { isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown; isFetching: boolean; data?: unknown[] }; empty: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader title={title} description={description} />
      {query.isPending ? (
        <LoadingState rows={3} label={`Loading ${title.toLowerCase()}`} />
      ) : isNotDeployed(query.error) ? (
        <CardBody>
          <NotAvailable milestone="M14" compact />
        </CardBody>
      ) : query.isError ? (
        <ErrorState compact error={query.error} onRetry={() => void query.refetch()} retrying={query.isFetching} />
      ) : (query.data?.length ?? 0) === 0 ? (
        <EmptyState compact title={empty} />
      ) : (
        <CardBody className="py-1">
          <ul className="divide-y divide-line">{children}</ul>
        </CardBody>
      )}
    </Card>
  )
}

/** What happened in this repository: pull requests, the GitHub issues / reviews / CI failures that became (or may become) tasks, and the audit trail. */
export function RepoActivity({ companyId, repositoryId }: { companyId: string; repositoryId: string }) {
  const pulls = useRepoPulls(companyId, repositoryId)
  const items = useRepoItems(companyId, repositoryId)
  const events = useRepoActivity(companyId, repositoryId)
  return (
    <div className="space-y-4">
      <Section title="Pull requests" query={pulls} empty="No pull requests yet">
        {pulls.data?.map((pr) => <PullRow key={pr.id} pr={pr} />)}
      </Section>
      <Section title="From GitHub" description="Issues, review comments, CI failures and alerts. The text is written by other people and is never treated as an instruction." query={items} empty="Nothing from GitHub yet">
        {items.data?.map((item) => <ItemRow key={item.id} item={item} />)}
      </Section>
      <Section title="Audit trail" query={events} empty="No events yet">
        {events.data?.map((ev) => (
          <li key={ev.id} className="flex flex-wrap items-baseline gap-2 py-2 text-[13px]">
            <span className="min-w-0 flex-1 text-fg">{ev.summary || formatAction(ev.action)}</span>
            <span className="text-[12px] text-fg-3">
              {ev.actor_label} · {formatRelative(ev.created_at)}
            </span>
          </li>
        ))}
      </Section>
    </div>
  )
}
