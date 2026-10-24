import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { loaderSteps } from '@/lib/aiSetup'
import { usePrefersReducedMotion } from '@/lib/motion'
import { makeProviderTest } from '@/test/m10'
import { BackgroundBeams } from './BackgroundBeams'
import { GlowCard } from './GlowCard'
import { MovingBorderButton } from './MovingBorderButton'
import { MultiStepLoader } from './MultiStepLoader'
import { Spotlight } from './Spotlight'
import { TextGenerate } from './TextGenerate'
import { VanishInput } from './VanishInput'

type Listener = () => void
let listeners: Listener[] = []
const mq = { matches: false }

function motion(reduce: boolean) {
  listeners = []
  mq.matches = reduce
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      get matches() {
        return mq.matches && query.includes('prefers-reduced-motion')
      },
      media: query,
      addEventListener: (_: string, l: Listener) => listeners.push(l),
      removeEventListener: (_: string, l: Listener) => (listeners = listeners.filter((x) => x !== l)),
    }),
  })
}

afterEach(() => {
  // jsdom has no matchMedia: that is "no preference", motion allowed
  delete (window as { matchMedia?: unknown }).matchMedia
})

describe('prefers-reduced-motion', () => {
  it('is read live and follows changes', () => {
    motion(false)
    function Probe() {
      return <output>{String(usePrefersReducedMotion())}</output>
    }
    render(<Probe />)
    expect(screen.getByRole('status')).toHaveTextContent('false')
    // the person switches the system setting while the page is open
    act(() => {
      mq.matches = true
      listeners.forEach((l) => l())
    })
    expect(screen.getByRole('status')).toHaveTextContent('true')
  })

  it('defaults to "motion allowed" where matchMedia does not exist', () => {
    function Probe() {
      return <output>{String(usePrefersReducedMotion())}</output>
    }
    render(<Probe />)
    expect(screen.getByRole('status')).toHaveTextContent('false')
  })
})

describe('Text generate reveal', () => {
  it('animates word by word, with the whole text in the accessible name', () => {
    motion(false)
    const { container } = render(<TextGenerate text="A company that runs itself." />)
    expect(screen.getByRole('heading', { name: 'A company that runs itself.' })).toBeInTheDocument()
    expect(container.querySelectorAll('h1 > span')).toHaveLength(5)
  })

  it('reduced motion: plain text, no spans', () => {
    motion(true)
    const { container } = render(<TextGenerate text="A company that runs itself." />)
    expect(screen.getByRole('heading', { name: 'A company that runs itself.' })).toHaveTextContent('A company that runs itself.')
    expect(container.querySelectorAll('h1 > span')).toHaveLength(0)
  })
})

describe('Spotlight and background beams', () => {
  it('are decorative (aria-hidden) and animated by default', () => {
    motion(false)
    const { container } = render(
      <>
        <Spotlight />
        <BackgroundBeams />
      </>,
    )
    expect(container.querySelector('[data-fx="spotlight"]')).toHaveAttribute('aria-hidden', 'true')
    expect(container.querySelector('[data-fx="beams"]')).toHaveAttribute('aria-hidden', 'true')
    expect(container.querySelector('[data-fx="spotlight"]')).not.toHaveAttribute('data-reduced')
    expect(container.querySelectorAll('[data-fx="beams"] path')).toHaveLength(28) // a still curve and a travelling beam each
  })

  it('reduced motion: still, with no travelling beams', () => {
    motion(true)
    const { container } = render(
      <>
        <Spotlight />
        <BackgroundBeams />
      </>,
    )
    expect(container.querySelector('[data-fx="spotlight"]')).toHaveAttribute('data-reduced', 'true')
    expect(container.querySelector('[data-fx="beams"]')).toHaveAttribute('data-reduced', 'true')
    expect(container.querySelectorAll('[data-fx="beams"] path')).toHaveLength(14)
  })
})

describe('Moving-border button', () => {
  it('is a real button whose border moves only while enabled and motion is allowed', async () => {
    motion(false)
    const onClick = vi.fn()
    const { container, rerender } = render(<MovingBorderButton onClick={onClick}>Launch</MovingBorderButton>)
    expect(container.querySelector('[data-moving]')).not.toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Launch' }))
    expect(onClick).toHaveBeenCalledOnce()
    rerender(<MovingBorderButton disabled>Launch</MovingBorderButton>)
    expect(container.querySelector('[data-moving]')).toBeNull()
    expect(screen.getByRole('button', { name: 'Launch' })).toBeDisabled()
  })

  it('reduced motion: a still border', () => {
    motion(true)
    const { container } = render(<MovingBorderButton>Start</MovingBorderButton>)
    expect(container.querySelector('[data-moving]')).toBeNull()
    expect(screen.getByRole('button', { name: 'Start' })).toBeEnabled()
  })
})

