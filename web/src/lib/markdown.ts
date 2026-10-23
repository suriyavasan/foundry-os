/**
 * A deliberately small Markdown parser for previewing SKILL.md files. It produces a plain data tree
 * (never HTML strings), which `components/ui/Markdown.tsx` renders as React elements, so there is no
 * `dangerouslySetInnerHTML` anywhere and text is always escaped by React.
 *
 * Safety rules (tested):
 * - raw HTML is never passed through: `<script>`/`<style>`/`<iframe>`... blocks are removed with their
 *   content, comments are removed, any other tag is dropped and its text kept. Inside fenced code and
 *   inline code spans everything is literal.
 * - links are kept only for http(s), mailto and in-page `#anchors`; anything else (`javascript:`,
 *   `data:`, `vbscript:`...) becomes plain text. Images are never loaded (the alt text is shown).
 *
 * Supported: headings, paragraphs, bullet/numbered lists (nested), fenced code, blockquotes, rules,
 * GFM tables, **bold**, *italic*, `code`, [links](url) and <autolinks>.
 */

export type Inline =
  | { t: 'text'; v: string }
  | { t: 'code'; v: string }
  | { t: 'strong'; c: Inline[] }
  | { t: 'em'; c: Inline[] }
  | { t: 'link'; href: string; c: Inline[] }

export interface ListItem {
  c: Inline[]
  children: Extract<Block, { t: 'list' }>[]
}

export type Block =
  | { t: 'heading'; level: 1 | 2 | 3 | 4 | 5 | 6; c: Inline[] }
  | { t: 'p'; c: Inline[] }
  | { t: 'code'; lang: string; v: string }
  | { t: 'list'; ordered: boolean; items: ListItem[] }
  | { t: 'quote'; c: Block[] }
  | { t: 'hr' }
  | { t: 'table'; head: Inline[][]; rows: Inline[][][] }

const MAX_INPUT = 400_000
const MAX_DEPTH = 6

/* --------------------------------- frontmatter --------------------------------- */

/** Split a leading `---` YAML block from the body. The preview shows the body; the detail shows frontmatter separately. */
export function splitFrontmatter(src: string): { frontmatter: string | null; body: string } {
  const m = /^\uFEFF?---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(src)
  if (!m) return { frontmatter: null, body: src }
  return { frontmatter: m[1] ?? '', body: src.slice(m[0].length) }
}

/* ---------------------------------- sanitising ---------------------------------- */

const BLOCK_TAGS = 'script|style|iframe|object|embed|noscript|template|svg|math|form|textarea|title'
const FENCE_RE = /^ {0,3}(`{3,}|~{3,})/

/** Remove executable/embedded HTML (with content) and comments from everything outside code fences. */
export function stripDangerousHtml(src: string): string {
  const lines = src.replace(/\r\n?/g, '\n').split('\n')
  const out: string[] = []
  let chunk: string[] = []
  let fence: string | null = null
  const flush = () => {
    if (!chunk.length) return
    let text = chunk.join('\n')
    text = text.replace(/<!--[\s\S]*?(?:-->|$)/g, '')
    text = text.replace(new RegExp(`<(${BLOCK_TAGS})\\b[\\s\\S]*?<\\/\\1\\s*>`, 'gi'), '')
    // An opening tag that never closes swallows the rest of the chunk, as a browser would.
    text = text.replace(new RegExp(`<(${BLOCK_TAGS})\\b[\\s\\S]*$`, 'i'), '')
    out.push(text)
    chunk = []
  }
  for (const line of lines) {
    const f = FENCE_RE.exec(line)
    if (fence === null && f) {
      flush()
      fence = f[1]![0]!.repeat(3)
      out.push(line)
    } else if (fence !== null) {
      out.push(line)
      if (f && f[1]!.startsWith(fence) && line.trim().replace(/[`~]/g, '') === '') fence = null
    } else {
      chunk.push(line)
    }
  }
  flush()
  return out.join('\n')
}

/* ----------------------------------- inline ------------------------------------ */

