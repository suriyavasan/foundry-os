import { useState } from 'react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { FreelancersTab } from './FreelancersTab'
import { HumanWorkTab } from './HumanWorkTab'

type TabName = 'human-work' | 'freelancers'

/**
 * `?panel=people-ops` (M22): the work HR and the AI employees hand to people. "Human work" is what an employee cannot do itself (a
 * signature, a call, an errand); "Freelancers" are jobs HR drafted for a marketplace, which a person approves, posts and records.
 * Foundry never posts, hires or pays on a marketplace.
 */
export function PeopleOpsPanel() {
  const { companyId } = useCanvas()
  const [tab, setTab] = useState<TabName>('human-work')
  return (
    <PanelFrame title="People ops" subtitle="What AI employees need people for, and the freelancers HR brings in">
      <Tabs value={tab} onValueChange={(v) => setTab(v as TabName)}>
        <TabsList aria-label="People ops sections">
          <TabsTrigger value="human-work">Human work</TabsTrigger>
          <TabsTrigger value="freelancers">Freelancers</TabsTrigger>
        </TabsList>
        <TabsContent value="human-work">
          <HumanWorkTab companyId={companyId} />
        </TabsContent>
        <TabsContent value="freelancers">
          <FreelancersTab companyId={companyId} />
        </TabsContent>
      </Tabs>
    </PanelFrame>
  )
}