describe('Placeholders-and-vanish input', () => {
  const placeholders = ['First idea', 'Second idea']

  it('shows a placeholder while empty and none once there is text; the field itself is a normal input', async () => {
    motion(false)
    const onChange = vi.fn()
    const { rerender } = render(<VanishInput aria-label="Idea" value="" placeholders={placeholders} onChange={onChange} />)
    expect(screen.getByText('First idea')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Idea'), 'x')
    expect(onChange).toHaveBeenCalled()
    rerender(<VanishInput aria-label="Idea" value="my idea" placeholders={placeholders} onChange={onChange} />)
    expect(screen.queryByText('First idea')).not.toBeInTheDocument()
  })

  it('lifts the text off in letters when vanishing', () => {
    motion(false)
    const { container } = render(<VanishInput aria-label="Idea" value="abc" vanishing placeholders={placeholders} onChange={() => undefined} />)
    expect(container.querySelector('[data-fx="vanish"]')).toHaveTextContent('abc')
    expect(container.querySelectorAll('[data-fx="vanish"] > span')).toHaveLength(3)
  })

  it('reduced motion: the first placeholder stays and nothing animates away', () => {
    motion(true)
    const { container } = render(<VanishInput aria-label="Idea" value="abc" vanishing placeholders={placeholders} onChange={() => undefined} />)
    expect(container.querySelector('[data-fx="vanish"]')).toBeNull()
    expect(screen.getByLabelText('Idea')).toHaveValue('abc')
  })
})

describe('Card hover', () => {
  it('is a radio button that follows the pointer with a glow, and says whether it is selected', async () => {
    motion(false)
    const { container, rerender } = render(<GlowCard onClick={() => undefined}>OpenRouter</GlowCard>)
    const card = screen.getByRole('radio', { name: 'OpenRouter' })
    expect(card).toHaveAttribute('aria-checked', 'false')
    expect(container.querySelector('button > span[aria-hidden]')).not.toBeNull() // the glow layer
    rerender(
      <GlowCard selected onClick={() => undefined}>
        OpenRouter
      </GlowCard>,
    )
    expect(screen.getByRole('radio', { name: 'OpenRouter' })).toHaveAttribute('aria-checked', 'true')
  })

  it('reduced motion: no glow layer and no lift, only the border colour', () => {
    motion(true)
    const { container } = render(<GlowCard>OpenRouter</GlowCard>)
    expect(container.querySelector('button > span[aria-hidden]')).toBeNull()
    expect(screen.getByRole('radio').className).not.toContain('-translate-y')
  })
})

describe('Multi-step loader', () => {
  it('renders one row per check from the real result, with the server’s detail', () => {
    motion(false)
    const test = makeProviderTest({ failing: 'context' })
    render(<MultiStepLoader steps={loaderSteps(test, false)} />)
    const list = screen.getByRole('list', { name: 'Connection test' })
    expect(list.querySelectorAll('li')).toHaveLength(6)
    expect(screen.getByRole('listitem', { name: 'Context window: failed' })).toHaveTextContent('context failed: nope')
    expect(screen.getByRole('listitem', { name: 'Reachable: passed' })).toBeInTheDocument()
    expect(screen.getByRole('listitem', { name: 'Ready: failed' })).toBeInTheDocument()
    expect(screen.getByText('The test found a problem')).toBeInTheDocument()
  })

  it('while waiting for the server only the first row is "checking": no invented progress', () => {
    motion(false)
    render(<MultiStepLoader steps={loaderSteps(null, true)} />)
    expect(screen.getByRole('listitem', { name: 'Reachable: checking' })).toBeInTheDocument()
    expect(screen.getByRole('listitem', { name: 'Authenticated: waiting' })).toBeInTheDocument()
    expect(screen.getByRole('listitem', { name: 'Ready: waiting' })).toBeInTheDocument()
    expect(screen.getByText('Testing the connection')).toBeInTheDocument()
  })

  it('marks a check the server did not run as "not checked"', () => {
    motion(false)
    render(<MultiStepLoader steps={loaderSteps({ ...makeProviderTest(), ok: false, checks: makeProviderTest().checks.slice(0, 2) }, false)} />)
    expect(screen.getByRole('listitem', { name: 'Tool calling: not checked' })).toBeInTheDocument()
    expect(screen.getAllByText('The server did not run this check.')).toHaveLength(3)
  })

  it('reduced motion: the spinner does not rotate', () => {
    motion(true)
    const { container } = render(<MultiStepLoader steps={loaderSteps(null, true)} />)
    expect(container.querySelector('.cos-spin')).toBeNull()
    motion(false)
    const again = render(<MultiStepLoader steps={loaderSteps(null, true)} />)
    expect(again.container.querySelector('.cos-spin')).not.toBeNull()
  })
})
