import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { normalizeLive } from '@/lib/liveState'
import { makeAgent, mockApi, page } from '@/test/factories'
import { renderWithProviders } from '@/test/render'
import { CanvasProvider } from './CanvasContext'
import { OrgTree } from './OrgTree'

const founder = makeAgent({ id: 'f', name: 'Atlas', role_title: 'Founder & CEO', kind: 'founder', reports_to_human: true, live: normalizeLive({ state: 'working', summary: 'Planning Q4', current_task: null, current_run: null, since: null }) })
const dev = makeAgent({ id: 'd', name: 'Dev', role_title: 'Backend Engineer', manager_agent_id: 'f' })
const paused = makeAgent({ id: 'p', name: 'Iris', role_title: 'Market Researcher', manager_agent_id: 'f', status: 'paused' })

function setup(onOpen = vi.fn()) {
  vi.stubGlobal('fetch', mockApi({ '/companies/c1/tasks': page([]) }).impl)
  renderWithProviders(
    <CanvasProvider companyId="c1">
      <div style={{ width: 800, height: 500 }}>
        <OrgTree agents={[founder, dev, paused]} ownerName="Sam" onOpenAgent={onOpen} />
      </div>
    </CanvasProvider>,
  )
  return onOpen
}

describe('OrgTree', () => {
  beforeEach(() => localStorage.clear())

  it('draws You (the Founder), the AI Co-founder and the reporting tree with names, roles and states', () => {
    setup()
    const tree = screen.getByRole('tree', { name: 'Organization chart' })
    expect(within(tree).getByText('You · Founder')).toBeInTheDocument()
    expect(within(tree).getByText('Sam')).toBeInTheDocument()
    expect(within(tree).getByText('Atlas')).toBeInTheDocument()
    expect(within(tree).getByText('AI Co-founder')).toBeInTheDocument()
    expect(within(tree).getByText('Backend Engineer')).toBeInTheDocument()
    // state words for non-idle employees only
    expect(within(tree).getByText('Working')).toBeInTheDocument()
    expect(within(tree).getByText('Paused')).toBeInTheDocument()
  })

  it('gives every avatar button an accessible name that includes the live summary', () => {
    setup()
    expect(screen.getByRole('button', { name: /Atlas, AI Co-founder, Working, Planning Q4/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Iris, Market Researcher, Paused/ })).toBeInTheDocument()
  })

  it('uses a distinct avatar type per employee type', () => {
    setup()
    const families = [...document.querySelectorAll('[data-family]')].map((e) => e.getAttribute('data-family'))
    expect(families).toEqual(expect.arrayContaining(['human', 'founder', 'engineering', 'research']))
  })

  it('opens the agent when its avatar is clicked', async () => {
    const onOpen = setup()
    await userEvent.click(screen.getByRole('button', { name: /Dev, Backend Engineer/ }))
    expect(onOpen).toHaveBeenCalledWith('d')
  })

  it('collapses and expands a team', async () => {
    setup()
    const toggle = screen.getByRole('button', { name: /Collapse Atlas's team/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await userEvent.click(toggle)
    expect(screen.queryByText('Backend Engineer')).not.toBeInTheDocument()
    const expand = screen.getByRole('button', { name: /Expand Atlas's team \(2 hidden\)/ })
    await userEvent.click(expand)
    expect(screen.getByText('Backend Engineer')).toBeInTheDocument()
  })

  it('has a fit control that toggles', async () => {
    setup()
    const fit = screen.getByRole('button', { name: 'Fit chart to width' })
    expect(fit).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(fit)
    expect(screen.getByRole('button', { name: 'Show actual size' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('reads left to right by default and switches to top-down, remembering the choice', async () => {
    setup()
    const tree = screen.getByRole('tree', { name: 'Organization chart' })
    expect(tree).toHaveAttribute('data-orientation', 'horizontal')
    await userEvent.click(screen.getByRole('button', { name: 'Lay out top to bottom' }))
    expect(tree).toHaveAttribute('data-orientation', 'vertical')
    expect(localStorage.getItem('company-os.org-orientation')).toBe('vertical')
    // the team toggles and node content work the same in both directions
    expect(screen.getByRole('button', { name: /Collapse Atlas's team/ })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Lay out left to right' }))
    expect(tree).toHaveAttribute('data-orientation', 'horizontal')
  })

  it('opens in the direction this browser chose last', () => {
    localStorage.setItem('company-os.org-orientation', 'vertical')
    setup()
    expect(screen.getByRole('tree', { name: 'Organization chart' })).toHaveAttribute('data-orientation', 'vertical')
  })
})