export function safeHref(href: string): string | null {
  // Browsers ignore control characters and whitespace inside a URL scheme ("java\tscript:"), so drop them before checking.
  const h = [...href].filter((ch) => ch.charCodeAt(0) > 0x20 && ch.charCodeAt(0) !== 0x7f).join('')
  if (/^(https?:\/\/|mailto:)/i.test(h) || /^#[\w-]*$/.test(h)) return h
  return null
}

const TAG_RE = /<\/?[A-Za-z][^>\n]*>|<![A-Za-z][^>\n]*>|<\?[^>\n]*>/g

function pushText(out: Inline[], v: string) {
  if (!v) return
  const last = out[out.length - 1]
  if (last && last.t === 'text') last.v += v
  else out.push({ t: 'text', v })
}

function findClosing(s: string, from: number, token: string): number {
  let i = from
  while (i < s.length) {
    const j = s.indexOf(token, i)
    if (j === -1) return -1
    if (s[j - 1] !== '\\') return j
    i = j + token.length
  }
  return -1
}

export function parseInline(src: string, depth = 0): Inline[] {
  const out: Inline[] = []
  let i = 0
  const s = src
  while (i < s.length) {
    const c = s[i]!
    if (c === '\\' && i + 1 < s.length && /[\\`*_{}[\]()#+\-.!|<>~]/.test(s[i + 1]!)) {
      pushText(out, s[i + 1]!)
      i += 2
      continue
    }
    if (c === '`') {
      let n = 1
      while (s[i + n] === '`') n++
      const ticks = '`'.repeat(n)
      const end = s.indexOf(ticks, i + n)
      if (end !== -1) {
        out.push({ t: 'code', v: s.slice(i + n, end).replace(/^ (.*) $/s, '$1') })
        i = end + n
        continue
      }
      pushText(out, ticks)
      i += n
      continue
    }
    if (c === '!' && s[i + 1] === '[') {
      const m = /^!\[([^\]\n]*)\]\(([^)\n]*)\)/.exec(s.slice(i))
      if (m) {
        pushText(out, m[1] ? `[image: ${m[1]}]` : '[image]')
        i += m[0].length
        continue
      }
    }
    if (c === '[' && depth < MAX_DEPTH) {
      const m = /^\[((?:[^\]\\\n]|\\.)*)\]\(\s*<?([^)\s>]*)>?(?:\s+"[^"\n]*")?\s*\)/.exec(s.slice(i))
      if (m) {
        const href = safeHref(m[2]!)
        const inner = parseInline(m[1]!, depth + 1)
        if (href) out.push({ t: 'link', href, c: inner })
        else for (const n of inner) out.push(n)
        i += m[0].length
        continue
      }
    }
    if (c === '<') {
      const auto = /^<((?:https?:\/\/|mailto:)[^\s<>]+)>/i.exec(s.slice(i))
      if (auto) {
        out.push({ t: 'link', href: auto[1]!, c: [{ t: 'text', v: auto[1]! }] })
        i += auto[0].length
        continue
      }
      TAG_RE.lastIndex = 0
      const tag = TAG_RE.exec(s.slice(i))
      if (tag && tag.index === 0) {
        i += tag[0].length // drop the tag, keep surrounding text
        continue
      }
    }
    if ((c === '*' || c === '_') && depth < MAX_DEPTH) {
      const double = s[i + 1] === c
      const token = double ? c + c : c
      const inner0 = i + token.length
      const end = findClosing(s, inner0, token)
      const wordy = c === '_' && /\w/.test(s[i - 1] ?? '')
      if (end > inner0 && !wordy && !/^\s/.test(s.slice(inner0, inner0 + 1)) && !/\s$/.test(s.slice(inner0, end))) {
        const kids = parseInline(s.slice(inner0, end), depth + 1)
        out.push(double ? { t: 'strong', c: kids } : { t: 'em', c: kids })
        i = end + token.length
        continue
      }
    }
    pushText(out, c)
    i++
  }
  return out
}

/* ----------------------------------- blocks ------------------------------------ */

const LIST_RE = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/
const TABLE_SEP_RE = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/
const HR_RE = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/

const splitRow = (line: string): string[] => {
  let t = line.trim()
  if (t.startsWith('|')) t = t.slice(1)
  if (t.endsWith('|') && !t.endsWith('\\|')) t = t.slice(0, -1)
  return t.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, '|'))
}

