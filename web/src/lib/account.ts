import type { ApiToken, SessionInfo, TokenScope } from '@/api/types'

export const TOKEN_SCOPES: Array<{ value: TokenScope; label: string; help: string }> = [
  { value: 'read', label: 'Read', help: 'Look at anything you can see. Cannot change anything.' },
  { value: 'write', label: 'Write', help: 'Read, and create or change work: tasks, notes, artifacts.' },
  { value: 'admin', label: 'Admin', help: 'Everything you can do, including team and policy. Treat it like your password.' },
]

export const TOKEN_NAME_MAX = 80

export function validateTokenName(name: string): string | null {
  const n = name.trim()
  if (!n) return 'Name the token so you can recognise it later'
  if (n.length > TOKEN_NAME_MAX) return `Use at most ${TOKEN_NAME_MAX} characters`
  return null
}

/** A short, human reading of a user-agent string (never trusted for anything). */
export function describeUserAgent(ua: string | null | undefined): string {
  if (!ua) return 'Unknown device'
  const browser = /Edg\//.test(ua) ? 'Edge' : /Chrome\//.test(ua) ? 'Chrome' : /Firefox\//.test(ua) ? 'Firefox' : /Safari\//.test(ua) ? 'Safari' : /curl|python|node|httpx/i.test(ua) ? 'Command line' : 'Browser'
  const os = /iPhone|iPad/.test(ua) ? 'iOS' : /Android/.test(ua) ? 'Android' : /Mac OS X|Macintosh/.test(ua) ? 'macOS' : /Windows/.test(ua) ? 'Windows' : /Linux/.test(ua) ? 'Linux' : null
  return os ? `${browser} on ${os}` : browser
}

/** The current session first, then the most recently used. */
export function sortSessions(list: SessionInfo[]): SessionInfo[] {
  return [...list].sort((a, b) => Number(b.current) - Number(a.current) || (b.last_seen_at ?? b.created_at).localeCompare(a.last_seen_at ?? a.created_at))
}

export const activeTokens = (list: ApiToken[]): ApiToken[] => list.filter((t) => !t.revoked_at)
