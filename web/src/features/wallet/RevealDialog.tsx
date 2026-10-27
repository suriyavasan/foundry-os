import { ExternalLink, ShieldCheck } from 'lucide-react'
import { ApiError, errorMessage } from '@/api'
import { useRevealCard, type CardProviderId } from '@/api/wallet'
import { Button, Dialog, FormError } from '@/components/ui'
import { isSafeRevealUrl, PROVIDER_LABEL } from './walletLogic'

function needsRecentSignIn(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false
  const reason = typeof err.details === 'object' && err.details !== null ? (err.details as { reason?: unknown }).reason : undefined
  return err.status === 403 && (reason === 'recent_login_required' || reason === 'reauthentication_required')
}

/**
 * "Reveal card" for a human. The card number never passes through this app or the server: the server answers with a link to
 * the provider's own page and you read the number there. Asks the server only when you click, because the server wants a
 * recent sign-in and audits every reveal.
 */
export function RevealDialog({ companyId, cardId, provider, cardLabel, onClose }: { companyId: string; cardId: string; provider?: CardProviderId; cardLabel: string; onClose: () => void }) {
  const reveal = useRevealCard(companyId)
  const where = provider ? PROVIDER_LABEL[provider] : 'the provider'
  const link = reveal.data && isSafeRevealUrl(reveal.data.url) ? reveal.data : null
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      size="sm"
      title={`Reveal ${cardLabel}`}
      description={`The card number is shown only on ${where}'s own page. Foundry AI OS never sees it, stores it or sends it to an agent.`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          {link ? (
            <a
              href={link.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-8 items-center gap-2 rounded-md border border-transparent bg-accent px-3 text-[13px] font-medium text-fg-on-accent hover:bg-accent-hover"
            >
              <ExternalLink className="size-3.5" aria-hidden />
              Open on {PROVIDER_LABEL[link.provider]}
            </a>
          ) : (
            <Button variant="primary" icon={<ShieldCheck className="size-3.5" />} loading={reveal.isPending} onClick={() => reveal.mutate(cardId)}>
              {reveal.isError ? 'Try again' : 'Get the link'}
            </Button>
          )}
        </>
      }
    >
      <div className="space-y-3 text-[13px] text-fg-2">
        {link ? (
          <>
            <p>{link.note}</p>
            <p className="rounded-md border border-line bg-raised px-3 py-2 text-[12.5px] text-fg-3">
              The link opens {where} in a new tab. Copy the number there, pay, then come back and tell us if the payment was not seen automatically.
            </p>
          </>
        ) : reveal.data ? (
          <FormError message="The server returned an address that is not a plain https link, so it was not opened." />
        ) : (
          <p>For safety the server asks for a recent sign-in before it points you to a card, and records that you looked (never the number).</p>
        )}
        {reveal.isError && (
          <FormError
            message={needsRecentSignIn(reveal.error) ? 'Sign in again (you must have signed in within the last few minutes), then try again.' : errorMessage(reveal.error, 'The card could not be revealed')}
          />
        )}
      </div>
    </Dialog>
  )
}
