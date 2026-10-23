import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Markdown } from './Markdown'

describe('<Markdown>', () => {
  it('renders structure as real elements', () => {
    const { container } = render(<Markdown source={'# Title\n\nHello **world** and `code`.\n\n- a\n- b'} />)
    expect(container.querySelector('h3')?.textContent).toBe('Title')
    expect(container.querySelector('strong')?.textContent).toBe('world')
    expect(container.querySelector('code')?.textContent).toBe('code')
    expect(container.querySelectorAll('li')).toHaveLength(2)
  })

  it('injects no script, handlers, iframes or images from a hostile document', () => {
    const hostile = [
      '# Safe heading',
      '<script>window.__pwned = 1</script>',
      '<img src=x onerror="window.__pwned=2">',
      '<iframe src="https://evil.example"></iframe>',
      '<a href="javascript:window.__pwned=3" onclick="window.__pwned=4">raw html link</a>',
      '[md link](javascript:window.__pwned=5)',
      '![tracking pixel](https://tracker.example/p.png)',
      '<style>*{display:none}</style>',
    ].join('\n\n')
    const { container } = render(<Markdown source={hostile} />)
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('iframe')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('style')).toBeNull()
    expect(container.querySelector('[onerror],[onclick]')).toBeNull()
    for (const a of container.querySelectorAll('a')) expect(a.getAttribute('href')).not.toMatch(/^javascript:/i)
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined()
    expect(container.textContent).toContain('Safe heading')
    expect(container.textContent).toContain('md link')
    expect(container.textContent).not.toMatch(/__pwned|evil\.example/)
  })

  it('opens external links safely', () => {
    const { container } = render(<Markdown source="[docs](https://example.com)" />)
    const a = container.querySelector('a')!
    expect(a.getAttribute('href')).toBe('https://example.com')
    expect(a.getAttribute('rel')).toContain('noopener')
    expect(a.getAttribute('target')).toBe('_blank')
  })

  it('shows HTML inside a fenced code block as literal text', () => {
    const { container } = render(<Markdown source={'```html\n<script>alert(1)</script>\n```'} />)
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('pre')?.textContent).toBe('<script>alert(1)</script>')
  })
})
