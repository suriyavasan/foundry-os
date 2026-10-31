import type { ChatChannel, ChatChannelKind, ChatChannelStatus, ChatKindInfo, ChatLink, ChatNotifyPrefs } from '@/api/types'
import type { Tone } from '@/components/ui'

/** Pure helpers for the chat apps screens (M19), so the rules are tested without a DOM. */

export interface ChannelGuide {
  /** One line under the title. */
  blurb: string
  /** Where the secrets come from, step by step. */
  steps: string[]
  /** Links the steps mention (opened in a new tab). */
  links: Array<{ label: string; href: string }>
}

export const CHANNEL_GUIDES: Record<ChatChannelKind, ChannelGuide> = {
  telegram: {
    blurb: 'Message your company from Telegram. Foundry polls Telegram for you, so no public address is needed.',
    steps: [
      'Open Telegram and start a chat with @BotFather.',
      'Send /newbot and answer the two questions: a name, then a username that ends in "bot".',
      'BotFather replies with a token. Paste it below.',
    ],
    links: [{ label: 'Open @BotFather', href: 'https://t.me/BotFather' }],
  },
  discord: {
    blurb: 'Direct messages with a bot of your own. No public address and no privileged intents are needed.',
    steps: [
      'In the Discord Developer Portal, click New Application.',
      'Open Bot in the sidebar, click Reset Token and copy it. Paste it below.',
      'After connecting, invite the bot to a server you are in (scopes bot and applications.commands), so Discord lets you send it a direct message.',
    ],
    links: [{ label: 'Open the Developer Portal', href: 'https://discord.com/developers/applications' }],
  },
  whatsapp: {
    blurb: 'The official WhatsApp Business Cloud API. Meta calls a webhook on this server, so it needs a public https address.',
    steps: [
      'In Meta for Developers, create an app with the WhatsApp product, then open WhatsApp, API setup and copy the Phone number ID.',
      'In Business settings, create a System User, assign it the app and generate a permanent access token with the whatsapp_business_messaging permission.',
      'In the app settings, Basic, copy the App secret.',
      'After connecting, paste the webhook URL and the verify token into WhatsApp, Configuration, and subscribe to messages.',
    ],
    links: [{ label: 'Open Meta for Developers', href: 'https://developers.facebook.com/apps' }],
  },
}

export const KIND_LABEL: Record<string, string> = { telegram: 'Telegram', discord: 'Discord', whatsapp: 'WhatsApp' }
export const CHANNEL_KINDS: readonly ChatChannelKind[] = ['telegram', 'discord', 'whatsapp']
export const isChannelKind = (k: string): k is ChatChannelKind => (CHANNEL_KINDS as readonly string[]).includes(k)

export const OPEN_LABEL: Record<ChatChannelKind, string> = { telegram: 'Open Telegram', discord: 'Open Discord', whatsapp: 'Open WhatsApp' }

/** The things a linked chat can be told about. */
export const NOTIFY_FIELDS: ReadonlyArray<{ key: keyof ChatNotifyPrefs; label: string; hint: string }> = [
  { key: 'replies', label: 'Replies', hint: 'Answers to what you send' },
  { key: 'questions', label: 'Questions', hint: 'When an agent asks you something' },
  { key: 'approvals', label: 'Approvals', hint: 'Decisions waiting for a person' },
  { key: 'alerts', label: 'Alerts', hint: 'Budget, stalls and emergency stops' },
  { key: 'digest', label: 'Daily digest', hint: 'A short summary each day' },
]

export const STATUS_LABEL: Record<ChatChannelStatus, string> = { connecting: 'Connecting', active: 'Connected', error: 'Needs attention', disabled: 'Paused' }
export const STATUS_TONE: Record<ChatChannelStatus, Tone> = { connecting: 'info', active: 'success', error: 'danger', disabled: 'neutral' }

/** The https link the API returned, or null: nothing else is ever put in an href. */
export function safeHttpsUrl(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    const u = new URL(url)
    return u.protocol === 'https:' ? u.href : null
  } catch {
    return null
  }
}

/** Seconds left on a link code, never negative. */
export function secondsLeft(expiresAt: string, now: number = Date.now()): number {
  const t = Date.parse(expiresAt)
  return Number.isNaN(t) ? 0 : Math.max(0, Math.ceil((t - now) / 1000))
}

/** 125 -> "2:05". */
export function formatCountdown(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** What a person sends the bot, for the kinds without a deep link. */
export function linkMessage(kind: string, code: string): string {
  return kind === 'discord' ? `link ${code}` : `/start ${code}`
}

/** The person's links on a channel. */
export const linksOf = (channel: ChatChannel, userId: string | undefined): ChatLink[] => (userId ? channel.links.filter((l) => l.user_id === userId) : [])

/** A link that appeared after the code was issued: `known` is the set of the person's link ids at that moment. */
export function newLinkOf(channel: ChatChannel | undefined, userId: string | undefined, known: ReadonlySet<string>): ChatLink | undefined {
  return channel ? linksOf(channel, userId).find((l) => !known.has(l.id)) : undefined
}

export const linkName = (l: ChatLink): string => l.display_name || l.user_name || 'your account'

/** The kinds to draw: everything the API lists, plus any connected channel of a kind it does not list. Order follows the API. */
export interface KindCard {
  kind: string
  info: ChatKindInfo | null
  channel: ChatChannel | null
}

export function kindCards(available: ChatKindInfo[], channels: ChatChannel[]): KindCard[] {
  const byKind = new Map(channels.map((c) => [c.kind as string, c]))
  const out: KindCard[] = available.map((info) => ({ kind: info.kind, info, channel: byKind.get(info.kind) ?? null }))
  for (const c of channels) if (!available.some((a) => a.kind === c.kind)) out.push({ kind: c.kind, info: null, channel: c })
  return out
}

/** WhatsApp only allows free-form replies within 24 hours of the person's last message. */
export const WHATSAPP_WINDOW_NOTE = 'WhatsApp only lets Foundry reply within 24 hours of your last message. After that, send any message to the number to receive updates again.'
