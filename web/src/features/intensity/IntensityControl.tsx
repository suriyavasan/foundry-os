import clsx from 'clsx'
import { useRef, useState } from 'react'
import { ApiError, errorMessage, useSetIntensity } from '@/api'
import type { Intensity } from '@/api/types'
import { Badge, ConfirmDialog, FormError } from '@/components/ui'
import { BUDGETS_STILL_APPLY, INTENSITY_LEVELS, PLAN_ONLY_WARNING, describeIntensity, needsPlanConfirm } from '@/lib/intensity'

export interface IntensityChooser {
  choose: (level: number, confirm?: boolean) => Promise<void>
  /** A level waiting for the plan-only confirmation (the dialog is open while this is set). */
  pending: number | null
  busy: boolean
  error: string | null
  /** The confirmation dialog. Render it OUTSIDE any popover so it survives the popover closing. */
  dialog: React.ReactNode
}

/**
 * Choosing a level writes it through PUT /intensity. Levels 4-5 on a plan-only AI stack ask for a confirmation first
 * (and the server refuses them without `confirm`). Read-only for anyone who is not an owner/admin: the server
 * enforces it, the UI just does not offer it.
 */
export function useIntensityChooser(companyId: string, intensity: Intensity, canEdit: boolean): IntensityChooser {
  const set = useSetIntensity(companyId)
  const [pending, setPending] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function choose(next: number, confirm = false) {
    setError(null)
    if (!canEdit || set.isPending || (next === intensity.level && !intensity.custom)) return
    if (!confirm && needsPlanConfirm(next, intensity.plan_only)) {
      setPending(next)
      return
    }
    try {
      await set.mutateAsync({ level: next, confirm: confirm || undefined })
      setPending(null)
    } catch (err) {
      if (err instanceof ApiError && err.code === 'intensity_confirm_required') {
        setPending(next) // the stack became plan-only since this was loaded
        return
      }
      setError(errorMessage(err))
    }
  }

  const name = INTENSITY_LEVELS.find((l) => l.level === pending)?.name ?? ''
  const dialog = (
    <ConfirmDialog
      open={pending !== null}
      onOpenChange={(o) => {
        if (!o) {
          setPending(null)
          setError(null)
        }
      }}
      title={`Switch to ${name}?`}
      confirmLabel={`Use ${name}`}
      destructive={false}
      loading={set.isPending}
      error={pending !== null ? error : null}
      onConfirm={() => pending !== null && void choose(pending, true)}
      description={
        <div className="space-y-2">
          <p>{intensity.warning ?? PLAN_ONLY_WARNING}</p>
          <p>Every AI connection of this company is a personal plan. You can lower the intensity again at any time.</p>
        </div>
      }
    />
  )
  return { choose, pending, busy: set.isPending, error, dialog }
}

/** The five-step control: a radio group (arrow keys move between steps, Space/Enter chooses). */
export function IntensitySteps({ intensity, canEdit, chooser, className }: { intensity: Intensity; canEdit: boolean; chooser: IntensityChooser; className?: string }) {
  const refs = useRef<Array<HTMLButtonElement | null>>([])
  const level = intensity.level

  function onKeyDown(e: React.KeyboardEvent, index: number) {
    const last = INTENSITY_LEVELS.length - 1
    const to =
      e.key === 'ArrowRight' || e.key === 'ArrowDown'
        ? Math.min(last, index + 1)
        : e.key === 'ArrowLeft' || e.key === 'ArrowUp'
          ? Math.max(0, index - 1)
          : e.key === 'Home'
            ? 0
            : e.key === 'End'
              ? last
              : null
    if (to === null) return
    e.preventDefault()
    refs.current[to]?.focus()
  }

  return (
    <div className={className}>
      <div role="radiogroup" aria-label="Company intensity" aria-disabled={!canEdit || undefined} className="grid grid-cols-5 gap-1">
        {INTENSITY_LEVELS.map((l, i) => {
          const checked = l.level === level
          return (
            <button
              key={l.level}
              ref={(el) => {
                refs.current[i] = el
              }}
              type="button"
              role="radio"
              aria-checked={checked}
              aria-label={`${l.level}, ${l.name}`}
              aria-disabled={!canEdit || undefined}
              tabIndex={checked ? 0 : -1}
              disabled={chooser.busy}
              onClick={() => void chooser.choose(l.level)}
              onKeyDown={(e) => onKeyDown(e, i)}
              title={canEdit ? l.name : `${l.name} (only owners and admins can change this)`}
              className={clsx(
                'flex h-8 items-center justify-center rounded-sm border text-[12px] font-medium transition-colors focus-visible:outline-2 focus-visible:outline-(--focus-ring)',
                checked ? 'border-accent bg-accent-subtle text-accent-fg' : 'border-line-strong bg-raised text-fg-3',
                canEdit && !checked && 'hover:bg-hover hover:text-fg',
                !canEdit && 'cursor-default opacity-80',
              )}
            >
              <span aria-hidden>{l.level}</span>
            </button>
          )
        })}
      </div>
      <div className="mt-1 grid grid-cols-5 gap-1 text-center text-[11px] text-fg-3" aria-hidden>
        {INTENSITY_LEVELS.map((l) => (
          <span key={l.level} className={clsx(l.level === level && 'font-medium text-fg')}>
            {l.name}
          </span>
        ))}
      </div>
      {!canEdit && <p className="mt-1.5 text-[12px] text-fg-3">Only owners and admins can change the intensity.</p>}
      {chooser.pending === null && <FormError message={chooser.error} />}
    </div>
  )
}

/** The level in plain words, the plan-only warning and the budget reminder. Shared by the popover and Settings. */
export function IntensityDetails({ intensity, showName = true }: { intensity: Intensity; showName?: boolean }) {
  const lines = describeIntensity(intensity.effective)
  const high = intensity.level >= 4
  return (
    <div className="space-y-2 text-[12.5px] text-fg-2">
      {showName && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[13px] font-medium text-fg">{intensity.name}</span>
          {intensity.custom && (
            <Badge tone="warning" title="A setting was changed by hand after this level was chosen. Choose the level again to restore it.">
              Custom
            </Badge>
          )}
        </div>
      )}
      {intensity.custom && <p className="text-fg-3">Some settings were changed by hand, so this is not exactly the {intensity.name} preset. Choose a level to apply its preset again.</p>}
      <ul className="list-disc space-y-0.5 pl-4">
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {intensity.warning && high && (
        <p role="note" className="rounded-md bg-warning-subtle px-2 py-1.5 text-warning">
          {intensity.warning}
        </p>
      )}
      <p className="text-fg-3">{BUDGETS_STILL_APPLY}</p>
    </div>
  )
}
