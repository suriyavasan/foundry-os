import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CommandExchange } from '@/api/types'
import { jsonResponse, makeTask, mockApi, page } from '@/test/factories'
import { makeExchange } from '@/test/m10'
import { apiError, baseRoutes, deferred, loc, renderCanvas } from '@/test/m5'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const feed = (items: CommandExchange[], next: string | null = null) => ({ items, next_cursor: next })
const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString()

const widget = () => screen.getByRole('region', { name: 'Command Center' })
const input = () => within(widget()).getByRole('combobox', { name: /Command or message for the AI Co-founder/ })

/** A feed that remembers what was posted, like the real one. */
function liveFeed(initial: CommandExchange[] = []) {
  const items = [...initial]
  return {
    items,
    feedRoute: () => jsonResponse(feed(items)),
    record: (ex: CommandExchange) => {
      items.unshift(ex)
      return ex
    },
  }
}

async function renderCc(over: Record<string, unknown> = {}, route = '/c/c1') {
  const api = mockApi({ ...baseRoutes, '/companies/c1/command-center/feed': feed([]), ...over })
  vi.stubGlobal('fetch', api.impl)
  renderCanvas(route)
  await screen.findByRole('region', { name: 'Command Center' })
  return api
}

describe('Command Center widget: states', () => {
  it('opens with a hello from the AI Co-founder and three suggestions that fill the composer', async () => {
    await renderCc()
    expect(await within(widget()).findByText(/I'm your AI Co-founder/)).toBeInTheDocument()
    const suggestions = within(widget()).getByRole('list', { name: 'Things to try' })
    expect(within(suggestions).getAllByRole('button').map((b) => b.textContent)).toEqual(["What's the plan this week?", '/status', 'Hire a designer'])
    await userEvent.click(within(suggestions).getByRole('button', { name: '/status' }))
    expect(input()).toHaveValue('/status')
    expect(input()).toHaveFocus()
  })

  it('a command answers at once with structured output: a table, a list, text, an error, and links that open panels', async () => {
    const items = [
      makeExchange({ id: 'e1', input: '/tasks blocked', output: { kind: 'table', title: 'Blocked tasks', text: '', columns: ['Task', 'Owner'], rows: [['Write copy', 'Iris'], ['Ship page', 'Atlas']], links: [{ label: 'Open tasks', panel: 'tasks' }] }, created_at: minutesAgo(1) }),
      makeExchange({ id: 'e2', input: '/goals', output: { kind: 'list', title: 'Goals', text: '', rows: [['Launch v1', 'active'], ['Grow']] }, created_at: minutesAgo(2) }),
      makeExchange({ id: 'e3', input: '/zap', output: { kind: 'error', title: 'Unknown command', text: 'There is no /zap. Try /help.' }, created_at: minutesAgo(3) }),
      makeExchange({ id: 'e4', input: '/status', output: { kind: 'text', title: 'Status', text: 'All **12** agents are idle.' }, created_at: minutesAgo(4) }),
    ]
    await renderCc({ '/companies/c1/command-center/feed': feed(items) })
    // oldest first: the newest is at the bottom
    const [text, error, list, table] = await within(widget()).findAllByTestId('exchange')
    expect(within(widget()).getAllByTestId('exchange')).toHaveLength(4)
    // table
    expect(within(table!).getByText('/tasks blocked')).toBeInTheDocument()
    expect(within(table!).getByRole('columnheader', { name: 'Task' })).toBeInTheDocument()
    expect(within(table!).getByRole('cell', { name: 'Write copy' })).toBeInTheDocument()
    // no status badge per message: a command that ran says nothing under it
    expect(within(widget()).queryByText('Done')).not.toBeInTheDocument()
    await userEvent.click(within(table!).getByRole('button', { name: /Open tasks/ }))
    await waitFor(() => expect(loc()).toBe('?panel=tasks'))
    // list
    expect(within(list!).getByText('Launch v1')).toBeInTheDocument()
    expect(within(list!).getByText(/active/)).toBeInTheDocument()
    // error
    expect(within(error!).getByText('Unknown command')).toBeInTheDocument()
    expect(within(error!).getByText('There is no /zap. Try /help.')).toBeInTheDocument()
    // text (markdown, safely)
    expect(within(text!).getByText('12', { selector: 'strong' })).toBeInTheDocument()
  })

  it('a free-text exchange moves from waiting to a typing indicator with the latest step (steps on request) to a reply with what it changed', async () => {
    const items = [
      makeExchange({ id: 'w1', input: 'Think about pricing', input_kind: 'note', status: 'pending', output: null, created_at: minutesAgo(1) }),
      makeExchange({ id: 'w2', input: 'Plan the launch', input_kind: 'note', status: 'processing', output: null, run_id: 'r1', created_at: minutesAgo(2) }),
      makeExchange({
        id: 'w3',
        input: 'Raise prices by 10%',
        input_kind: 'note',
        status: 'answered',
        note_id: 'n7',
        output: { kind: 'text', title: null, text: 'I created a goal and a review task.' },
        actions: [{ type: 'goal', ref_id: 'g1', label: 'Pricing review' }, { type: 'task', ref_id: 't1', label: null }],
        created_at: minutesAgo(3),
      }),
    ]
    await renderCc({
      '/companies/c1/command-center/feed': feed(items),
      '/companies/c1/runs/r1/events': feed([
        { seq: 1, type: 'progress', payload: { message: 'Reading the notes' }, occurred_at: minutesAgo(1) },
        { seq: 2, type: 'usage', payload: { message: 'tokens: 100' }, occurred_at: minutesAgo(1) },
        { seq: 3, type: 'tool_call', payload: { message: 'Creating a goal' }, occurred_at: minutesAgo(1) },
      ] as unknown as CommandExchange[]),
      '/companies/c1/tasks': page([makeTask({ id: 't1', title: 'Review pricing' })]),
    })
    // oldest first
    const [answered, working, waiting] = await within(widget()).findAllByTestId('exchange')
    expect(within(waiting!).getByRole('status')).toHaveTextContent('Waiting for the AI Co-founder')
    expect(within(waiting!).queryByRole('button')).not.toBeInTheDocument()

    // working: one latest line, the steps behind "Show steps"
    expect(within(working!).getByText(/AI Co-founder is working on it/)).toBeInTheDocument()
    await waitFor(() => expect(within(working!).getByText('Creating a goal')).toBeInTheDocument())
    expect(within(working!).queryByRole('list', { name: 'Live progress' })).not.toBeInTheDocument()
    expect(within(working!).queryByText('Reading the notes')).not.toBeInTheDocument()
    await userEvent.click(within(working!).getByRole('button', { name: 'Show steps' }))
    const progress = within(working!).getByRole('list', { name: 'Live progress' })
    expect(within(progress).getByText('Reading the notes')).toBeInTheDocument()
    expect(within(progress).getByText('Creating a goal')).toBeInTheDocument()
    expect(within(progress).queryByText('tokens: 100')).not.toBeInTheDocument() // usage noise is not progress
    expect(within(working!).getByRole('button', { name: 'Hide steps' })).toHaveAttribute('aria-expanded', 'true')

    // answered: the reply, no status badge, the changes collapsed to one line
    expect(within(answered!).getByText('I created a goal and a review task.')).toBeInTheDocument()
    expect(within(answered!).queryByText('Answered')).not.toBeInTheDocument()
    expect(within(answered!).queryByRole('list', { name: 'Changed while answering' })).not.toBeInTheDocument()
    await userEvent.click(within(answered!).getByRole('button', { name: 'Changed 2 things' }))
    const chips = within(answered!).getByRole('list', { name: 'Changed while answering' })
    expect(within(chips).getByRole('button', { name: 'Open goal: Pricing review' })).toBeInTheDocument()
    await userEvent.click(within(chips).getByRole('button', { name: /Open task/ }))
    await waitFor(() => expect(loc()).toBe('?panel=task:t1'))
  })

  it('a failed exchange explains what happened and can be retried; without any explanation it still says something', async () => {
    const items = [
      makeExchange({ id: 'f1', input: 'Do the thing', input_kind: 'note', status: 'failed', output: { kind: 'error', title: 'Could not answer', text: 'The model timed out after 15 minutes.' }, created_at: minutesAgo(30) }),
      makeExchange({ id: 'f2', input: 'Another', input_kind: 'note', status: 'failed', output: null, created_at: minutesAgo(31) }),
    ]
    const api = await renderCc({ '/companies/c1/command-center/feed': feed(items), 'POST /companies/c1/command-center/f1/retry': makeExchange({ id: 'f1', status: 'pending', output: null }) })
    // oldest first: f2, then f1
    const [second, first] = await within(widget()).findAllByTestId('exchange')
    // one inline line under the message: "Failed · Retry"; the reason sits in the reply
    expect(within(first!).getByRole('status')).toHaveTextContent(/^Failed\s*·\s*Retry$/)
    expect(within(first!).getByText('Could not answer')).toBeInTheDocument()
    expect(within(first!).getByText('The model timed out after 15 minutes.')).toBeInTheDocument()
    expect(within(second!).getByRole('alert')).toHaveTextContent(/did not get an answer/)
    await userEvent.click(within(first!).getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/command-center/f1/retry')).toBe(true))
  })

  it('one that has waited longer than the system waits says so, in one line, and offers a retry', async () => {
    const stale = makeExchange({ id: 's1', input: 'Still waiting', input_kind: 'note', status: 'processing', output: null, run_id: null, created_at: minutesAgo(20) })
    const fresh = makeExchange({ id: 's2', input: 'Just now', input_kind: 'note', status: 'processing', output: null, run_id: null, created_at: minutesAgo(1) })
    await renderCc({ '/companies/c1/command-center/feed': feed([fresh, stale]) })
    const [staleCard, freshCard] = await within(widget()).findAllByTestId('exchange')
    expect(within(staleCard!).getByRole('alert')).toHaveTextContent(/Taking longer than usual/)
    expect(within(staleCard!).getByRole('button', { name: 'Retry now' })).toBeInTheDocument()
    expect(within(freshCard!).queryByRole('button', { name: /Retry/ })).not.toBeInTheDocument()
  })

  it('keeps "Open the note" and "Copy text" in a small menu on the reply', async () => {
    await renderCc({ '/companies/c1/command-center/feed': feed([makeExchange({ id: 'a1', input: 'x', input_kind: 'note', status: 'answered', note_id: 'n7', output: { kind: 'text', title: null, text: 'Noted.' } })]) })
    const card = await within(widget()).findByTestId('exchange')
    expect(within(card).queryByText('Open the note')).not.toBeInTheDocument()
    await userEvent.click(within(card).getByRole('button', { name: 'Message options' }))
    expect(await screen.findByRole('menuitem', { name: 'Copy text' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('menuitem', { name: 'Open the note' }))
    await waitFor(() => expect(loc()).toBe('?panel=note:n7'))
  })

  it('says it is not built yet, without an input, on a backend without the command center; and shows errors with retry', async () => {
    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    const first = renderCanvas()
    await screen.findByRole('region', { name: 'Command Center' })
    expect(await within(widget()).findByText('Arrives in M10')).toBeInTheDocument()
    expect(within(widget()).queryByRole('combobox')).not.toBeInTheDocument()
    first.unmount()

    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/command-center/feed': () => apiError(500, 'Feed is down') }).impl)
    renderCanvas()
    expect(await within(await screen.findByRole('region', { name: 'Command Center' })).findByText('Feed is down', {}, { timeout: 6000 })).toBeInTheDocument()
    expect(within(widget()).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  }, 15_000)

  it('a viewer can read the history but not send', async () => {
    await renderCc({ '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme', role: 'viewer' }] }, '/companies/c1/command-center/feed': feed([makeExchange()]) })
    await within(widget()).findAllByTestId('exchange')
    expect(within(widget()).queryByRole('combobox')).not.toBeInTheDocument()
    expect(within(widget()).getByText(/Viewers cannot send commands/)).toBeInTheDocument()
    expect(within(widget()).queryByRole('list', { name: 'Things to try' })).toBeNull()
  })
})

describe('sending', () => {
  it('Ctrl+Enter sends free text; the exchange appears at once as "Sending…" and is replaced by the server’s; the box is cleared', async () => {
    const gate = deferred<Response>()
    const live = liveFeed()
    const api = await renderCc({ '/companies/c1/command-center/feed': live.feedRoute, 'POST /companies/c1/command': () => gate.promise as unknown as Response })
    const user = userEvent.setup()
    await user.type(input(), 'Think about pricing')
    await user.keyboard('{Control>}{Enter}{/Control}')
    const card = await within(widget()).findByTestId('exchange')
    expect(within(card).getByText('Think about pricing')).toBeInTheDocument()
    expect(within(card).getByText('Sending…')).toBeInTheDocument()
    expect(input()).toHaveValue('')
    expect(api.calls.find((c) => c.method === 'POST')?.body).toEqual({ input: 'Think about pricing' })
    gate.resolve(jsonResponse(live.record(makeExchange({ id: 'real', input: 'Think about pricing', input_kind: 'note', status: 'pending', output: null })), 201))
    await waitFor(() => expect(within(widget()).queryByText('Sending…')).not.toBeInTheDocument())
    expect(within(widget()).getAllByTestId('exchange')).toHaveLength(1)
    expect(within(widget()).getByText('Waiting for the AI Co-founder')).toBeInTheDocument()
  })

  it('plain Enter runs a one-line command, with its output right under it', async () => {
    const live = liveFeed()
    const api = await renderCc({
      '/companies/c1/command-center/feed': live.feedRoute,
      'POST /companies/c1/command': () => jsonResponse(live.record(makeExchange({ id: 'c1', input: '/status', output: { kind: 'text', title: 'Status', text: 'Everything is running.' } })), 201),
    })
    const user = userEvent.setup()
    await user.type(input(), '/status{Enter}')
    await waitFor(() => expect(api.calls.find((c) => c.method === 'POST')?.body).toEqual({ input: '/status' }))
    expect(await within(widget()).findByText('Everything is running.')).toBeInTheDocument()
  })

  it('Enter sends free text; Shift+Enter only adds a line', async () => {
    const live = liveFeed()
    const api = await renderCc({ '/companies/c1/command-center/feed': live.feedRoute, 'POST /companies/c1/command': () => jsonResponse(live.record(makeExchange({ id: 'n', input: 'two', input_kind: 'note', status: 'pending', output: null })), 201) })
    const user = userEvent.setup()
    await user.type(input(), 'one{Shift>}{Enter}{/Shift}two')
    expect(input()).toHaveValue('one\ntwo')
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
    await user.keyboard('{Enter}')
    await waitFor(() => expect(api.calls.find((c) => c.method === 'POST')?.body).toEqual({ input: 'one\ntwo' }))
    expect(input()).toHaveValue('')
  })

  it('refuses an empty input with a reason and sends nothing', async () => {
    const api = await renderCc()
    await userEvent.click(within(widget()).getByRole('button', { name: 'Send' }))
    expect(await within(widget()).findByText(/Type a command \(try \/help\)/)).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
  })

  it('an input the server refuses still gets an output: a failed exchange with the reason and a Retry that sends it again', async () => {
    let attempts = 0
    const live = liveFeed()
    const api = await renderCc({
      '/companies/c1/command-center/feed': live.feedRoute,
      'POST /companies/c1/command': () => {
        attempts += 1
        return attempts === 1
          ? jsonResponse({ error: { code: 'ai_setup_required', message: 'Connect a working AI model first', details: { reason: 'no_tested_provider' } } }, 409)
          : jsonResponse(live.record(makeExchange({ id: 'ok', input: '/status', output: { kind: 'text', title: 'Status', text: 'Everything is running.' } })), 201)
      },
    })
    const user = userEvent.setup()
    await user.type(input(), '/status{Enter}')
    const card = await within(widget()).findByTestId('exchange')
    await waitFor(() => expect(within(card).getByRole('status')).toHaveTextContent(/Failed, it did not reach the server\s*·\s*Retry/))
    expect(within(card).getByText('Connect a working AI model first')).toBeInTheDocument()
    await user.click(within(card).getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(api.calls.filter((c) => c.method === 'POST' && c.path === '/companies/c1/command')).toHaveLength(2))
    expect(await within(widget()).findByText('Everything is running.')).toBeInTheDocument()
    expect(within(widget()).getAllByTestId('exchange')).toHaveLength(1)
  })

  it('refuses text over 10,240 characters before sending', async () => {
    const api = await renderCc()
    const box = input()
    await userEvent.click(box)
    await userEvent.paste('a'.repeat(10_241))
    await userEvent.keyboard('{Control>}{Enter}{/Control}')
    expect(await within(widget()).findByText(/too long/)).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
  })
})

describe('slash-command autocomplete', () => {
  it('"/" lists the commands with descriptions; arrows move, Tab accepts; a command that takes arguments gets a space', async () => {
    await renderCc()
    const user = userEvent.setup()
    await user.type(input(), '/')
    const list = await within(widget()).findByRole('listbox', { name: 'Commands' })
    expect(within(list).getAllByRole('option')).toHaveLength(8)
    expect(within(list).getByText('/help')).toBeInTheDocument()
    expect(within(list).getByText('List every command')).toBeInTheDocument()
    await user.keyboard('t')
    await user.keyboard('a')
    const narrowed = within(widget()).getByRole('listbox', { name: 'Commands' })
    expect(within(narrowed).getAllByRole('option').map((o) => o.textContent)).toEqual([expect.stringContaining('/tasks [status]'), expect.stringContaining('/task <title> [@agent]')])
    await user.keyboard('{ArrowDown}{Tab}')
    expect(input()).toHaveValue('/task ')
    expect(within(widget()).queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('Enter on a partial command accepts the suggestion instead of sending it; on a complete one it sends', async () => {
    const api = await renderCc({ 'POST /companies/c1/command': () => jsonResponse(makeExchange({ id: 'h', input: '/help' }), 201) })
    const user = userEvent.setup()
    await user.type(input(), '/he{Enter}')
    expect(input()).toHaveValue('/help')
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
    await user.keyboard('{Enter}')
    await waitFor(() => expect(api.calls.find((c) => c.method === 'POST')?.body).toEqual({ input: '/help' }))
  })

  it('Escape closes the list without clearing the text; clicking a suggestion accepts it', async () => {
    await renderCc()
    const user = userEvent.setup()
    await user.type(input(), '/sp')
    expect(await within(widget()).findByRole('listbox')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(within(widget()).queryByRole('listbox')).not.toBeInTheDocument()
    expect(input()).toHaveValue('/sp')
    await user.clear(input())
    await user.type(input(), '/ap')
    await user.click(await within(widget()).findByRole('option', { name: /\/approve <id>/ }))
    expect(input()).toHaveValue('/approve ')
  })

  it('does not open for free text or after a space', async () => {
    await renderCc()
    const user = userEvent.setup()
    await user.type(input(), 'hello /he')
    expect(within(widget()).queryByRole('listbox')).not.toBeInTheDocument()
    await user.clear(input())
    await user.type(input(), '/tasks ')
    expect(within(widget()).queryByRole('listbox')).not.toBeInTheDocument()
  })
})

describe('history', () => {
  it('ArrowUp recalls what was sent, newest first; ArrowDown walks back to the draft; it is kept for next time', async () => {
    await renderCc({ 'POST /companies/c1/command': () => jsonResponse(makeExchange({ id: `e${Math.random()}` }), 201) })
    const user = userEvent.setup()
    await user.type(input(), '/goals{Enter}')
    await user.type(input(), 'a free thought{Control>}{Enter}{/Control}')
    await user.type(input(), 'my draft')
    await user.keyboard('{ArrowUp}')
    expect(input()).toHaveValue('a free thought')
    await user.keyboard('{ArrowUp}')
    expect(input()).toHaveValue('/goals')
    await user.keyboard('{ArrowUp}')
    expect(input()).toHaveValue('/goals') // the oldest stays
    await user.keyboard('{ArrowDown}{ArrowDown}')
    expect(input()).toHaveValue('my draft')
    expect(JSON.parse(localStorage.getItem('company-os.command-history.c1') ?? '[]')).toEqual(['/goals', 'a free thought'])
  })

  it('starts from what this browser typed before', async () => {
    localStorage.setItem('company-os.command-history.c1', JSON.stringify(['/status']))
    await renderCc()
    await userEvent.click(input())
    await userEvent.keyboard('{ArrowUp}')
    expect(input()).toHaveValue('/status')
  })
})

describe('Command Center panel', () => {
  it('shows the full history with older pages on request, and the list of commands', async () => {
    const newest = makeExchange({ id: 'n1', input: '/status', created_at: minutesAgo(1) })
    const older = makeExchange({ id: 'o1', input: '/goals', created_at: minutesAgo(90), output: { kind: 'text', title: 'Goals', text: 'Two goals.' } })
    const cursors: Array<string | null> = []
    await renderCc(
      {
        '/companies/c1/command-center/feed': (url: URL) => {
          cursors.push(url.searchParams.get('cursor'))
          return jsonResponse(url.searchParams.get('cursor') === 'c2' ? feed([older]) : feed([newest], 'c2'))
        },
      },
      '/c/c1?panel=command-center',
    )
    const dialog = await screen.findByRole('dialog', { name: 'Command Center' })
    await within(dialog).findAllByTestId('exchange')
    expect(within(dialog).queryByText('/goals', { selector: 'p' })).not.toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Load older messages' }))
    await waitFor(() => expect(within(dialog).getByText('Two goals.')).toBeInTheDocument())
    expect(cursors).toContain('c2')
    expect(within(dialog).queryByRole('button', { name: 'Load older messages' })).not.toBeInTheDocument()
    // the reference list
    expect(within(dialog).getByText('/task <title> [@agent]')).toBeInTheDocument()
    expect(within(dialog).getByText('Create a goal · changes things')).toBeInTheDocument()
    // same input as the widget
    expect(within(dialog).getByRole('combobox', { name: /Command or message/ })).toBeInTheDocument()
  })

  it('says it is not built yet without the endpoint', async () => {
    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    renderCanvas('/c/c1?panel=command-center')
    const dialog = await screen.findByRole('dialog', { name: 'Command Center' })
    expect(await within(dialog).findByText('The Command Center is not built yet')).toBeInTheDocument()
    expect(within(dialog).queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('the Notes panel is still reachable (the notes themselves are unchanged)', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/notes': page([]) }).impl)
    renderCanvas('/c/c1?panel=notes')
    expect(await screen.findByRole('dialog', { name: "Founder's notes" })).toBeInTheDocument()
  })
})
