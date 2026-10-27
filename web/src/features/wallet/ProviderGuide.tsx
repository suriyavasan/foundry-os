import { CheckCircle2, Circle } from 'lucide-react'
import type { CardProviderInfo } from '@/api/wallet'
import { Badge, SectionLabel } from '@/components/ui'

/**
 * The provider's funding guide: the exact steps only the owner can do (KYC, funding, OTP), the honest eligibility text and
 * what was and was not verified against the real system. Everything is text from the server. When `checked` is given, each
 * step has a tickbox so the person can track their own progress (kept in the browser, not sent anywhere).
 */
export function ProviderGuide({
  provider,
  guideUpdatedOn,
  checked,
  onToggle,
}: {
  provider: CardProviderInfo
  guideUpdatedOn?: string
  checked?: ReadonlySet<number>
  onToggle?: (index: number) => void
}) {
  const interactive = !!checked && !!onToggle
  return (
    <div className="space-y-3" role="group" aria-label={`${provider.label} funding guide`}>
      <p className="text-[12.5px] text-fg-2">{provider.eligibility}</p>
      {provider.steps.length > 0 && (
        <div>
          <SectionLabel>What you do</SectionLabel>
          <ol className="mt-1.5 space-y-2">
            {provider.steps.map((s, i) => {
              const done = checked?.has(i) ?? false
              return (
                <li key={`${i}-${s.title}`} className="flex gap-2.5 rounded-md border border-line bg-raised px-3 py-2">
                  {interactive ? (
                    <input
                      type="checkbox"
                      id={`guide-step-${provider.id}-${i}`}
                      className="mt-0.5 size-3.5 shrink-0 accent-(--accent)"
                      checked={done}
                      onChange={() => onToggle(i)}
                      aria-label={`Done: ${s.title}`}
                    />
                  ) : done ? (
                    <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-success" aria-hidden />
                  ) : (
                    <Circle className="mt-0.5 size-3.5 shrink-0 text-fg-3" aria-hidden />
                  )}
                  <div className="min-w-0">
                    <p className="flex flex-wrap items-center gap-1.5 text-[13px] font-medium text-fg">
                      <span>
                        {i + 1}. {s.title}
                      </span>
                      {s.owner_only && <Badge tone="warning">Only you can do this</Badge>}
                    </p>
                    {s.body && <p className="mt-0.5 text-[12.5px] whitespace-pre-wrap text-fg-2">{s.body}</p>}
                  </div>
                </li>
              )
            })}
          </ol>
        </div>
      )}
      <div>
        <SectionLabel>What was and was not verified</SectionLabel>
        <p className="mt-1 text-[12.5px] text-fg-3">{provider.verification}</p>
        {guideUpdatedOn && <p className="mt-1 text-[12px] text-fg-3">Guide last reviewed {guideUpdatedOn}. Rules change: check the provider's own page before you rely on it.</p>}
      </div>
    </div>
  )
}
