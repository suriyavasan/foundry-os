import { Plus } from 'lucide-react'
import { useState } from 'react'
import { isNotDeployed, useAiStack, useReorderAiStack } from '@/api'
import { Button, ErrorState, LoadingState, NotAvailable } from '@/components/ui'
import { stackBlockingText, summarySentence } from '@/lib/aiStack'
import { AddAiDialog } from './AddAiDialog'
import { AiStackRow } from './AiStackRow'

/**
 * The one place the owner manages their AI (M17): an ordered list of connections, a plain-language summary of who does what,
 * and "Add AI". Used by the onboarding step and the Settings AI panel.
 */
export function AiStack({ companyId, canEdit }: { companyId: string; canEdit: boolean }) {
  const stack = useAiStack(companyId)
  const reorder = useReorderAiStack(companyId)
  const [adding, setAdding] = useState(false)

  if (stack.isPending) return <LoadingState rows={3} label="Loading your AI" />
  if (stack.isError) {
    if (isNotDeployed(stack.error)) {
      return <NotAvailable milestone="M17" title="AI settings are not available on this server yet" description="This backend predates the AI stack. Update the server to manage your AI here." />
    }
    return <ErrorState error={stack.error} onRetry={() => void stack.refetch()} retrying={stack.isFetching} />
  }

  const { items, options, summary, ready, blocking_reason } = stack.data
  const optionOf = (key: string) => options.find((o) => o.option === key)

  function move(index: number, dir: -1 | 1) {
    const ids = items.map((c) => c.id)
    const j = index + dir
    if (j < 0 || j >= ids.length) return
    ;[ids[index], ids[j]] = [ids[j]!, ids[index]!]
    reorder.mutate(ids)
  }

  return (
    <section aria-label="Your AI" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[13px] text-fg-2">
          {items.length === 0 ? 'No AI yet — add one to start.' : 'The first one is tried first. Use the arrows to change the order.'}
        </p>
        {canEdit && (
          <Button variant="primary" icon={<Plus className="size-3.5" />} onClick={() => setAdding(true)}>
            Add AI
          </Button>
        )}
      </div>

      {items.length === 0 ? (
        <div className="rounded-lg border border-dashed border-line-strong bg-surface p-6 text-center">
          <p className="text-[14px] font-medium text-fg">No AI yet</p>
          <p className="mt-1 text-[12.5px] text-fg-3">Add one to start. You can sign in with a plan, paste an API key, or use a model on this computer.</p>
          {!canEdit && <p className="mt-2 text-[12px] text-fg-3">Only an owner or admin can add an AI.</p>}
        </div>
      ) : (
        <ol className="space-y-3" aria-label="AI connections, in order">
          {items.map((c, i) => (
            <AiStackRow
              key={c.id}
              companyId={companyId}
              conn={c}
              option={optionOf(c.option)}
              canEdit={canEdit}
              isFirst={i === 0}
              isLast={i === items.length - 1}
              busyOrder={reorder.isPending}
              onMove={(d) => move(i, d)}
            />
          ))}
        </ol>
      )}
      {reorder.isError && (
        <p role="alert" className="text-[12.5px] text-danger">
          Could not change the order. It was put back.
        </p>
      )}

      {items.length > 0 && (
        <div className="rounded-md border border-line bg-raised p-3 text-[12.5px] text-fg-2" aria-label="Who does what">
          <p>{summarySentence('Heavy lifting', summary.heavy)}</p>
          <p className="mt-1">{summarySentence('Everyday work', summary.everyday)}</p>
        </div>
      )}
      {items.length > 0 && !ready && (
        <p role="status" className="text-[12.5px] text-warning">
          {stackBlockingText(blocking_reason)}
        </p>
      )}

      {adding && <AddAiDialog companyId={companyId} options={options} canEdit={canEdit} onClose={() => setAdding(false)} />}
    </section>
  )
}
