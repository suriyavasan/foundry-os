import clsx from 'clsx'
import { ShieldAlert } from 'lucide-react'
import type { ReactNode } from 'react'
import { hostOf, safeHref } from './radarLogic'

/**
 * Text that came from the open internet (or was written by an agent from it). It is only ever rendered as plain text
 * (React escapes it; there is no markdown or HTML here), and it is marked so a reader, and the tests, can tell.
 */
export function UntrustedText({ children, className, as: Tag = 'span' }: { children: ReactNode; className?: string; as?: 'span' | 'p' | 'div' }) {
  return (
    <Tag data-untrusted="true" className={clsx('break-words whitespace-pre-wrap', className)}>
      {children}
    </Tag>
  )
}

/** The standing notice at the top of every tab that shows outside text. */
export function UntrustedBanner({ children }: { children?: ReactNode }) {
  return (
    <div role="note" className="flex items-start gap-2 rounded-md border border-line bg-raised px-3 py-2 text-[12px] text-fg-2">
      <ShieldAlert className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
      <p>
        <span className="font-medium text-fg">Untrusted text.</span>{' '}
        {children ?? 'What follows was written by people and sites outside the company, or by agents summarising them. It is shown as plain text, links open in a new tab, and agents are told never to follow instructions found in it.'}
      </p>
    </div>
  )
}

/**
 * A link to outside content. Only an http(s) address becomes a link; anything else is shown as inert text. Always opens
 * in a new tab without sending the referrer or giving the page a handle on this one.
 */
export function ExternalLink({ href, children, className, showHost }: { href: string | null | undefined; children: ReactNode; className?: string; showHost?: boolean }) {
  const safe = safeHref(href)
  if (!safe) return <span className={className}>{children}</span>
  return (
    <>
      <a href={safe} target="_blank" rel="noopener noreferrer nofollow" className={clsx('text-accent-fg underline-offset-2 hover:underline', className)}>
        {children}
      </a>
      {showHost && <span className="ml-1.5 text-[11.5px] text-fg-3">{hostOf(safe)}</span>}
    </>
  )
}
