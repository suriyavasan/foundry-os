const LAST_COMPANY_KEY = 'company-os.last-company'
const THEME_KEY = 'company-os.theme'

export type Theme = 'dark' | 'light'

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}
function safeSet(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* storage unavailable (private mode); preference is session-only */
  }
}

export const getLastCompanyId = () => safeGet(LAST_COMPANY_KEY)
export const setLastCompanyId = (id: string) => safeSet(LAST_COMPANY_KEY, id)

export function getStoredTheme(): Theme {
  return safeGet(THEME_KEY) === 'light' ? 'light' : 'dark'
}
export function storeTheme(theme: Theme) {
  safeSet(THEME_KEY, theme)
}

/* Founder's notes: which AI Co-founder replies this browser has already opened. */
const seenKey = (cid: string) => `company-os.notes-seen.${cid}`

export function readSeenReplies(cid: string): Record<string, string> {
  const raw = safeGet(seenKey(cid))
  if (!raw) return {}
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    return Object.fromEntries(Object.entries(parsed).filter((e): e is [string, string] => typeof e[1] === 'string'))
  } catch {
    return {}
  }
}

export function writeSeenReplies(cid: string, seen: Record<string, string>) {
  safeSet(seenKey(cid), JSON.stringify(seen))
}
