import { Card, CardBody, CardHeader } from '@/components/ui'
import { ChannelsManager } from './ChannelsManager'

/** Settings, Chat apps: connect Telegram, Discord or WhatsApp, link accounts, choose what each chat is sent. */
export function ChatAppsSection({ companyId }: { companyId: string }) {
  return (
    <Card aria-label="Chat apps">
      <CardHeader title="Chat apps" description="Talk to your company from Telegram, Discord or WhatsApp: send instructions, answer questions, decide approvals" />
      <CardBody>
        <ChannelsManager companyId={companyId} />
      </CardBody>
    </Card>
  )
}
