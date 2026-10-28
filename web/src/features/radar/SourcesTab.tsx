import { Plus, RotateCcw, Trash2, Zap } from 'lucide-react'
import { useState } from 'react'
import { ApiError, errorMessage } from '@/api'
import {
  useCreateRadarSource,
  useDeleteRadarSource,
  useRadarSources,
  useReseedRadarSources,
  useTestRadarSource,
  useUpdateRadarSource,
  type RadarProbeResult,
  type RadarSource,
  type SourceCategory,
} from '@/api/radar'
import { Badge, Boundary, Button, ConfirmDialog, Dialog, Field, FormError, Input, LoadingState, Select, Switch, TBody, TD, TH, THead, TR, Table, Tooltip, useToast } from '@/components/ui'
import { formatRelative } from '@/lib/format'
import { SOURCE_CATEGORIES, disabledReason, fetchStatus, intervalLabel, isHttpUrl, readVia } from './radarLogic'
import { ExternalLink, UntrustedText } from './Untrusted'

export function SourcesTab({ companyId, canAct }: { companyId: string; canAct: boolean }) {
  const q = useRadarSources(companyId)
  const [category, setCategory] = useState('')
  const [adding, setAdding] = useState(false)
  const reseed = useReseedRadarSources(companyId)
  const toast = useToast()
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[12.5px] text-fg-3">Where the radar reads. Each feed is fetched politely: robots.txt is respected, requests are spaced per site, and nothing is fetched from a source you switch off.</p>
        <div className="flex flex-wrap items-center gap-2">
          <Select aria-label="Filter sources by category" className="h-7 w-auto text-[12.5px]" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {SOURCE_CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </Select>
          {canAct && (
            <>
              <Button
                size="sm"
                loading={reseed.isPending}
                icon={<RotateCcw className="size-3.5" />}
                onClick={() => reseed.mutate(undefined, { onSuccess: () => toast.success('Missing sources restored. Ones you edited were left alone.'), onError: (err) => toast.error(errorMessage(err, 'Could not restore the sources')) })}
              >
                Restore missing
              </Button>
              <Button size="sm" variant="primary" icon={<Plus className="size-3.5" />} onClick={() => setAdding(true)}>
                Add a feed
              </Button>
            </>
          )}
        </div>
      </div>
      <div className="rounded-md border border-line bg-surface">
        <Boundary
          query={q}
          compact={false}
          milestone="M16"
          unavailableTitle="Sources are not built yet"
          unavailableText="The source registry comes with M16."
          isEmpty={(d) => d.length === 0}
          empty={{ title: 'No sources', description: canAct ? 'Add a public RSS or Atom feed, or restore the starting list.' : 'The owner has not added any source yet.' }}
        >
          {(sources) => {
            const shown = category ? sources.filter((s) => s.category === category) : sources
            if (shown.length === 0) return <p className="p-4 text-[13px] text-fg-3">No sources in this category.</p>
            return <SourceTable companyId={companyId} sources={shown} canAct={canAct} />
          }}
        </Boundary>
      </div>
      {adding && <AddSourceDialog companyId={companyId} onClose={() => setAdding(false)} />}
    </div>
  )
}

function SourceTable({ companyId, sources, canAct }: { companyId: string; sources: RadarSource[]; canAct: boolean }) {
  return (
    <Table>
      <THead>
        <TR>
          <TH>Source</TH>
          <TH>How it is read</TH>
          <TH>Last fetch</TH>
          <TH className="text-right">Items</TH>
          <TH>On</TH>
          {canAct && (
            <TH>
              <span className="sr-only">Actions</span>
            </TH>
          )}
        </TR>
      </THead>
      <TBody>
        {sources.map((s) => (
          <SourceRow key={s.id} companyId={companyId} source={s} canAct={canAct} />
        ))}
      </TBody>
    </Table>
  )
}

