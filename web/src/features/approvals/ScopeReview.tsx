import { Globe2 } from 'lucide-react'
import type { Approval } from '@/api/types'
import { Badge, SectionLabel } from '@/components/ui'
import { readReachabilityScope } from '@/lib/reachabilityApprovals'
import { titleCase } from '@/lib/format'

/**
 * The exact scope of a reachability or identity approval (M8): the registration pull request, a domain purchase with its
 * price, DNS changes, a mailbox, or the hosted-UI deploy. Everything is plain text; nothing the server did not send is
 * shown, and approving authorises exactly what is listed. Renders nothing for any other approval.
 */
export function ScopeReview({ approval, dense = false }: { approval: Approval; dense?: boolean }) {
  const scope = readReachabilityScope(approval)
  if (!scope) return null
  return (
    <div className="mt-2 space-y-2 rounded-md border border-line bg-raised p-2.5" role="group" aria-label={`Exact scope of ${approval.title}`} data-scope={scope.kind}>
      <div className="flex items-center gap-1.5">
        <Globe2 className="size-3.5 text-fg-3" aria-hidden />
        <SectionLabel>{scope.heading}</SectionLabel>
      </div>
      <p className="text-[12.5px] text-fg-2">{scope.effect}</p>
      {scope.rows.length > 0 && (
        <dl className="grid grid-cols-[minmax(7rem,10rem)_1fr] gap-x-3 gap-y-1 text-[12.5px]">
          {scope.rows.map((r) => (
            <div key={r.label} className="contents">
              <dt className="text-fg-3">{r.label}</dt>
              <dd className="min-w-0 break-words text-fg">{r.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {scope.file && (
        <div>
          <SectionLabel>
            File <span className="font-mono normal-case">{scope.file.path}</span>
          </SectionLabel>
          <pre className={`mt-1 overflow-auto rounded-sm border border-line bg-canvas p-2 text-[12px] leading-snug whitespace-pre-wrap break-words text-fg ${dense ? 'max-h-28' : 'max-h-60'}`}>{scope.file.content}</pre>
        </div>
      )}
      {scope.prBody && (
        <div>
          <SectionLabel>Pull request text</SectionLabel>
          <pre className={`mt-1 overflow-auto rounded-sm border border-line bg-canvas p-2 text-[12px] leading-snug whitespace-pre-wrap break-words text-fg ${dense ? 'max-h-24' : 'max-h-40'}`}>{scope.prBody}</pre>
        </div>
      )}
      {scope.dns.length > 0 && (
        <ul aria-label="DNS changes" className="space-y-1">
          {scope.dns.map((d, i) => (
            <li key={`${d.type}-${d.name}-${i}`} className="flex flex-wrap items-center gap-1.5 text-[12.5px]">
              <Badge tone={d.op === 'delete' ? 'danger' : d.op === 'create' || d.op === 'set' ? 'info' : 'warning'}>{titleCase(d.op)}</Badge>
              <span className="font-mono text-[12px] text-fg">{d.type}</span>
              <span className="font-mono text-[12px] break-all text-fg">{d.name}</span>
              {d.value && (
                <>
                  <span className="text-fg-3">→</span>
                  <span className="font-mono text-[12px] break-all text-fg-2">{d.value}</span>
                </>
              )}
              {d.ttl != null && <span className="text-fg-3">TTL {d.ttl}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
