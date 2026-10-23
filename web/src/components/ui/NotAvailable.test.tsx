import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { NotAvailable } from './States'

describe('NotAvailable', () => {
  it('states plainly that the subsystem is not built and names the milestone', () => {
    render(<NotAvailable milestone="M6" title="Finance is not built yet" description="Spend will live here." />)
    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.getByText('Finance is not built yet')).toBeInTheDocument()
    expect(screen.getByText('Arrives in M6')).toBeInTheDocument()
    expect(screen.getByText('Spend will live here.')).toBeInTheDocument()
  })

  it('has a compact variant with the same honesty', () => {
    render(<NotAvailable compact milestone="M3" />)
    expect(screen.getByText(/Not built yet/)).toBeInTheDocument()
    expect(screen.getByText('Arrives in M3')).toBeInTheDocument()
  })
})
