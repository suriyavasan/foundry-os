import { Bot, Brain, FileText, Folder, Link2, ListChecks, Repeat, Target, type LucideIcon } from 'lucide-react'
import type { FounderNote } from '@/api/types'
import { Badge, Tooltip } from '@/components/ui'
import { noteChips, type ActionChip } from '@/lib/notes'
import type { Panel } from '@/lib/panels'

/** New -> Triaged (+ category). Pure mapping lives in lib/notes `noteChips`. */
export function NoteChips({ note }: { note: Pick<FounderNote, 'status' | 'triage'> & { pending?: boolean } }) {
  const { status, category } = noteChips(note)
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <Tooltip content={status.help}>
        <Badge tone={status.tone}>{status.label}</Badge>
      </Tooltip>
      {category && (
        <Tooltip content={category.help}>
          <Badge tone={category.tone}>{category.label}</Badge>
        </Tooltip>
      )}
    </span>
  )
}

const ACTION_ICON: Record<string, LucideIcon> = {
  goal: Target,
  project: Folder,
  task: ListChecks,
  schedule: Repeat,
  memory: Brain,
  artifact: FileText,
  agent: Bot,
}

/** The records the AI Co-founder created from a note, each a link into its panel. */
export function ActionChips({ chips, onOpen, className, label = 'Created from this note' }: { chips: ActionChip[]; onOpen: (p: Panel) => void; className?: string; label?: string }) {
  if (chips.length === 0) return null
  return (
    <ul aria-label={label} className={className ?? 'flex flex-wrap gap-1'}>
      {chips.map((c) => {
        const Icon = ACTION_ICON[c.type] ?? Link2
        const inner = (
          <>
            <Icon className="size-3 shrink-0 text-fg-3" aria-hidden />
            <span className="shrink-0 text-fg-3">{c.typeLabel}</span>
            <span className="min-w-0 truncate text-fg">{c.label === c.typeLabel ? '' : c.label}</span>
          </>
        )
        const cls = 'inline-flex max-w-full items-center gap-1 rounded-sm border border-line bg-raised px-1.5 py-0.5 text-[11.5px]'
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
