/** Which artifacts get an inline text preview: text/*, markdown and JSON only. Everything else downloads. */
export function isPreviewable(mimeType: string, kind?: string): boolean {
  const m = mimeType.toLowerCase().split(';')[0]!.trim()
  if (m.startsWith('text/')) return true
  if (m === 'application/json' || m.endsWith('+json')) return true
  if (m === 'application/markdown' || m === 'text/markdown') return true
  // "link" artifacts are small text pointers.
  return kind === 'link' && m === 'text/uri-list'
}

/** Pretty-print JSON; leave everything else untouched. Never throws. */
export function prettyPreview(text: string, mimeType: string): string {
  const m = mimeType.toLowerCase()
  if (m.includes('json')) {
    try {
      return JSON.stringify(JSON.parse(text), null, 2)
    } catch {
      return text
    }
  }
  return text
}
