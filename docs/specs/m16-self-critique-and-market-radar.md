# M16: Self-critique and market radar

Status: WP-2 (services, APIs, guardrails, routines, hiring, runtime tools) implemented on 2026-10-27; WP-1 (schema,
fetcher, ingest) and WP-3 (web) are separate work packages of the same milestone. Decisions D47 to D49 in
`docs/architecture.md`; API in `docs/api.md` ("Milestone 16"); security in `docs/security.md` section 8.

## 1. Goal

The company reads the outside world every day, judges its own product every week, and turns both into work without a
human in the loop, inside the limits the human set.

```
 radar_sources -> fetcher (worker job, no model) -> radar_items --> daily "market-radar" routine
  100 seeds        robots, per-host rate, SSRF       deduped,        Market Researcher (else the Co-founder):
  + owner/agent    guard, size caps                  matched,        digest, <= 5 page reads, <= 4 web searches,
                                                     flagged         brief, ideas with evidence
 weekly "weekly-self-critique" (QA / Designer / Co-founder) --\                       |
 weekly "ui-inspiration" (Product Designer) -------------------+--> radar_ideas (capped, deduped, expiring)
                                                                          |
 daily "ideas-triage" (Co-founder) accepts into a project (or one new goal a week) -> M12 engine builds it
 watchdog D10 keeps the backlog moving
```

## 2. Surface (WP-2)

* **Owner API** `/api/companies/{cid}/radar` (`app/api/routes/radar.py`, schemas in `app/schemas/radar.py`): status,
  sources, keywords, items, briefs, ideas, fetch-now. Members and viewers read; owners and admins write; agents get 403;
  non-members get 404. Lists are `{items, next_cursor}`.
* **Agent API** `/api/agent/v1/radar` (`app/api/routes/agent_api_radar.py`, included into the agent router so the
  guardrail gate runs first): `GET digest`, `POST read`, `POST briefs`, `POST ideas`, `POST ideas/{id}/decide`,
  `POST ui-search`, `POST keywords`, `PATCH keywords/{id}`, `POST sources`. Eight runtime tools map onto them
  (`company_radar_digest`, `_read`, `_brief`, `_propose`, `_decide`, `_keywords`, `_ui_search`, `_add_source`).
* **Services** in `app/radar/`: `ideas.py` (backlog, caps, duplicates, deciding), `briefs.py`, `digest.py` (owner feed and
  status, agent digest and self-critique packet, routine skip rules, counts), `page_read.py`, `ui_search.py`,
  `access.py` (who may do what), `search_cap.py` (4 web searches a day in the radar routine).

### Roles (enforced in services; the tool lists in `agent_toolkit.radar_tools_for` come from the same table)

| Capability | Tool | Who |
|---|---|---|
| read the digest | `company_radar_digest` | research, design, QA, Co-founder, project leads |
| read a page | `company_radar_read` | any agent with the `web` toolset; the Co-founder (it has none, the control plane does the fetch) |
| write a brief | `company_radar_brief` | research, design, QA, Co-founder |
| propose an idea | `company_radar_propose` | research, design, QA, Co-founder |
| decide an idea | `company_radar_decide` | Co-founder; a project lead for its own projects |
| keywords / feeds | `company_radar_keywords`, `_add_source` | research, Co-founder |
| UI references | `company_radar_ui_search` | research, design |

### Ideas

* Proposing needs evidence (item, artifact or task ids of this company, or http(s) links), respects
  `radar_max_ideas_per_day` (rolling 24 h) and `radar_max_open_ideas`, and refuses near-duplicates (title similarity
  >= 0.85, the M12 function) of open ideas, ideas rejected or accepted in the last 14 days, open work tasks and tasks done
  in the last 7 days: `409 duplicate_idea` names the existing record.
* `priority_score = round(impact * confidence * 4 / effort)`.
* Accepting creates a work task through the M12 task core with the caller's run, so every M12 cap applies
  (`max_work_tasks_per_project`, duplicate detection, plan approval in supervised mode). The task carries the idea id
  (`idempotency_key radar-idea:<id>`), a bounded neutralised problem and proposal, and the **count** of evidence items,
  never item text. An idea whose own text trips the injection detector can only be accepted by a person.
* `new_goal` (Co-founder only, `radar_max_goals_per_week`, `radar_auto_accept` on) creates an active goal, its project
  and the first task in one transaction. With `radar_auto_accept` off only people accept; agents may still reject or merge.
* An accepted idea becomes `done` when its task is done (task transition hook); open ideas older than 30 days expire (D10).