function SourceRow({ companyId, source: s, canAct }: { companyId: string; source: RadarSource; canAct: boolean }) {
  const update = useUpdateRadarSource(companyId)
  const del = useDeleteRadarSource(companyId)
  const test = useTestRadarSource(companyId)
  const toast = useToast()
  const [testOpen, setTestOpen] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const st = fetchStatus(s.last_status)
  const off = disabledReason(s.disabled_reason)
  const searchOnly = s.read_via === 'web_search'

  function toggle(next: boolean) {
    update.mutate({ id: s.id, enabled: next }, { onSuccess: () => toast.success(next ? `${s.name} is on` : `${s.name} is off`), onError: (err) => toast.error(errorMessage(err, 'Could not change the source')) })
  }
  function runTest() {
    setTestOpen(true)
    test.mutate(s.id)
  }
  const testError = test.error ? (test.error instanceof ApiError && test.error.status === 429 ? 'Too many test fetches this hour. Try again later.' : errorMessage(test.error, 'The test fetch failed')) : null

  return (
    <TR>
      <TD className="max-w-[16rem]">
        <span className="block truncate font-medium text-fg">
          <ExternalLink href={s.url}>{s.name}</ExternalLink>
        </span>
        <span className="mt-0.5 flex flex-wrap items-center gap-1 text-[11.5px] text-fg-3">
          <span>{s.category}</span>
          {s.origin === 'seed' && <Badge tone="neutral">Seed</Badge>}
          {s.origin === 'agent' && <Badge tone="info">Added by an agent</Badge>}
          {s.origin === 'owner' && <Badge tone="accent">Added by you</Badge>}
          {s.robots_override && (
            <Tooltip content="You told the radar to ignore this site's robots.txt.">
              <Badge tone="warning">robots.txt ignored</Badge>
            </Tooltip>
          )}
        </span>
      </TD>
      <TD className="max-w-[14rem] text-[12.5px] text-fg-2">
        {readVia(s)}
        {!searchOnly && <span className="block text-[11.5px] text-fg-3">{intervalLabel(s.interval_seconds)}</span>}
        {s.tos_note && (
          <span className="block text-[11.5px] text-fg-3" title="Terms of use, as noted when the list was checked">
            {s.tos_note}
          </span>
        )}
      </TD>
      <TD className="max-w-[14rem]">
        {searchOnly ? (
          <span className="text-[12.5px] text-fg-3">Never fetched</span>
        ) : (
          <>
            <Badge tone={st.tone}>{st.label}</Badge>
            <span className="mt-0.5 block text-[11.5px] text-fg-3" title={s.last_fetched_at ?? undefined}>
              {s.last_fetched_at ? formatRelative(s.last_fetched_at) : 'Not fetched yet'}
              {s.consecutive_failures > 0 && ` · ${s.consecutive_failures} in a row failed`}
            </span>
            {s.last_error && <span className="block truncate text-[11.5px] text-danger" title={s.last_error}>{s.last_error}</span>}
          </>
        )}
        {off && !s.enabled && <span className="mt-0.5 block text-[11.5px] text-fg-3">{off}</span>}
      </TD>
      <TD className="text-right tabular-nums">{s.items_total.toLocaleString('en-US')}</TD>
      <TD>
        {canAct ? (
          <Switch label={`${s.name} is ${s.enabled ? 'on' : 'off'}`} checked={s.enabled} loading={update.isPending} onCheckedChange={toggle} />
        ) : (
          <Badge tone={s.enabled ? 'success' : 'neutral'}>{s.enabled ? 'On' : 'Off'}</Badge>
        )}
      </TD>
      {canAct && (
        <TD>
          <div className="flex items-center justify-end gap-1">
            {!searchOnly && (
              <Button size="sm" variant="ghost" icon={<Zap className="size-3.5" />} loading={test.isPending} onClick={runTest} aria-label={`Test ${s.name}`}>
                Test
              </Button>
            )}
            {s.origin !== 'seed' && (
              <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => setConfirmDelete(true)} aria-label={`Delete ${s.name}`}>
                Delete
              </Button>
            )}
          </div>
          {testOpen && (
            <TestDialog
              source={s}
              pending={test.isPending}
              result={test.data ?? null}
              error={testError}
              onClose={() => {
                setTestOpen(false)
                test.reset()
              }}
            />
          )}
          <ConfirmDialog
            open={confirmDelete}
            onOpenChange={setConfirmDelete}
            title={`Delete ${s.name}?`}
            description="The source is removed. Items already collected from it are kept."
            confirmLabel="Delete source"
            loading={del.isPending}
            error={del.error ? errorMessage(del.error) : null}
            onConfirm={() => del.mutate(s.id, { onSuccess: () => setConfirmDelete(false) })}
          />
        </TD>
      )}
    </TR>
  )
}

