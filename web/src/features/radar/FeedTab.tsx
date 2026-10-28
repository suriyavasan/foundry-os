import { useDeferredValue, useState } from 'react'
import { errorMessage } from '@/api'
import { useRadarItems, useUpdateRadarItem, type ItemStatus, type RadarItem } from '@/api/radar'
import { Boundary, Input, Select, useToast } from '@/components/ui'
import { ItemRow } from './ItemRow'
import { ITEM_STATUS_FILTERS, SOURCE_CATEGORIES } from './radarLogic'
import { UntrustedBanner } from './Untrusted'

export function FeedTab({ companyId, canAct }: { companyId: string; canAct: boolean }) {
  const [category, setCategory] = useState('')
  const [status, setStatus] = useState<ItemStatus | 'all'>('all')
  const [flagged, setFlagged] = useState(false)
  const [text, setText] = useState('')
  const deferred = useDeferredValue(text.trim())
  const filters = { category: category || undefined, status: status === 'all' ? undefined : status, flagged: flagged || undefined, q: deferred || undefined }
  const q = useRadarItems(companyId, filters)
  const update = useUpdateRadarItem(companyId)
  const toast = useToast()
  const [busyId, setBusyId] = useState<string | null>(null)
  const filtered = Boolean(category || status !== 'all' || flagged || deferred)

  function toggle(item: RadarItem) {
    setBusyId(item.id)
    update.mutate(
      { id: item.id, status: item.status === 'dismissed' ? 'new' : 'dismissed' },
      {
        onError: (err) => toast.error(errorMessage(err, 'Could not update the item')),
        onSettled: () => setBusyId(null),
      },
    )
  }

  return (
    <div className="space-y-3">
      <UntrustedBanner>Titles, excerpts and authors come straight from other websites. Links open in a new tab.</UntrustedBanner>
      <div className="flex flex-wrap items-center gap-2">
        <Input aria-label="Search the feed" type="search" placeholder="Search titles and excerpts" className="h-7 w-56 text-[12.5px]" value={text} onChange={(e) => setText(e.target.value)} />
        <Select aria-label="Filter by category" className="h-7 w-auto text-[12.5px]" value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">Any category</option>
          {SOURCE_CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
        <Select aria-label="Filter by status" className="h-7 w-auto text-[12.5px]" value={status} onChange={(e) => setStatus(e.target.value as ItemStatus | 'all')}>
          {ITEM_STATUS_FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </Select>
        <label className="flex items-center gap-1.5 text-[12.5px] text-fg-2">
          <input type="checkbox" className="size-3.5 accent-(--accent)" checked={flagged} onChange={(e) => setFlagged(e.target.checked)} />
          Flagged text only
        </label>
      </div>
      <div className="rounded-md border border-line bg-surface">
        <Boundary
          query={q}
          compact={false}
          milestone="M16"
          unavailableTitle="The feed is not built yet"
          unavailableText="Collected items come with M16."
          isEmpty={(d) => d.length === 0}
          empty={{
            title: filtered ? 'Nothing matches these filters' : 'Nothing collected yet',
            description: filtered ? 'Clear a filter to see more.' : 'Items appear here after the first fetch. Only items that match your keywords are kept, except from sources where every item matters.',
          }}
        >
          {(items) => (
            <ul className="divide-y divide-line">
              {items.map((it) => (
                <ItemRow key={it.id} item={it} canAct={canAct} busy={busyId === it.id} onToggleDismiss={toggle} />
              ))}
            </ul>
          )}
        </Boundary>
      </div>
    </div>
  )
}
