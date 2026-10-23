import { ShieldAlert, ShieldCheck } from 'lucide-react'
import type { Approval } from '@/api/types'
import { Badge, SectionLabel } from '@/components/ui'
import { readDlpFindings, readOutbound } from '@/lib/approvalLanes'
import { titleCase } from '@/lib/format'

const SEVERITY_TONE = { high: 'danger', medium: 'warning', low: 'neutral' } as const

/**
 * The exact outbound content under review and the DLP scan result. Read-only and informational for the human: it is
 * rendered as plain text (never HTML), and a finding shows what kind of thing was detected, never the matched text.
 * Renders nothing for an approval that carries neither.
 */
export function OutboundReview({ approval, dense = false }: { approval: Approval; dense?: boolean }) {
  const out = readOutbound(approval)
  const findings = readDlpFindings(approval)
  const scanned = Array.isArray(approval.dlp_findings)
  if (!out && findings.length === 0 && !scanned) return null
  const meta = [out?.channel ? titleCase(out.channel) : null, out?.to ? `to ${out.to}` : null].filter(Boolean).join(' ')
  return (
    <div className="mt-2 space-y-2 rounded-md border border-line bg-raised p-2.5" aria-label={`Outbound content for ${approval.title}`} role="group">
      {out && (
        <div>
          <SectionLabel>Exact outbound content{meta ? ` · ${meta}` : ''}</SectionLabel>
          {out.subject && <div className="mt-1 text-[12.5px] font-medium text-fg">{out.subject}</div>}
          <pre className={`mt-1 overflow-auto rounded-sm border border-line bg-canvas p-2 text-[12px] leading-snug whitespace-pre-wrap break-words text-fg ${dense ? 'max-h-28' : 'max-h-60'}`}>{out.body}</pre>
        </div>
      )}
      <div>
        <SectionLabel>DLP scan</SectionLabel>
        {findings.length === 0 ? (
          <p className="mt-1 flex items-center gap-1.5 text-[12.5px] text-fg-2">
            <ShieldCheck className="size-3.5 text-success" aria-hidden /> No findings.
          </p>
        ) : (
          <ul className="mt-1 space-y-1">
            {findings.map((f, i) => (
              <li key={`${f.kind}-${i}`} className="flex flex-wrap items-center gap-1.5 text-[12.5px]">
                <ShieldAlert className="size-3.5 text-warning" aria-hidden />
                <Badge tone={f.severity ? SEVERITY_TONE[f.severity] : 'neutral'}>{titleCase(f.kind)}</Badge>
                {f.severity && <span className="text-fg-3">{f.severity} severity</span>}
                {f.count != null && f.count > 1 && <span className="text-fg-3">{f.count} matches</span>}
                {f.description && <span className="text-fg-2">{f.description}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
