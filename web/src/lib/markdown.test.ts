import { describe, expect, it } from 'vitest'
import { parseInline, parseMarkdown, safeHref, splitFrontmatter, stripDangerousHtml } from './markdown'

const flat = (v: unknown): string => JSON.stringify(v)

describe('splitFrontmatter', () => {
  it('separates a leading YAML block', () => {
    const r = splitFrontmatter('---\nname: x\n---\n# Title\n')
    expect(r.frontmatter).toBe('name: x')
    expect(r.body).toBe('# Title\n')
  })
  it('leaves a document without frontmatter alone', () => {
    expect(splitFrontmatter('# Hi\n---\nnot front').frontmatter).toBeNull()
  })
})

describe('stripDangerousHtml', () => {
  it('removes script/style/iframe blocks together with their content', () => {
    const out = stripDangerousHtml('before\n<script>alert(1)</script>\nmid <style>body{display:none}</style> after\n<iframe src="x"></iframe>end')
    expect(out).not.toMatch(/alert|display:none|iframe|script|style/i)
    expect(out).toContain('before')
    expect(out).toContain('end')
  })
  it('removes a multi-line script even across blank lines, and an unclosed one to the end', () => {
    expect(stripDangerousHtml('a\n<script>\nevil()\n\nmore()\n</script>\nb')).not.toMatch(/evil|more/)
    expect(stripDangerousHtml('a\n<script>\nevil()')).not.toMatch(/evil/)
  })
  it('removes comments', () => expect(stripDangerousHtml('a <!-- secret --> b')).not.toMatch(/secret/))
  it('leaves fenced code untouched', () => {
    const src = '```html\n<script>alert(1)</script>\n```'
    expect(stripDangerousHtml(src)).toBe(src)
  })
})

describe('safeHref', () => {
  it('allows http(s), mailto and in-page anchors', () => {
    expect(safeHref('https://example.com/a?b=1')).toBe('https://example.com/a?b=1')
    expect(safeHref('http://example.com')).not.toBeNull()
    expect(safeHref('mailto:a@b.co')).not.toBeNull()
    expect(safeHref('#section-1')).toBe('#section-1')
  })
  it('rejects script-capable and relative schemes, including obfuscated ones', () => {
    for (const bad of ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', ' javascript:alert(1)', 'java\tscript:alert(1)', 'java\nscript:alert(1)', 'data:text/html;base64,AAAA', 'vbscript:x', 'file:///etc/passwd', '//evil.com', '/relative/path', 'ftp://x']) {
      expect(safeHref(bad), bad).toBeNull()
    }
  })
})

describe('parseInline', () => {
  it('parses code, emphasis and links', () => {
    const nodes = parseInline('use `npm i` for **bold** and *it* with [docs](https://x.dev)')
    expect(nodes.map((n) => n.t)).toEqual(['text', 'code', 'text', 'strong', 'text', 'em', 'text', 'link'])
  })
  it('turns a javascript: link into plain text, never a link', () => {
    const nodes = parseInline('[click](javascript:alert(1))')
    expect(nodes.some((n) => n.t === 'link')).toBe(false)
    expect(flat(nodes)).toContain('click')
  })
  it('never loads images, showing the alt text instead', () => {
    const nodes = parseInline('![logo](https://tracker.example/p.png)')
    expect(flat(nodes)).toContain('[image: logo]')
    expect(flat(nodes)).not.toContain('tracker')
  })
  it('drops inline HTML tags but keeps their text', () => {
    const nodes = parseInline('hello <b onclick="x()">world</b> <img src=x onerror=alert(1)> end')
    const text = nodes.map((n) => (n.t === 'text' ? n.v : '')).join('')
    expect(text).toBe('hello world  end')
    expect(flat(nodes)).not.toMatch(/onclick|onerror|<img/)
  })
  it('keeps HTML-looking text literal inside code spans', () => {
    expect(flat(parseInline('`<script>x</script>`'))).toContain('<script>x</script>')
  })
  it('keeps comparison operators in prose', () => {
    expect(parseInline('a < b and c > d').map((n) => (n.t === 'text' ? n.v : '')).join('')).toBe('a < b and c > d')
  })
})

describe('parseMarkdown', () => {
  const doc = `---
name: demo
---
# Title

Intro with **bold**.

- one
- two
  - nested
1. first
2. second

> quote

\`\`\`bash
rm -rf /tmp/x
\`\`\`

| a | b |
|---|---|
| 1 | 2 |

---
`
  const blocks = parseMarkdown(doc)
  it('drops the frontmatter and parses the block kinds', () => {
    expect(blocks.map((b) => b.t)).toEqual(['heading', 'p', 'list', 'list', 'quote', 'code', 'table', 'hr'])
    expect(flat(blocks)).not.toContain('name: demo')
  })
  it('nests lists by indentation', () => {
    const list = blocks[2]
    expect(list && list.t === 'list' && list.items[1]!.children).toHaveLength(1)
  })
  it('keeps code block content literal', () => {
    const code = blocks.find((b) => b.t === 'code')
    expect(code && code.t === 'code' && code.v).toBe('rm -rf /tmp/x')
  })
  it('strips a hostile document down to inert text', () => {
    const hostile = parseMarkdown('# Hi\n<script>alert("xss")</script>\n[a](javascript:alert(1))\n<img src=x onerror=alert(1)>\n<iframe src="//evil"></iframe>')
    const s = flat(hostile)
    expect(s).not.toMatch(/alert\("xss"\)|script|iframe|onerror|javascript:|evil/)
  })
  it('does not hang on pathological input', () => {
    const t0 = Date.now()
    parseMarkdown('*'.repeat(5000) + '_'.repeat(5000) + '['.repeat(2000) + '`'.repeat(2000))
    expect(Date.now() - t0).toBeLessThan(2000)
  })
})