function parseList(lines: string[], start: number): { block: Extract<Block, { t: 'list' }>; next: number } {
  const rows: Array<{ indent: number; ordered: boolean; text: string }> = []
  let i = start
  while (i < lines.length) {
    const m = LIST_RE.exec(lines[i]!)
    if (m) {
      const indent = m[1]!.replace(/\t/g, '    ').length
      const ordered = /\d/.test(m[2]!)
      // A different list kind at the top level starts a new list.
      if (rows.length && indent <= rows[0]!.indent && ordered !== rows[0]!.ordered) break
      rows.push({ indent, ordered, text: m[3]! })
      i++
    } else if (lines[i]!.trim() !== '' && /^\s+\S/.test(lines[i]!) && rows.length) {
      rows[rows.length - 1]!.text += ` ${lines[i]!.trim()}`
      i++
    } else break
  }
  const root: Extract<Block, { t: 'list' }> = { t: 'list', ordered: rows[0]!.ordered, items: [] }
  const stack: Array<{ indent: number; list: Extract<Block, { t: 'list' }> }> = [{ indent: rows[0]!.indent, list: root }]
  for (const r of rows) {
    while (stack.length > 1 && r.indent < stack[stack.length - 1]!.indent) stack.pop()
    let top = stack[stack.length - 1]!
    if (r.indent > top.indent && top.list.items.length && stack.length < MAX_DEPTH) {
      const parent = top.list.items[top.list.items.length - 1]!
      const sub: Extract<Block, { t: 'list' }> = { t: 'list', ordered: r.ordered, items: [] }
      parent.children.push(sub)
      top = { indent: r.indent, list: sub }
      stack.push(top)
    }
    top.list.items.push({ c: parseInline(r.text), children: [] })
  }
  return { block: root, next: i }
}

export function parseBlocks(src: string, depth = 0): Block[] {
  const lines = src.replace(/\r\n?/g, '\n').split('\n')
  const blocks: Block[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]!
    if (line.trim() === '') {
      i++
      continue
    }
    const fence = FENCE_RE.exec(line)
    if (fence) {
      const marker = fence[1]![0]!
      const lang = line.trim().slice(fence[1]!.length).trim().split(/\s+/)[0] ?? ''
      const body: string[] = []
      i++
      while (i < lines.length && !(new RegExp(`^ {0,3}${marker}{${fence[1]!.length},}\\s*$`).test(lines[i]!))) body.push(lines[i++]!)
      i++ // closing fence
      blocks.push({ t: 'code', lang: lang.replace(/[^\w+#.-]/g, '').slice(0, 24), v: body.join('\n') })
      continue
    }
    const h = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line)
    if (h) {
      blocks.push({ t: 'heading', level: h[1]!.length as 1 | 2 | 3 | 4 | 5 | 6, c: parseInline(h[2]!) })
      i++
      continue
    }
    if (HR_RE.test(line)) {
      blocks.push({ t: 'hr' })
      i++
      continue
    }
    if (/^ {0,3}>/.test(line)) {
      const q: string[] = []
      while (i < lines.length && /^ {0,3}>/.test(lines[i]!)) q.push(lines[i++]!.replace(/^ {0,3}> ?/, ''))
      blocks.push({ t: 'quote', c: depth < MAX_DEPTH ? parseBlocks(q.join('\n'), depth + 1) : [{ t: 'p', c: parseInline(q.join(' ')) }] })
      continue
    }
    if (LIST_RE.test(line) && !HR_RE.test(line)) {
      const { block, next } = parseList(lines, i)
      blocks.push(block)
      i = next
      continue
    }
    if (line.includes('|') && i + 1 < lines.length && TABLE_SEP_RE.test(lines[i + 1]!) && lines[i + 1]!.includes('-')) {
      const head = splitRow(line).map((c) => parseInline(c))
      i += 2
      const rows: Inline[][][] = []
      while (i < lines.length && lines[i]!.trim() !== '' && lines[i]!.includes('|')) rows.push(splitRow(lines[i++]!).map((c) => parseInline(c)))
      blocks.push({ t: 'table', head, rows })
      continue
    }
    const para: string[] = []
    while (i < lines.length && lines[i]!.trim() !== '' && !FENCE_RE.test(lines[i]!) && !/^ {0,3}#{1,6}\s/.test(lines[i]!) && !/^ {0,3}>/.test(lines[i]!) && !(LIST_RE.test(lines[i]!) && para.length)) {
      para.push(lines[i++]!.trim())
    }
    if (para.length === 0) {
      para.push(lines[i++]!.trim())
    }
    blocks.push({ t: 'p', c: parseInline(para.join(' ')) })
  }
  return blocks
}

/** Source (SKILL.md) -> render tree. Frontmatter is removed; dangerous HTML is stripped before parsing. */
export function parseMarkdown(src: string): Block[] {
  const { body } = splitFrontmatter(src.slice(0, MAX_INPUT))
  return parseBlocks(stripDangerousHtml(body))
}
