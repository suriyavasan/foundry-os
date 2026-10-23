import { ChevronRight } from 'lucide-react'
import { Link } from 'react-router'
import type { ReactNode } from 'react'

export interface Crumb {
  label: string
  to?: string
}

/** Standard page chrome: breadcrumb/title/description + actions, then the content area. */
export function Page({
  title,
  description,
  breadcrumbs,
  actions,
  children,
}: {
  title: ReactNode
  description?: ReactNode
  breadcrumbs?: Crumb[]
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <div>
      <header className="border-b border-line bg-surface px-4 py-3 md:px-6">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div className="min-w-0">
            {breadcrumbs && breadcrumbs.length > 0 && (
              <nav aria-label="Breadcrumb" className="mb-0.5 flex items-center gap-1 text-[12px] text-fg-3">
                {breadcrumbs.map((c, i) => (
                  <span key={i} className="flex items-center gap-1">
                    {i > 0 && <ChevronRight className="size-3" aria-hidden />}
                    {c.to ? (
                      <Link to={c.to} className="hover:text-fg hover:underline">
                        {c.label}
                      </Link>
                    ) : (
                      <span>{c.label}</span>
                    )}
                  </span>
                ))}
              </nav>
            )}
            <h1 className="text-[17px] leading-6 font-semibold tracking-tight text-fg">{title}</h1>
            {description && <p className="mt-0.5 max-w-3xl text-[13px] text-fg-3">{description}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      </header>
      <div className="mx-auto max-w-[1400px] px-4 py-5 md:px-6">{children}</div>
    </div>
  )
}
