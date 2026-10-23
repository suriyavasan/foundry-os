import { Check, Copy, ExternalLink, QrCode as QrIcon } from 'lucide-react'
import { useState } from 'react'
import { IconButton, useToast } from '@/components/ui'
import { QrCode } from '@/components/qr/QrCode'
import { canRenderQr } from '@/lib/reachability'

/** Copies `value`; the icon turns into a check for a moment. Says so plainly if the browser refuses. */
export function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false)
  const toast = useToast()
  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error('Could not copy. Select the address and copy it by hand.')
    }
  }
  return (
    <IconButton label={copied ? 'Copied' : label} onClick={() => void copy()} className="size-7">
      {copied ? <Check className="size-3.5 text-success" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
    </IconButton>
  )
}

/** One address: the text, copy, open, and (optionally) a QR code to scan from another device. */
export function UrlRow({ label, url, qr = false, qrOpen = false, note }: { label: string; url: string; qr?: boolean; qrOpen?: boolean; note?: string }) {
  const [showQr, setShowQr] = useState(qrOpen)
  const drawable = qr && canRenderQr(url)
  return (
    <div className="rounded-md border border-line bg-raised px-3 py-2">
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <div className="text-[11.5px] text-fg-3">{label}</div>
          <div className="truncate font-mono text-[12.5px] text-fg" title={url}>
            {url}
          </div>
        </div>
        {drawable && (
          <IconButton label={showQr ? `Hide QR code for ${url}` : `Show QR code for ${url}`} aria-expanded={showQr} onClick={() => setShowQr((s) => !s)} className="size-7">
            <QrIcon className="size-3.5" aria-hidden />
          </IconButton>
        )}
        <CopyButton value={url} label={`Copy ${label.toLowerCase()} address`} />
        <a href={url} target="_blank" rel="noopener noreferrer" aria-label={`Open ${url} in a new tab`} className="inline-flex size-7 items-center justify-center rounded-md text-fg-2 transition-colors hover:bg-hover hover:text-fg">
          <ExternalLink className="size-3.5" aria-hidden />
        </a>
      </div>
      {note && <p className="mt-1 text-[12px] text-fg-3">{note}</p>}
      {drawable && showQr && (
        <div className="mt-2 flex items-center gap-3">
          <QrCode value={url} size={136} label={`QR code for ${url}`} />
          <p className="max-w-xs text-[12px] text-fg-3">Scan with a phone on the same network to open this address.</p>
        </div>
      )}
    </div>
  )
}
