import clsx from 'clsx'
import { Loader2 } from 'lucide-react'
import type { ComponentProps, ReactNode } from 'react'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md'

const variants: Record<ButtonVariant, string> = {
  primary: 'bg-accent text-fg-on-accent hover:bg-accent-hover border-transparent',
  secondary: 'bg-raised text-fg border-line-strong hover:bg-hover',
  ghost: 'bg-transparent text-fg-2 border-transparent hover:bg-hover hover:text-fg',
  danger: 'bg-danger text-fg-on-accent hover:bg-danger-hover border-transparent',
}
const sizes: Record<ButtonSize, string> = {
  sm: 'h-7 px-2.5 text-[12.5px] gap-1.5',
  md: 'h-8 px-3 text-[13px] gap-2',
}

export interface ButtonProps extends ComponentProps<'button'> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  icon?: ReactNode
}

export function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  icon,
  disabled,
  className,
  children,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={clsx(
        'inline-flex shrink-0 items-center justify-center rounded-md border font-medium whitespace-nowrap transition-colors',
        'disabled:opacity-50 disabled:hover:bg-inherit',
        variants[variant],
        sizes[size],
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 className="cos-spin size-3.5" aria-hidden /> : icon}
      {children}
    </button>
  )
}

export function IconButton({
  label,
  className,
  children,
  ...rest
}: Omit<ComponentProps<'button'>, 'aria-label'> & { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={clsx(
        'inline-flex size-8 items-center justify-center rounded-md text-fg-2 transition-colors hover:bg-hover hover:text-fg disabled:opacity-50',
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  )
}
