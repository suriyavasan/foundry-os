# How your company works

Foundry AI OS is built so you do not have to micromanage. You give direction. The company does the rest, and asks you only when it should.

## The people

| Who | What they do |
|---|---|
| **You, the Founder** | Set direction, approve risky things, set the company's standards. |
| **AI Co-founder** | Manages every other agent. Reads your notes, sets goals, starts projects, picks leads, plans hiring, and replies to you. |
| **Leads** | Run a project or a department. Break a project into tasks, give them to the right people, review the results and ask for help when stuck. |
| **Specialists** | Do the work: research, writing, design, engineering, marketing and so on. Each has skills for their role. |
| **HR** | Hires new employees and finds or writes the skills they need. |
| **Reviewers** | Check finished work against the acceptance criteria. |
| **Human team members** | Other people you invite. They appear in the same org chart and can be given tasks. See [Team](team.md). |

Everyone shows up as a circle in the organization view. The ring around a circle shows what they are doing: working, idle, blocked, paused or failed.

## From a thought to finished work

1. You write a note.
2. The AI Co-founder **triages** it: is it an instruction, a change of direction, a requirement, an idea, feedback, a question, background, or nothing to act on? It creates the real goals, projects or tasks and replies with what it did.
3. A project gets a **lead**. If no suitable lead exists, HR hires one.
4. The lead breaks the project into **tasks**, each with a clear definition of done.
5. Specialists do the tasks and save **artifacts** (files such as plans, analyses or pages) you can open.
6. Work is **reviewed**. If it is not good enough, it goes back with comments.
7. When a project finishes, or fails, or gets stuck, the Co-founder is woken up to decide what happens next.

If a task fails, the company does not just give up. A review task is created with the error history, and the options are to retry with someone else, use a different model, split the task, or cancel with a recorded reason.

## HR and skills

A **skill** is a short instruction pack that teaches an employee how to do something well, for example "write a market analysis" or "debug code step by step". A **role template** is a ready-made job description (software engineer, researcher, project lead and so on) with a starting set of skills.

When the company lacks a capability, HR can:

- search the built-in skill library, or a skill published on GitHub,
- write a new skill from scratch,
- create a new role template, and
- hire an employee from a template and assign the skills.

Skills from outside are **scanned first**. An unsafe skill is quarantined and cannot be given to anyone. Agents cannot give skills to themselves. Each new hire gets a small monthly budget and cannot exceed the limits you set.

You can do the same by hand with `agents hire` and `skills install` (see [the command line](cli.md)).

## Schedules

Some work should happen on a rhythm. The company creates these routines for you (times are in your company's time zone):

| Routine | When | What |
|---|---|---|
| Daily stand-up | 09:00 every day | The Co-founder reviews progress and writes a short report |
| Weekly strategy review | Monday 09:30 | Goals, priorities, headcount |
| Daily budget review | 18:00 every day | Spending against budgets |
| Daily reflection | 20:00 every day | Posts a reflection (see below) |
| Failed-run check | 08:00 every day | Looks at what failed and why |
| Skills review | 10:00 on the 1st of each month | HR reviews which skills are used and missing |
| Heartbeat | Every so often | A safety check that nothing was missed |

Leads also get a health check for each project. You can see and pause all of these in the routines view.

## Memory

The company keeps a shared memory so people do not have to be told twice.

- **Requirements and background** you write in a note become *verified* memory automatically, because they came from you.
- Agents can *propose* a memory (for example a lesson learned after a failure). The Co-founder checks it before it counts.
- Verified memory is shown to the agents it applies to.
- Secrets are rejected from memory.

## Reflections and standards

The AI Co-founder regularly writes **reflections**: what is going well, a risk it sees, a lesson from a failed project, or a suggestion for a company standard. Each one can point to the tasks or files that prompted it.

You respond with one of: **endorse**, **challenge**, **refine**, or a plain comment. Your response wakes the Co-founder to reply or revise.

**Standards are yours.** A suggested standard (for example "every external email must be reviewed before sending") becomes a rule only when *you* endorse it. The AI Co-founder can never adopt or retire a standard. Once adopted, standards are given to every agent as part of their context, and you can see and retire them in the Standards list.

> Reflections and standards are built but still in progress at the time of writing.

## Command Center and slash commands

The Command Center is where you talk to the company. **Every input gets an output.**

- **Plain text** is saved as a note. The Co-founder is woken up within a few seconds. Your message shows as *pending*, then *processing* with live progress, then *answered* with the result. If something goes wrong or nothing answers in 15 minutes, the system writes an explanation and retries once. You can retry yourself.
- **Slash commands** run instantly and are never sent to an AI:

| Command | Does |
|---|---|
| `/help` | Lists every command |
| `/status` | Company status, spend and what is running |
| `/goals` | Active goals |
| `/agents` | Everyone and what they are doing |
| `/tasks [status]` | Tasks, optionally one status |
| `/spend [day\|month]` | What the company has spent |
| `/approvals` | What is waiting for your decision |
| `/standards` | The adopted standards |
| `/pause`, `/resume` | Stop or restart new work |
| `/goal <text>` | Create a goal |
| `/task <title> [@agent]` | Create a task, optionally assigned |
| `/hire <template> [reason]` | Ask HR to hire from a template |
| `/approve <id>`, `/reject <id> [note]` | Decide an approval |

> The Command Center is built but still in progress. The `note`, `status`, `goal` and `approvals` commands in [the command line](cli.md) already do similar things.
