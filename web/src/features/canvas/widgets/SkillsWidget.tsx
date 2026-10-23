import { NotAvailable, Widget } from '@/components/ui'

/** Skills and the HR agent arrive in M4. No data is invented. */
export function SkillsWidget({ className }: { className?: string }) {
  return (
    <Widget title="Skills" subtitle="Capabilities" className={className}>
      <NotAvailable
        compact
        className="p-4"
        milestone="M4"
        description="The skill library and the HR agent that hires and equips employees arrive in M4."
      />
    </Widget>
  )
}