function TestDialog({ source, pending, result, error, onClose }: { source: RadarSource; pending: boolean; result: RadarProbeResult | null; error: string | null; onClose: () => void }) {
  const st = result ? fetchStatus(result.status) : null
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={`Test fetch: ${source.name}`} description="One request, nothing is stored." footer={<Button onClick={onClose}>Close</Button>}>
      <div className="space-y-3 text-[13px]">
        {pending && <LoadingState rows={2} label="Fetching" />}
        <FormError message={error} />
        {result && st && (
          <>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[12.5px]">
              <dt className="text-fg-3">Result</dt>
              <dd>
                <Badge tone={st.tone}>{st.label}</Badge>
              </dd>
              <dt className="text-fg-3">robots.txt</dt>
              <dd className="text-fg-2">{result.robots.replace(/_/g, ' ')}</dd>
              <dt className="text-fg-3">HTTP status</dt>
              <dd className="text-fg-2">{result.http_status ?? 'none'}</dd>
              <dt className="text-fg-3">Requests made</dt>
              <dd className="text-fg-2">{result.requests}</dd>
            </dl>
            {result.error && <p className="text-danger">{result.error}</p>}
            {result.items.length > 0 ? (
              <div>
                <p className="mb-1 text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">First {Math.min(5, result.items.length)} items it read (untrusted text)</p>
                <ul className="divide-y divide-line rounded-md border border-line">
                  {result.items.slice(0, 5).map((it) => (
                    <li key={it.url + it.title} className="px-3 py-2">
                      <UntrustedText as="p" className="font-medium text-fg">
                        <ExternalLink href={it.url} showHost>
                          {it.title}
                        </ExternalLink>
                      </UntrustedText>
                      {it.excerpt && <UntrustedText as="p" className="line-clamp-2 text-[12px] text-fg-3">{it.excerpt}</UntrustedText>}
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              !result.error && <p className="text-fg-3">It read no items.</p>
            )}
          </>
        )}
      </div>
    </Dialog>
  )
}

function AddSourceDialog({ companyId, onClose }: { companyId: string; onClose: () => void }) {
  const create = useCreateRadarSource(companyId)
  const toast = useToast()
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [feed, setFeed] = useState('')
  const [category, setCategory] = useState<SourceCategory>('blog')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setFormError(null)
    const ce: Record<string, string> = {}
    if (!name.trim()) ce.name = 'Give the feed a name'
    if (!isHttpUrl(url)) ce.url = 'Enter the site address, starting with https://'
    if (!isHttpUrl(feed)) ce.fetch_url = 'Enter the feed address, starting with https://'
    setErrors(ce)
    if (Object.keys(ce).length) return
    try {
      await create.mutateAsync({ name: name.trim(), url: url.trim(), fetch_url: feed.trim(), category })
      toast.success('Feed added')
      onClose()
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        setErrors(err.fieldErrors)
        setFormError(Object.keys(err.fieldErrors).length ? 'Some fields need attention.' : err.message)
      } else {
        setFormError(errorMessage(err, 'Could not add the feed'))
      }
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      title="Add a feed"
      description="A public RSS, Atom or JSON feed. Use Test afterwards to see what the radar reads from it."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={create.isPending}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="add-radar-source" loading={create.isPending}>
            Add feed
          </Button>
        </>
      }
    >
      <form id="add-radar-source" onSubmit={submit} noValidate className="space-y-3">
        <Field label="Name" required error={errors.name}>
          {({ id, invalid, describedBy }) => <Input id={id} value={name} invalid={invalid} aria-describedby={describedBy} maxLength={160} onChange={(e) => setName(e.target.value)} />}
        </Field>
        <Field label="Site address" required error={errors.url} hint="The page people visit.">
          {({ id, invalid, describedBy }) => <Input id={id} type="url" placeholder="https://example.com" value={url} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setUrl(e.target.value)} />}
        </Field>
        <Field label="Feed address" required error={errors.fetch_url} hint="The RSS, Atom or JSON feed. robots.txt is checked before every fetch.">
          {({ id, invalid, describedBy }) => <Input id={id} type="url" placeholder="https://example.com/feed.xml" value={feed} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setFeed(e.target.value)} />}
        </Field>
        <Field label="Category" error={errors.category}>
          {({ id, describedBy }) => (
            <Select id={id} aria-describedby={describedBy} value={category} onChange={(e) => setCategory(e.target.value as SourceCategory)}>
              {SOURCE_CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <FormError message={formError} />
      </form>
    </Dialog>
  )
}
