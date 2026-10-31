import { describe, expect, it } from 'vitest'
import { job } from '@/test/m22'
import { availableActions, briefText, isContractor, isOpenJob, listingUrlProblem, nextStep, parseUsd, safeOpenUrl } from './outsourcing'

describe('outsourcing helpers', () => {
  it('offers only the steps that make sense for a status', () => {
    expect(availableActions(job({ status: 'pending_approval' }))).toEqual(['cancel'])
    expect(availableActions(job({ status: 'approved' }))).toEqual(['posted', 'hired', 'raise-budget', 'cancel'])
    expect(availableActions(job({ status: 'posted' }))).not.toContain('posted')
    const hired = availableActions(job({ status: 'hired', freelancer_name: 'Pat', agreed_price_micros: 45_000_000 }))
    expect(hired).toEqual(expect.arrayContaining(['delivered', 'paid', 'close']))
    // once paid: no second payment and no cancelling
    const paid = availableActions(job({ status: 'delivered', paid_micros: 45_000_000 }))
    expect(paid).toEqual(['close'])
    expect(availableActions(job({ status: 'closed' }))).toEqual([])
    expect(availableActions(job({ status: 'declined' }))).toEqual([])
  })

  it('knows open jobs and contractors', () => {
    expect(isOpenJob(job({ status: 'posted' }))).toBe(true)
    expect(isOpenJob(job({ status: 'declined' }))).toBe(false)
    expect(isContractor(job({ status: 'hired', freelancer_name: 'Pat' }))).toBe(true)
    expect(isContractor(job({ status: 'delivered', freelancer_name: 'Pat' }))).toBe(true)
    expect(isContractor(job({ status: 'hired', freelancer_name: null }))).toBe(false)
    expect(isContractor(job({ status: 'posted', freelancer_name: 'Pat' }))).toBe(false)
  })

  it('says what happens next without ever promising an automatic post', () => {
    expect(nextStep(job({ status: 'pending_approval' }))).toMatch(/Nothing is posted/)
    expect(nextStep(job({ status: 'approved' }))).toMatch(/yourself/)
    expect(nextStep(job({ status: 'cancelled' }))).toMatch(/Nothing was posted or paid by Foundry/)
  })

  it('only offers an https helper link', () => {
    expect(safeOpenUrl(job())).toBe('https://www.fiverr.com/search/gigs?query=Logo')
    expect(safeOpenUrl(job({ open_url: 'javascript:alert(1)' }))).toBeNull()
    expect(safeOpenUrl(job({ open_url: null }))).toBeNull()
  })

  it('builds the brief to paste', () => {
    const text = briefText(job())
    expect(text).toContain('Logo for Acme')
    expect(text).toContain('Skills: logo design')
    expect(text).toContain('Budget: up to $50.00')
  })

  it('parses dollar amounts without float drift', () => {
    expect(parseUsd('45.50')).toBe(45.5)
    expect(parseUsd('$1,000')).toBe(1000)
    expect(parseUsd('0')).toBeNull()
    expect(parseUsd('0', { allowZero: true })).toBe(0)
    expect(parseUsd('abc')).toBeNull()
    expect(parseUsd('-3')).toBeNull()
    expect(parseUsd('1e3')).toBeNull()
  })

  it('checks the listing link like the server does', () => {
    expect(listingUrlProblem('fiverr', 'https://www.fiverr.com/gigs/x')).toBeNull()
    expect(listingUrlProblem('fiverr', 'https://fiverr.com/x')).toBeNull()
    expect(listingUrlProblem('fiverr', 'http://www.fiverr.com/x')).toMatch(/https/)
    expect(listingUrlProblem('fiverr', 'https://fiverr.com.evil.example/x')).toMatch(/not a Fiverr link/)
    expect(listingUrlProblem('fiverr', 'https://www.upwork.com/x')).toMatch(/not a Fiverr link/)
    expect(listingUrlProblem('upwork', 'https://www.upwork.com/jobs/~01')).toBeNull()
    expect(listingUrlProblem('other', 'https://example.org/job')).toBeNull()
    expect(listingUrlProblem('other', 'ftp://example.org')).toMatch(/https/)
    expect(listingUrlProblem('fiverr', 'https://user:pw@www.fiverr.com/x')).toMatch(/username/)
    expect(listingUrlProblem('fiverr', 'nonsense')).toMatch(/full link/)
  })
})
