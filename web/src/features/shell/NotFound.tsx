import { Compass } from 'lucide-react'
import { Link } from 'react-router'
import { getLastCompanyId } from '@/lib/storage'

export function NotFound({
  title = 'Page not found',
  description = 'The page you are looking for does not exist or you do not have access to it.',
  backTo,
  backLabel = 'Go home',
}: {
  title?: string
  description?: string
  backTo?: string
  backLabel?: string
}) {
  const home = backTo ?? '/'
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-1 px-6 text-center">
      <div className="mb-2 flex size-10 items-center justify-center rounded-md bg-neutral-subtle text-fg-2">
        <Compass className="size-5" aria-hidden />
      </div>
      <p className="text-[12px] font-medium tracking-wide text-fg-3 uppercase">404</p>
      <h1 className="text-[17px] font-semibold text-fg">{title}</h1>
      <p className="max-w-md text-[13px] text-fg-3">{description}</p>
      <Link
        to={home}
        className="mt-3 inline-flex h-8 items-center rounded-md border border-line-strong bg-raised px-3 text-[13px] font-medium text-fg hover:bg-hover"
      >
        {backLabel}
      </Link>
    </div>
  )
}

/** Top-level unknown route. */
export function NotFoundPage() {
  const last = getLastCompanyId()
  return (
    <div className="min-h-screen bg-canvas">
      <NotFound backTo={last ? `/c/${last}/command-center` : '/'} />
    </div>
  )
}
