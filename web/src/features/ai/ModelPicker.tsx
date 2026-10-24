import clsx from 'clsx'
import { useId, useState } from 'react'
import { Input } from '@/components/ui'
import { filterModels, type ModelOption } from '@/lib/aiSetup'

/**
 * The model field. With a list from the provider (`GET /model-providers/{id}/models`) it is a searchable list; without one
 * (endpoint missing, empty, or no provider yet) it is a plain text field with clickable examples, so a model the list
 * does not know can always be typed. Either way the value is free text: the "model available" check has the last word.
 */
export function ModelPicker({
  id,
  value,
  onChange,
  options,
  loading = false,
  suggestions = [],
  invalid,
  describedBy,
  disabled,
}: {
  id: string
  value: string
  onChange: (v: string) => void
  options: ModelOption[]
  loading?: boolean
  suggestions?: string[]
  invalid?: boolean
  describedBy?: string
  disabled?: boolean
}) {
  const listId = useId()
  const [open, setOpen] = useState(false)
  const [cursor, setCursor] = useState(0)
  const shown = filterModels(options, options.some((o) => o.id === value) ? '' : value)
  const active = Math.min(cursor, Math.max(0, shown.length - 1))
  const hasList = options.length > 0

  function pick(m: ModelOption) {
    onChange(m.id)
    setOpen(false)
  }

  return (
    <div className="relative">
      <Input
        id={id}
        className="h-10 font-mono text-[13px]"
        value={value}
        invalid={invalid}
        disabled={disabled}
        aria-describedby={describedBy}
        autoComplete="off"
        spellCheck={false}
        autoCapitalize="none"
        placeholder={loading ? 'Loading models…' : hasList ? 'Search or type a model' : 'Type the model name'}
        role={hasList ? 'combobox' : undefined}
        aria-expanded={hasList ? open : undefined}
        aria-controls={hasList ? listId : undefined}
        aria-autocomplete={hasList ? 'list' : undefined}
        aria-activedescendant={hasList && open && shown[active] ? `${listId}-${active}` : undefined}
        onChange={(e) => {
          onChange(e.target.value)
          setOpen(true)
          setCursor(0)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          if (!hasList) return
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setOpen(true)
            setCursor((active + 1) % Math.max(1, shown.length))
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setCursor((active - 1 + shown.length) % Math.max(1, shown.length))
          } else if (e.key === 'Enter' && open && shown[active]) {
            e.preventDefault()
            pick(shown[active]!)
          } else if (e.key === 'Escape' && open) {
            e.stopPropagation()
            setOpen(false)
          }
        }}
      />
      {hasList && open && shown.length > 0 && (
        <ul id={listId} role="listbox" aria-label="Models" className="absolute z-20 mt-1 max-h-56 w-full overflow-y-auto rounded-md border border-line-strong bg-surface p-1 shadow-pop">
          {shown.map((m, i) => (
            <li
              key={m.id}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={m.id === value}
              onMouseDown={(e) => {
                e.preventDefault()
                pick(m)
              }}
              onMouseMove={() => setCursor(i)}
              className={clsx('flex cursor-pointer items-center justify-between gap-3 rounded-sm px-2 py-1.5 font-mono text-[12.5px]', i === active ? 'bg-hover text-fg' : 'text-fg-2')}
            >
              <span className="min-w-0 truncate">{m.id}</span>
              {m.context_length ? <span className="shrink-0 font-sans text-[11px] text-fg-3">{Math.round(m.context_length / 1000)}K context</span> : null}
            </li>
          ))}
        </ul>
      )}
      {!hasList && suggestions.length > 0 && (
        <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[12px] text-fg-3">
          Examples:
          {suggestions.map((s) => (
            <button key={s} type="button" disabled={disabled} onClick={() => onChange(s)} className="rounded-sm border border-line bg-raised px-1.5 py-0.5 font-mono text-[11.5px] text-fg-2 transition-colors hover:bg-hover hover:text-fg">
              {s}
            </button>
          ))}
        </p>
      )}
    </div>
  )
}
