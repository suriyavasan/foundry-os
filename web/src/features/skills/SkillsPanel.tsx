import { Library, Search } from 'lucide-react'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui'
import { CatalogTab } from './CatalogTab'
import { LibraryTab } from './LibraryTab'

export function SkillsPanel() {
  return (
    <PanelFrame title="Skills" subtitle="What each employee knows how to do. Skills are scanned before any agent can use them.">
      <Tabs defaultValue="library">
        <TabsList aria-label="Skills sections">
          <TabsTrigger value="library">
            <span className="inline-flex items-center gap-1.5">
              <Library className="size-3.5" aria-hidden /> Library
            </span>
          </TabsTrigger>
          <TabsTrigger value="catalog">
            <span className="inline-flex items-center gap-1.5">
              <Search className="size-3.5" aria-hidden /> Find &amp; install
            </span>
          </TabsTrigger>
        </TabsList>
        <TabsContent value="library">
          <LibraryTab />
        </TabsContent>
        <TabsContent value="catalog">
          <CatalogTab />
        </TabsContent>
      </Tabs>
    </PanelFrame>
  )
}