## 3. Routines (`builtin_schedules.RADAR_SCHEDULES`, `ensure_radar_schedules`)

| Name | Agent | Cron (company tz) | Skipped (no model run) when |
|---|---|---|---|
| `market-radar` | Market Researcher, else Co-founder | `30 7 * * *` | radar off, or no new items since the last daily brief |
| `ideas-triage` | Co-founder | `0 11 * * 1-5` | radar off, or no open ideas |
| `weekly-self-critique` | QA, else Product Designer, else Co-founder | `0 10 * * 5` | radar or `self_critique_enabled` off, or nothing happened in the week |
| `ui-inspiration` | Product Designer (none = no schedule) | `0 10 * * 3` | radar off |

Fired tasks get a **counts-only** line ("Radar: 5 new items from 2 sources, 0 flagged; 1 open ideas."), never item text.
The routines follow the roles: `ensure_radar_schedules` runs at company creation and from watchdog D10 every minute, so a
hire moves `market-radar` to the Market Researcher and a retirement moves it back. The M12 strategy digest gains a
"Radar:" block (numbers and, when no Market Researcher exists and the radar had new items all week, an instruction to
request one hire with `company_request_hire`, template `market-researcher`).

**D9 initiative** now also carries the three best open ideas as wake reasons (ids; titles inside `<untrusted>` tags).
**D10 `idea_backlog`**: ideas open over 72 h wake the Co-founder twice, 24 h apart, then one info alert
(`radar_ideas_waiting`); a decision settles the ladder; bounded like D1 to D9.

**Search cap (code, not prompt).** `POST /guardrails/check-tool` for `web_search` from a task fired by `market-radar` is
limited to 4 a day per company (`rate_limit_hits`, key `radar_search_queries_per_day`); other tasks keep only the
general `web_fetches_per_hour` limits.

## 4. Hiring

`roles/builtin/market-researcher.yaml` (family research, toolsets company, file, web, search, skills, todo, vision) and the
company skill `roles/skills/market-radar-playbook`. The Co-founder runs the radar until it hires one.

## 5. Untrusted text

1. Ingest (WP-1): plain text, control and bidi characters stripped, `<untrusted` neutralised, injection flags stored.
2. Agent API: outside text is returned **only** in fields named `content` (item title and excerpt, idea text, brief
   themes, page text, UI reference copy). A test asserts no item title appears outside `content`.
3. Runtime: `guard.tag_untrusted_fields(payload, keys, source_of)` (`tag_artifact_excerpts` is now a thin wrapper) wraps
   every `content` as `<untrusted source="radar:<slug>">`, `radar-idea:<id>`, `radar-brief:<id>`, `radar-page` or
   `radar-ui`, and reports the injection categories to `/guardrails/flags`. The MCP server reuses the same handlers.
4. Authority: nothing in a radar item can change permissions, budgets, sources, owner keywords or approve anything. A
   flagged item's page is read only when the agent names that item.

## 6. Owner decisions applied

New company: radar on. The Co-founder may accept ideas (`radar_auto_accept`), at most 1 new goal a week, 5 new ideas a
day, 25 open. Agents may add up to 20 feed sources (`radar_agent_sources`, stored disabled as a suggestion when off).
4 web searches a day in the radar routine. One Page Love UI search is on (a guardrail `deny` rule on `radar.ui_search`
turns it off). The Co-founder runs the radar until a Market Researcher is hired.

## 7. Deviations and limits

* `radar.fetch` is **not** a registered guardrail action: the fetcher is gated by `radar_enabled`, the company status,
  the emergency stop and the daily request budget (WP-1), but a human `deny` rule cannot stop it. Reserved for later.
* The UI-search cache is per process (a restart empties it), 24 h, 200 entries.
* Injection categories from the backend detector use the e-mail guard's names ("instruction override"); the runtime
  reports its own ("instruction_override").
* `GET /radar` computes its status in `app/radar/digest.py`, not in `service.get_status`.

## 8. Verified and not verified

Verified with fakes only (no real network): every route and role, caps, duplicates, tenancy, the injection layers, the
routines and their skips, D9/D10, the search cap, the page reader (HTML fixtures, robots, SSRF, size, flags), UI search
parsing (JSON and SSE fixtures), and one end-to-end run on the real engine (`backend/tests/test_m16_e2e.py`).
Not verified: any real feed, Hacker News, One Page Love or Siteinspire response (their MCP result shapes are assumed from
the research and parsed defensively), DuckDuckGo behaviour of `web_search` for `site:` queries, real model quality or cost
of a radar run, `vision_analyze` on real screenshots.
