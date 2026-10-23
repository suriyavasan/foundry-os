import { Plus, X } from 'lucide-react'
import { useState } from 'react'
import { Button, IconButton, Input } from '@/components/ui'

/** Add/remove list of short strings (constraints, tool permissions). */
export function StringListEditor({
  values,
  onChange,
  placeholder,
  addLabel = 'Add',
  inputLabel,
  disabled,
  emptyText = 'None added.',
}: {
  values: string[]
  onChange: (next: string[]) => void
  placeholder?: string
  addLabel?: string
  inputLabel: string
  disabled?: boolean
  emptyText?: string
}) {
  const [draft, setDraft] = useState('')

  const add = () => {
    const v = draft.trim()
    if (!v || values.includes(v)) {
      setDraft('')
      return
    }
    onChange([...values, v])
    setDraft('')
  }

  return (
    <div className="space-y-2">
      {values.length === 0 ? (
        <p className="text-[12.5px] text-fg-3">{emptyText}</p>
      ) : (
        <ul className="space-y-1">
          {values.map((v, i) => (
            <li
              key={`${v}-${i}`}
              className="flex items-start gap-2 rounded-md border border-line bg-raised py-1 pr-1 pl-2.5 text-[13px]"
            >
              <span className="min-w-0 flex-1 py-0.5 break-words">{v}</span>
              <IconButton
                label={`Remove ${v}`}
                className="size-6"
                disabled={disabled}
                onClick={() => onChange(values.filter((_, j) => j !== i))}
              >
                <X className="size-3.5" />
              </IconButton>
            </li>
          ))}
        </ul>
      )}
      {!disabled && (
        <div className="flex gap-2">
          <Input
            aria-label={inputLabel}
            value={draft}
            placeholder={placeholder}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                add()
              }
            }}
          />
          <Button onClick={add} disabled={!draft.trim()} icon={<Plus className="size-3.5" />}>
            {addLabel}
          </Button>
        </div>
      )}
    </div>
  )
}
