# Third-party material in the web app

Runtime dependencies are listed in `package.json` (with their own licences in `node_modules`). This file records what needs
a decision or a credit beyond that.

## Illustrated faces (M20)

People on the canvas (agents, members, the Founder) are drawn with generated illustrated faces. Nothing is fetched at run time:
the faces are produced in the browser from a seed (the agent or member id) by DiceBear, and shown as `data:` images.

| Package | Version | What | Licence |
| --- | --- | --- | --- |
| `@dicebear/core` | 9.x | Avatar generator | MIT |
| `@dicebear/lorelei` | 9.x | Face style used for AI agents. Artwork by Lisa Wischofsky | CC0 1.0 (public domain dedication) |
| `@dicebear/notionists` | 9.x | Face style used for humans. Artwork by Zoish | CC0 1.0 (public domain dedication) |

- Licences checked on 2026-10-30 at <https://www.dicebear.com/licenses/>. CC0 needs no attribution; the creators are credited here
  as a courtesy.
- Core is pinned to 9.x because the style packages declare `@dicebear/core ^9` as a peer dependency; do not move it to 10 until
  the style packages do.
- The generated SVG carries a licence `<metadata>` block; it is stripped from the in-page copy to keep it small (`lib/faces.ts`).
  The artwork is CC0, so nothing is lost.
- Style packages can be swapped without touching callers: only `src/lib/facesImpl.ts` imports DiceBear.
