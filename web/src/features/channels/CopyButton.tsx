import { Check, Copy } from 'lucide-react'
import { useState } from 'react'
import { Button, useToast } from '@/components/ui'

/** Copies a value to the clipboard. When the browser refuses, it says so (the value stays visible to select by hand). */
export function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false)
  const toast = useToast()
  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error('Could not copy. Select the text and copy it by hand.')
    }
  }
  return (
    <Button size="sm" variant="ghost" aria-label={label} icon={copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />} onClick={() => void copy()}>
      {copied ? 'Copied' : 'Copy'}
    </Button>
  )
}

/** A read-only value (a code, a URL, a token) with a copy button. */
export function CopyField({ label, value, mono = true }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <p className="mb-1 text-[12px] font-medium text-fg-2">{label}</p>
      <div className="flex items-center gap-2 rounded-md border border-line-strong bg-canvas py-1 pr-1 pl-2.5">
        <span className={`min-w-0 flex-1 text-[12.5px] break-all text-fg ${mono ? 'font-mono' : ''}`}>{value}</span>
        <CopyButton value={value} label={`Copy ${label}`} />
      </div>
    </div>
  )
}
