import clsx from 'clsx'
import { Fragment, createElement, useMemo, type ReactNode } from 'react'
import { parseMarkdown, type Block, type Inline } from '@/lib/markdown'

/** Renders the safe tree from lib/markdown. React escapes all text; there is no raw HTML path. */
function renderInline(nodes: Inline[]): ReactNode {
  return nodes.map((n, i) => {
    switch (n.t) {
      case 'text':
        return <Fragment key={i}>{n.v}</Fragment>
      case 'code':
        return (
          <code key={i} className="rounded-sm bg-raised px-1 py-0.5 font-mono text-[0.92em] text-fg">
            {n.v}
          </code>
        )
      case 'strong':
        return (
          <strong key={i} className="font-semibold text-fg">
            {renderInline(n.c)}
          </strong>
        )
      case 'em':
        return <em key={i}>{renderInline(n.c)}</em>
      case 'link':
        return (
          <a key={i} href={n.href} target={n.href.startsWith('#') ? undefined : '_blank'} rel="noopener noreferrer nofollow" className="text-accent-fg underline underline-offset-2 hover:text-accent-hover">
            {renderInline(n.c)}
          </a>
        )
    }
  })
}

const HEADING_CLASS = ['text-[17px]', 'text-[15px]', 'text-[14px]', 'text-[13px]', 'text-[13px]', 'text-[12.5px]']

function renderList(b: Extract<Block, { t: 'list' }>, key: number | string): ReactNode {
  const Tag = b.ordered ? 'ol' : 'ul'
  return (
    <Tag key={key} className={clsx('my-2 space-y-1 pl-5', b.ordered ? 'list-decimal' : 'list-disc')}>
      {b.items.map((it, i) => (
        <li key={i} className="pl-0.5">
          {renderInline(it.c)}
          {it.children.map((c, j) => renderList(c, j))}
        </li>
      ))}
    </Tag>
  )
}

function renderBlocks(blocks: Block[]): ReactNode {
  return blocks.map((b, i) => {
    switch (b.t) {
      case 'heading':
        // Panel titles are h1/h2 in the drawer; a document's headings start at h3.
        return createElement(`h${Math.min(6, b.level + 2)}`, { key: i, className: clsx('mt-4 mb-1.5 font-semibold text-fg first:mt-0', HEADING_CLASS[b.level - 1]) }, renderInline(b.c))
      case 'p':
        return (
          <p key={i} className="my-2 leading-relaxed">
            {renderInline(b.c)}
          </p>
        )
      case 'code':
        return (
          <pre key={i} tabIndex={0} aria-label={b.lang ? `${b.lang} code` : 'Code'} className="my-2 max-h-96 overflow-auto rounded-md border border-line bg-raised p-3 font-mono text-[12px] leading-relaxed text-fg">
            <code>{b.v}</code>
          </pre>
        )
      case 'list':
        return renderList(b, i)
      case 'quote':
        return (
          <blockquote key={i} className="my-2 border-l-2 border-line-strong pl-3 text-fg-3">
            {renderBlocks(b.c)}
          </blockquote>
        )
      case 'hr':
        return <hr key={i} className="my-4 border-line" />
      case 'table':
        return (
          <div key={i} className="my-2 overflow-x-auto">
            <table className="w-full border-collapse text-left text-[12.5px]">
              <thead>
                <tr>
                  {b.head.map((c, j) => (
                    <th key={j} scope="col" className="border-b border-line-strong bg-raised px-2.5 py-1.5 font-semibold text-fg">
                      {renderInline(c)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {b.rows.map((r, j) => (
                  <tr key={j} className="border-b border-line last:border-b-0">
                    {r.map((c, k) => (
                      <td key={k} className="px-2.5 py-1.5 align-top">
                        {renderInline(c)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
    }
  })
}

export function Markdown({ source, className }: { source: string; className?: string }) {
  const blocks = useMemo(() => parseMarkdown(source), [source])
  return <div className={clsx('text-[13px] break-words text-fg-2', className)}>{renderBlocks(blocks)}</div>
}
