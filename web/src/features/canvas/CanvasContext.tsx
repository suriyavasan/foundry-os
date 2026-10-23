import { createContext, use, useCallback, useMemo, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { parsePanel, samePanel, withPanel, type Panel } from '@/lib/panels'

export type CreateKind = 'task' | 'goal' | 'project' | 'hire' | 'department'

export interface CreatePreset {
  project_id?: string
  goal_id?: string
  assignee_agent_id?: string
  parent_task_id?: string
}

interface CanvasApi {
  companyId: string
  panel: Panel | null
  openPanel: (p: Panel) => void
  closePanel: () => void
  /** Currently open create dialog, if any. */
  creating: { kind: CreateKind; preset?: CreatePreset } | null
  create: (kind: CreateKind, preset?: CreatePreset) => void
  closeCreate: () => void
  paletteOpen: boolean
  setPaletteOpen: (open: boolean) => void
  /** The pause/resume confirmation, shared by the top bar button and the command palette. */
  pauseOpen: boolean
  setPauseOpen: (open: boolean) => void
  /** The emergency-stop confirmation, shared by the Guardrails panel and the command palette. */
  stopOpen: boolean
  setStopOpen: (open: boolean) => void
}

const CanvasContext = createContext<CanvasApi | null>(null)

interface PanelNavState {
  panelDepth?: number
}

/**
 * Drawer navigation backed by the URL (`?panel=`). Opening pushes a history entry so the back button
 * closes/steps back; closing unwinds exactly the entries this session pushed, and falls back to a
 * replace for deep links (no entries to unwind).
 */
export function usePanelNav() {
  const location = useLocation()
  const navigate = useNavigate()
  const panel = useMemo(() => parsePanel(location.search), [location.search])
  const depth = (location.state as PanelNavState | null)?.panelDepth ?? 0

  const openPanel = useCallback(
    (p: Panel) => {
      if (samePanel(panel, p)) return
      navigate(
        { pathname: location.pathname, search: withPanel(location.search, p) },
        { state: { panelDepth: panel ? depth + 1 : 1 } satisfies PanelNavState },
      )
    },
    [navigate, location.pathname, location.search, panel, depth],
  )

  const closePanel = useCallback(() => {
    if (depth > 0) navigate(-depth)
    else navigate({ pathname: location.pathname, search: withPanel(location.search, null) }, { replace: true })
  }, [navigate, depth, location.pathname, location.search])

  return { panel, openPanel, closePanel }
}

export function CanvasProvider({ companyId, children }: { companyId: string; children: ReactNode }) {
  const { panel, openPanel, closePanel } = usePanelNav()
  const [creating, setCreating] = useState<CanvasApi['creating']>(null)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [pauseOpen, setPauseOpen] = useState(false)
  const [stopOpen, setStopOpen] = useState(false)

  const value = useMemo<CanvasApi>(
    () => ({
      companyId,
      panel,
      openPanel,
      closePanel,
      creating,
      create: (kind, preset) => setCreating({ kind, preset }),
      closeCreate: () => setCreating(null),
      paletteOpen,
      setPaletteOpen,
      pauseOpen,
      setPauseOpen,
      stopOpen,
      setStopOpen,
    }),
    [companyId, panel, openPanel, closePanel, creating, paletteOpen, pauseOpen, stopOpen],
  )
  return <CanvasContext value={value}>{children}</CanvasContext>
}

export function useCanvas(): CanvasApi {
  const ctx = use(CanvasContext)
  if (!ctx) throw new Error('useCanvas must be used within <CanvasProvider>')
  return ctx
}
