import clsx from 'clsx'
import { useId, type ComponentProps, type ReactNode } from 'react'

const control =
  'w-full rounded-md border border-line-strong bg-canvas px-2.5 text-[13px] text-fg placeholder:text-fg-3 ' +
  'transition-colors hover:border-fg-3 focus:border-accent focus:outline-none focus-visible:outline-none ' +
  'focus:ring-1 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-60 aria-[invalid=true]:border-danger aria-[invalid=true]:ring-danger'

export function Input({ className, invalid, ...rest }: ComponentProps<'input'> & { invalid?: boolean }) {
  return <input aria-invalid={invalid || undefined} className={clsx(control, 'h-8', className)} {...rest} />
}

export function Textarea({ className, invalid, ...rest }: ComponentProps<'textarea'> & { invalid?: boolean }) {
  return (
    <textarea
      aria-invalid={invalid || undefined}
      className={clsx(control, 'min-h-16 resize-y py-1.5 leading-snug', className)}
      {...rest}
    />
  )
}

export function Select({ className, invalid, children, ...rest }: ComponentProps<'select'> & { invalid?: boolean }) {
  return (
    <select aria-invalid={invalid || undefined} className={clsx(control, 'h-8 pr-6', className)} {...rest}>
      {children}
    </select>
  )
}

export function Label({ className, ...rest }: ComponentProps<'label'>) {
  return <label className={clsx('mb-1 block text-[12px] font-medium text-fg-2', className)} {...rest} />
}

export interface FieldProps {
  label: string
  /** Render-prop receives ids to wire up the control. */
  children: (ids: { id: string; invalid: boolean; describedBy: string | undefined }) => ReactNode
  error?: string | null
  hint?: ReactNode
  required?: boolean
  className?: string
}

/** Label + control + hint + error text, with accessible wiring. */
export function Field({ label, children, error, hint, required, className }: FieldProps) {
  const id = useId()
  const hintId = `${id}-hint`
  const errId = `${id}-err`
  const describedBy = [hint ? hintId : null, error ? errId : null].filter(Boolean).join(' ') || undefined
  return (
    <div className={className}>
      <Label htmlFor={id}>
        {label}
        {required && (
          <span className="ml-0.5 text-danger" aria-hidden>
            *
          </span>
        )}
      </Label>
      {children({ id, invalid: !!error, describedBy })}
      {hint && !error && (
        <p id={hintId} className="mt-1 text-[12px] text-fg-3">
          {hint}
        </p>
      )}
      {error && (
        <p id={errId} role="alert" className="mt-1 text-[12px] text-danger">
          {error}
        </p>
      )}
    </div>
  )
}

/** Inline form-level error banner. */
export function FormError({ message }: { message: string | null | undefined }) {
  if (!message) return null
  return (
    <div role="alert" className="rounded-md border border-danger/40 bg-danger-subtle px-3 py-2 text-[13px] text-danger">
      {message}
    </div>
  )
}
