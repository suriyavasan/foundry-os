import clsx from 'clsx'
import type { ComponentProps } from 'react'

export function Table({ className, ...rest }: ComponentProps<'table'>) {
  return (
    <div className="overflow-x-auto">
      <table className={clsx('w-full border-collapse text-left text-[13px]', className)} {...rest} />
    </div>
  )
}
export function THead(props: ComponentProps<'thead'>) {
  return <thead {...props} />
}
export function TBody(props: ComponentProps<'tbody'>) {
  return <tbody {...props} />
}
export function TR({ className, interactive, ...rest }: ComponentProps<'tr'> & { interactive?: boolean }) {
  return (
    <tr
      className={clsx('border-b border-line last:border-b-0', interactive && 'hover:bg-hover', className)}
      {...rest}
    />
  )
}
export function TH({ className, ...rest }: ComponentProps<'th'>) {
  return (
    <th
      scope="col"
      className={clsx(
        'h-8 border-b border-line bg-raised px-3 text-[11.5px] font-medium tracking-wide whitespace-nowrap text-fg-3 uppercase',
        className,
      )}
      {...rest}
    />
  )
}
export function TD({ className, ...rest }: ComponentProps<'td'>) {
  return <td className={clsx('h-9 px-3 py-1.5 align-middle text-fg', className)} {...rest} />
}
