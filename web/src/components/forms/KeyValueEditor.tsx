import { Plus, X } from 'lucide-react'
import { Button, IconButton, Input } from '@/components/ui'
import { looksLikeSecretKey } from '@/lib/secrets'

export interface KVRow {
  key: string
  value: string
  /** Rows loaded with non-string values are edited (and saved) as JSON. */
  json: boolean
}

export function rowsFromObject(obj: Record<string, unknown> | null | undefined): KVRow[] {
  return Object.entries(obj ?? {}).map(([key, v]) =>
    typeof v === 'string' ? { key, value: v, json: false } : { key, value: JSON.stringify(v), json: true },
  )
}

export interface RowsResult {
  value: Record<string, unknown>
  /** error message per row index */
  errors: Record<number, string>
}

export function rowsToObject(rows: KVRow[]): RowsResult {
  const value: Record<string, unknown> = {}
  const errors: Record<number, string> = {}
  const seen = new Set<string>()
  rows.forEach((r, i) => {
    const key = r.key.trim()
    if (!key) {
      if (r.value.trim()) errors[i] = 'Key is required'
      return
    }
    if (seen.has(key)) {
      errors[i] = `Duplicate key "${key}"`
      return
    }
    seen.add(key)
    if (looksLikeSecretKey(key)) {
      errors[i] = 'Looks like a secret. Secrets are not allowed in runtime config.'
      return
    }
    if (r.json) {
      try {
        value[key] = JSON.parse(r.value)
      } catch {
        errors[i] = 'Value must be valid JSON'
      }
    } else {
      value[key] = r.value
    }
  })
  return { value, errors }
}

/** Key/value editor for non-secret runtime config. */
export function KeyValueEditor({
  rows,
  onChange,
  disabled,
  errors = {},
}: {
  rows: KVRow[]
  onChange: (rows: KVRow[]) => void
  disabled?: boolean
  errors?: Record<number, string>
}) {
  const update = (i: number, patch: Partial<KVRow>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  return (
    <div className="space-y-2">
      {rows.length === 0 && <p className="text-[12.5px] text-fg-3">No runtime config set.</p>}
      {rows.map((r, i) => (
        <div key={i}>
          <div className="flex gap-2">
            <Input
              aria-label={`Config key ${i + 1}`}
              placeholder="key"
              className="max-w-[12rem] font-mono"
              value={r.key}
              disabled={disabled}
              invalid={!!errors[i]}
              onChange={(e) => update(i, { key: e.target.value })}
            />
            <Input
              aria-label={`Config value ${i + 1}`}
              placeholder={r.json ? 'JSON value' : 'value'}
              className="font-mono"
              value={r.value}
              disabled={disabled}
              invalid={!!errors[i]}
              onChange={(e) => update(i, { value: e.target.value })}
            />
            <IconButton
              label={`Remove config ${r.key || i + 1}`}
              disabled={disabled}
              onClick={() => onChange(rows.filter((_, j) => j !== i))}
            >
              <X className="size-3.5" />
            </IconButton>
          </div>
          {errors[i] && (
            <p role="alert" className="mt-1 text-[12px] text-danger">
              {errors[i]}
            </p>
          )}
        </div>
      ))}
      {!disabled && (
        <Button
          size="sm"
          icon={<Plus className="size-3.5" />}
          onClick={() => onChange([...rows, { key: '', value: '', json: false }])}
        >
          Add entry
        </Button>
      )}
    </div>
  )
}
