import type { ReactNode } from 'react'

/** Wordmark used on every pre-login screen. */
export function Wordmark() {
  return (
    <div className="flex items-center gap-2.5">
      <span aria-hidden className="relative flex size-8 items-center justify-center rounded-md bg-accent-subtle">
        <span className="size-2.5 rotate-45 rounded-[2px] bg-accent" />
      </span>
      <span className="text-[13px] font-semibold tracking-wide text-fg-2">Company OS</span>
    </div>
  )
}

/**
 * The calm full-page frame for setup, sign-in and invitations: one centred column, the wordmark above a single
 * card, nothing else on screen. `wide` is for the wizard steps that need a little more room.
 */
export function AuthShell({ children, wide = false, footer }: { children: ReactNode; wide?: boolean; footer?: ReactNode }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-canvas px-4 py-10">
      <div className={wide ? 'w-full max-w-lg' : 'w-full max-w-sm'}>
        <div className="mb-6">
          <Wordmark />
        </div>
        <div className="rounded-md border border-line bg-surface p-6">{children}</div>
        {footer && <div className="mt-4 text-center text-[12px] text-fg-3">{footer}</div>}
      </div>
    </main>
  )
}
