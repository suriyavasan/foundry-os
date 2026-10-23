import { useMemo } from 'react'
import { qrMatrix, qrPath } from '@/lib/qr'

const QUIET = 4

/** A QR code drawn in the browser (no network, nothing leaves the page). Dark on light in every theme. */
export function QrCode({ value, size = 168, label }: { value: string; size?: number; label: string }) {
  const { path, dim } = useMemo(() => {
    const m = qrMatrix(value)
    return { path: qrPath(m), dim: m.length + QUIET * 2 }
  }, [value])
  return (
    <svg role="img" aria-label={label} width={size} height={size} viewBox={`${-QUIET} ${-QUIET} ${dim} ${dim}`} shapeRendering="crispEdges" className="rounded-md" style={{ background: 'var(--qr-bg)' }}>
      <path d={path} fill="var(--qr-fg)" />
    </svg>
  )
}
