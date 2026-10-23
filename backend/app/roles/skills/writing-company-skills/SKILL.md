---
name: writing-company-skills
description: "Write a short, procedural SKILL.md that passes the security scan."
version: 1.0.0
author: Company OS
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [skills, authoring, skill-md, documentation, hr]
    category: hr
    related_skills: [hiring-playbook]
---

# Writing company skills

A skill is a directory with a `SKILL.md`: YAML frontmatter plus instructions an employee loads when the situation matches. Write one when the company has a repeatable procedure that no installed skill covers.

## Format

```markdown
---
name: kebab-case-slug            # must equal the slug; a-z, 0-9, "-", max 64 chars
description: "One line: what it does and when to use it."
version: 1.0.0
author: <your name or team>
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [lowercase, tags, used, for-matching]   # these tags decide who gets assigned work
    category: sales
    requires_toolsets: [web]      # only if the skill really needs them
---

# Title
## When to use
## Procedure
## Pitfalls
```

## Procedure

1. **Scope it.** One skill, one job. If the description needs "and", split it.
2. **Name the trigger.** "When to use" lists 3-5 concrete situations and one "Do not use for" line.
3. **Write numbered steps.** Each step is an action, then "Done when ..." so the employee knows when to move on. Prefer decisions and checklists over background prose.
4. **Name tools by what they do** (search the web, write a file, submit an artifact), not by internal paths.
5. **Add pitfalls** you would warn a new hire about, each in one line.
6. **Keep it short.** Under 150 lines. Move long reference material to `references/<topic>.md` and link it; put scripts, if any, in `scripts/`.
7. **Choose tags carefully.** Lowercase, 3-8 tags: the role, the domain, the key activities. Tasks are matched to employees by these tags.
8. **Submit** with `company_author_skill` (slug, name, description, tags, body_markdown, optional files). Read the response: `active` means it is installed; `quarantined` means the scanner flagged it.

## Passing the scan

Authored skills are scanned as untrusted. A flagged skill is quarantined and never reaches an employee. Avoid:

- shell snippets that download and run code, or read secrets or environment variables;
- sending data to external hosts, or instructions to ignore, override or reveal system prompts;
- invisible or look-alike Unicode characters;
- commands that change permissions, credentials or startup files.

If a legitimate example trips the scan, describe it in prose instead of showing the command.

## Updating

Re-authoring the same slug replaces the content and bumps the version. Say what changed in the first line of "Pitfalls" or in a short changelog section.

## Pitfalls

- Copying generic advice the model already knows. Write what is specific to this company.
- Putting credentials, customer data or personal data in a skill.
- Over-broad tags such as `work` or `general`; they match everything and help nothing.
