import { Badge, Tooltip } from '@/components/ui'
import type { Reflection } from '@/api/types'
import type { Panel } from '@/lib/panels'
import { REFLECTION_KIND_TONE, REFLECTION_STATUS_HELP, REFLECTION_STATUS_LABEL, REFLECTION_STATUS_TONE, evidenceChips, reflectionKindLabel } from '@/lib/reflections'

export function KindChip({ kind }: { kind: Reflection['kind'] }) {
  return <Badge tone={REFLECTION_KIND_TONE[kind] ?? 'neutral'}>{reflectionKindLabel(kind)}</Badge>
}

export function StatusChip({ status }: { status: Reflection['status'] }) {
  return (
    <Tooltip content={REFLECTION_STATUS_HELP[status] ?? ''}>
      <Badge tone={REFLECTION_STATUS_TONE[status] ?? 'neutral'}>{REFLECTION_STATUS_LABEL[status] ?? status}</Badge>
    </Tooltip>
  )
}

/** The records a reflection is about. A chip opens the panel of its record when the UI has one; otherwise it is plain text. */
export function EvidenceChips({ evidence, onOpen }: { evidence: Reflection['evidence']; onOpen: (p: Panel) => void }) {
  const chips = evidenceChips(evidence)
  if (chips.length === 0) return null
  return (
    <ul aria-label="Evidence" className="flex flex-wrap gap-1">
      {chips.map((c) => {
        const cls = 'inline-flex max-w-full items-center gap-1 rounded-sm border border-line bg-raised px-1.5 py-0.5 text-[11.5px]'
        const inner = (
          <>
            <span className="shrink-0 text-fg-3">{c.typeLabel}</span>
            <span className="min-w-0 truncate text-fg">{c.label === c.typeLabel ? '' : c.label}</span>
          </>
        )
        return (
          <li key={c.key} className="min-w-0">
            {c.panel ? (
              <button type="button" onClick={() => onOpen(c.panel!)} aria-label={`Open ${c.typeLabel.toLowerCase()}${c.label === c.typeLabel ? '' : `: ${c.label}`}`} className={`${cls} transition-colors hover:bg-hover`}>
                {inner}
              </button>
            ) : (
              <span className={cls}>{inner}</span>
            )}
          </li>
        )
      })}
    </ul>
  )
}
