import { ExternalLink, FolderGit2, Search, Star } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { ApiError, errorMessage, useInstallSkill, useSkillCatalog } from '@/api'
import type { CatalogItem, CatalogSource, InstallSkillResult } from '@/api/types'
import { Badge, Button, Card, Chip, EmptyState, ErrorState, Input, LoadingState, NotAvailable } from '@/components/ui'
import { isNotDeployed } from '@/api/work'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { describeInstall, repoQueryFor } from '@/lib/catalog'
import { VerdictBadge } from './SkillBadges'

const SOURCES: Array<{ id: CatalogSource; label: string; hint: string }> = [
  { id: 'all', label: 'All', hint: 'Library, Hermes and anything already installed' },
  { id: 'library', label: 'Library', hint: 'Skills already in this company' },
  { id: 'hermes', label: 'Hermes', hint: 'The bundled and optional Hermes skills' },
  { id: 'github', label: 'GitHub', hint: 'Public repositories. Type owner/repo to list its skills.' },
]

export function CatalogTab() {
  const { companyId } = useCanvas()
  const [source, setSource] = useState<CatalogSource>('all')
  const [draft, setDraft] = useState('')
  const [q, setQ] = useState('')
  // GitHub needs something to search for; the other sources list on their own.
  const enabled = source !== 'github' || q.trim() !== ''
  const query = useSkillCatalog(companyId, q.trim(), source, enabled)
  const items = query.data?.pages.flatMap((p) => p.items) ?? []

  function submit(e: FormEvent) {
    e.preventDefault()
    setQ(draft)
  }
  function browseRepo(item: CatalogItem) {
    const next = repoQueryFor(item)
    setDraft(next)
    setQ(next)
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div role="radiogroup" aria-label="Catalog source" className="inline-flex rounded-md border border-line-strong bg-raised p-0.5">
          {SOURCES.map((s) => (
            <button
              key={s.id}
              type="button"
              role="radio"
              aria-checked={source === s.id}
              title={s.hint}
              onClick={() => setSource(s.id)}
              className={`h-7 rounded-sm px-3 text-[12.5px] font-medium transition-colors ${source === s.id ? 'bg-accent text-fg-on-accent' : 'text-fg-2 hover:bg-hover hover:text-fg'}`}
            >
              {s.label}
            </button>
          ))}
        </div>
        <form onSubmit={submit} role="search" aria-label="Search the catalog" className="flex min-w-60 flex-1 gap-2">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-fg-3" aria-hidden />
            <Input aria-label="Search catalog" className="pl-8" placeholder={source === 'github' ? 'owner/repo, or words to search repositories' : 'Search skills'} value={draft} onChange={(e) => setDraft(e.target.value)} />
          </div>
          <Button type="submit" variant="primary" loading={query.isFetching && !query.isFetchingNextPage && enabled}>
            Search
          </Button>
        </form>
      </div>

      {source === 'github' && (
        <p className="text-[12px] text-fg-3">
          GitHub skills are pinned to a commit and need an allowed open-source license. Skills from repositories other than the trusted ones (anthropics, openai, huggingface, NVIDIA) are quarantined on a caution or dangerous scan.
        </p>
      )}

      {!enabled ? (
        <Card>
          <EmptyState icon={FolderGit2} title="Search GitHub" description="Enter owner/repo to list that repository's skills, or words to search public repositories." />
        </Card>
      ) : query.isPending ? (
        <Card>
          <LoadingState rows={5} label={source === 'github' ? 'Searching GitHub' : 'Searching the catalog'} />
        </Card>
      ) : query.isError ? (
        <Card>
          {isNotDeployed(query.error) ? (
            <NotAvailable milestone="M4" title="The catalog is not built yet" />
          ) : (
            <ErrorState error={query.error} title={query.error instanceof ApiError && query.error.code === 'upstream_error' ? 'The source did not answer' : 'Could not search the catalog'} onRetry={() => void query.refetch()} retrying={query.isFetching} />
          )}
        </Card>
      ) : items.length === 0 ? (
        <Card>
          <EmptyState title="Nothing found" description={q ? `No ${source === 'all' ? '' : SOURCES.find((s) => s.id === source)!.label + ' '}skills match "${q}".` : 'This source has no skills to list.'} />
        </Card>
      ) : (
        <ul className="space-y-2">
          {items.map((item) => (
            <CatalogRow key={`${item.source}:${item.source_ref}`} item={item} onBrowse={browseRepo} />
          ))}
          {query.hasNextPage && (
            <li className="flex justify-center pt-1">
              <Button loading={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
                Load more
              </Button>
            </li>
          )}
        </ul>
      )}
    </div>
  )
}

function CatalogRow({ item, onBrowse }: { item: CatalogItem; onBrowse: (i: CatalogItem) => void }) {
  const { companyId, openPanel } = useCanvas()
  const install = useInstallSkill(companyId)
  const [result, setResult] = useState<InstallSkillResult | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const isRepo = item.item_type === 'repository'
  const installed = item.installed || !!result

  async function doInstall() {
    setFailure(null)
    try {
      setResult(await install.mutateAsync({ source_ref: item.source_ref }))
    } catch (e) {
      // The server's own refusal reason (license_not_allowed, scanner_unavailable, upstream_error, 409...).
      setFailure(e instanceof ApiError ? `${e.message} (${e.code})` : errorMessage(e))
    }
  }

  const summary = result ? describeInstall(result) : null
  return (
    <li>
      <Card className="p-3">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[13.5px] font-medium text-fg">{item.name}</span>
              {isRepo && <Badge tone="info">Repository</Badge>}
              <Badge tone="neutral">{item.source === 'github' ? 'GitHub' : item.source === 'hermes' ? 'Hermes' : 'Library'}</Badge>
              {item.license ? <Badge tone="neutral">{item.license}</Badge> : item.source === 'github' && !isRepo ? <Badge tone="warning">No license found</Badge> : null}
              {item.stars != null && (
                <span className="inline-flex items-center gap-0.5 text-[12px] text-fg-3">
                  <Star className="size-3" aria-hidden /> {item.stars.toLocaleString()}
                </span>
              )}
              {item.installed && <Badge tone="success">Installed{item.status && item.status !== 'active' ? ` · ${item.status}` : ''}</Badge>}
            </div>
            {item.description && <p className="mt-1 line-clamp-2 text-[12.5px] text-fg-2">{item.description}</p>}
            <div className="mt-1.5 flex flex-wrap items-center gap-1">
              {item.tags.slice(0, 6).map((t) => (
                <Chip key={t}>{t}</Chip>
              ))}
              <span className="ml-1 truncate font-mono text-[11px] text-fg-3" title={item.source_ref}>
                {item.source_ref}
              </span>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {isRepo ? (
              <Button size="sm" icon={<ExternalLink className="size-3.5" />} onClick={() => onBrowse(item)}>
                Browse skills
              </Button>
            ) : item.installed && item.skill_id ? (
              <Button size="sm" onClick={() => openPanel({ kind: 'skill', id: item.skill_id! })}>
                View
              </Button>
            ) : (
              <Button size="sm" variant="primary" loading={install.isPending} disabled={installed} onClick={doInstall}>
                {installed ? 'Installed' : 'Install'}
              </Button>
            )}
          </div>
        </div>

        {summary && result && (
          <div role="status" className={`mt-2.5 rounded-md border px-3 py-2 text-[12.5px] ${result.quarantined ? 'border-danger/40 bg-danger-subtle' : result.skill.scan_verdict === 'caution' ? 'border-warning/40 bg-warning-subtle' : 'border-line bg-raised'}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-fg">{summary.headline}</span>
              <VerdictBadge verdict={result.skill.scan_verdict} bare />
            </div>
            <p className="mt-1 text-fg-2">{summary.detail}</p>
            <button type="button" className="mt-1 text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'skill', id: result.skill.id })}>
              Open skill
            </button>
          </div>
        )}
        {failure && (
          <div role="alert" className="mt-2.5 rounded-md border border-danger/40 bg-danger-subtle px-3 py-2 text-[12.5px] text-danger">
            Not installed: {failure}
          </div>
        )}
      </Card>
    </li>
  )
}
