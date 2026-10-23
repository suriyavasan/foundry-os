import type { Agent, ConnectedAccount, EmailProviderKind, Mailbox, MailboxStatus } from '@/api/types'
import type { Tone } from '@/components/ui/Badge'
import { accountState } from './reachability'

/** Company email identity (M8 §3). Pure helpers: provider requirements, mailbox rules and display. */

export interface EmailProviderInfo {
  kind: EmailProviderKind
  label: string
  summary: string
  /** Accounts that must be connected for this provider (all of them). */
  needs: string[]
}

export const EMAIL_PROVIDER_INFO: readonly EmailProviderInfo[] = [
  { kind: 'agentmail', label: 'AgentMail', summary: 'Inboxes made for agents. One API creates a mailbox per agent; no domain needed.', needs: ['agentmail'] },
  { kind: 'cloudflare_resend', label: 'Your domain: Cloudflare Email Routing + Resend', summary: 'Receive with Cloudflare Email Routing and send with Resend, on your own domain.', needs: ['cloudflare', 'resend'] },
  { kind: 'gmail', label: 'Gmail (existing account)', summary: 'Use a Gmail account you already own. You sign in once; no account is created for you.', needs: ['gmail'] },
  { kind: 'outlook', label: 'Outlook (existing account)', summary: 'Use an Outlook account you already own. You sign in once; no account is created for you.', needs: ['outlook'] },
]

export function emailProviderInfo(kind: string | null | undefined): EmailProviderInfo | null {
  return EMAIL_PROVIDER_INFO.find((p) => p.kind === kind) ?? null
}

export interface ProviderReadiness {
  ready: boolean
  /** The accounts still to connect, by provider id. */
  missing: string[]
  /** Accounts that are connected but failing their check. */
  failing: string[]
}

/** Whether the accounts a provider needs are connected. `accounts` null = the list is unavailable (never "ready"). */
export function emailProviderReadiness(info: EmailProviderInfo, accounts: ConnectedAccount[] | null | undefined): ProviderReadiness {
  const states = info.needs.map((n) => [n, accountState(accounts, n)] as const)
  return {
    ready: states.every(([, s]) => s === 'connected'),
    missing: states.filter(([, s]) => s === 'missing' || s === 'unknown').map(([n]) => n),
    failing: states.filter(([, s]) => s === 'invalid').map(([n]) => n),
  }
}

export const LOCAL_PART_PATTERN = /^[a-z0-9][a-z0-9._-]{0,63}$/

export function validateLocalPart(value: string): string | null {
  const v = value.trim().toLowerCase()
  if (!v) return 'Enter the part before the @'
  if (v.includes('@')) return 'Enter only the part before the @'
  if (!LOCAL_PART_PATTERN.test(v)) return 'Use lowercase letters, digits, dots, dashes and underscores, starting with a letter or digit (up to 64 characters)'
  return null
}

export const MAILBOX_STATUS_LABEL: Record<MailboxStatus, string> = { active: 'Active', pending: 'Pending', error: 'Error', disabled: 'Disabled' }
export const MAILBOX_STATUS_TONE: Record<MailboxStatus, Tone> = { active: 'success', pending: 'info', error: 'danger', disabled: 'neutral' }

/** Mailboxes in a stable order: the company's own first, then by address. */
export function sortMailboxes(boxes: Mailbox[]): Mailbox[] {
  const rank = (m: Mailbox) => (m.address.startsWith('company@') ? 0 : m.address.startsWith('cofounder@') ? 1 : 2)
  return [...boxes].sort((a, b) => rank(a) - rank(b) || a.address.localeCompare(b.address))
}

/** Agents that can own a mailbox: anyone not retired or rejected. */
export function mailboxOwners(agents: readonly Agent[]): Agent[] {
  return agents.filter((a) => a.status !== 'retired' && a.status !== 'rejected').sort((a, b) => a.name.localeCompare(b.name))
}
