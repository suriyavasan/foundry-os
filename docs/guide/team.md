# Team

Foundry AI OS is for you first, but you can bring other people in. They sit in the same organization view as your AI employees.

## Roles

| Role | Can |
|---|---|
| **Owner** | Everything, including the instance-wide emergency stop |
| **Admin** | Manage the company, its settings, guardrails and team. Cannot do owner-only things such as the instance-wide emergency stop |
| **Member** | A *human team member*. Appears in the org chart, has a manager, can be given tasks and reviews, and can decide approvals assigned to them |
| **Viewer** | Read only |

Roles are enforced by the program on every action, not just by hiding buttons. People who are not in your company cannot even see that it exists.

## Invite someone

1. Open the Team panel and choose **Invite**.
2. Enter their email, a role, a manager and a job title.
3. Send it.

What happens next depends on whether the company has email set up (see [Reach it from anywhere](reach-it-from-anywhere.md#company-email)):

- **With company email:** the invitation is emailed from the company address.
- **Without it:** the screen shows a link you copy and send yourself. No email is sent. The screen says so.

The invitation:

- works once,
- expires after 7 days,
- is tied to that email address,
- can be revoked before it is used.

The person opens the link, chooses a username and password (or signs in if they already have an account on this install) and joins.

## Humans at work

A human member can be assigned tasks like an AI employee. The AI Co-founder treats them as colleagues, assigns work at a polite pace, and can see how much they have on their plate. It never sees their credentials. Agents are never started for human tasks; the person gets a notification instead and completes the task in the screen (comment, attach a file, mark done or blocked).

A bell icon collects what needs you: tasks assigned, reviews requested, approvals needed, and replies from the Co-founder. With company email connected, people can opt in to email notifications for each kind.

## Removing someone

Remove a member from the Team panel. Their open tasks are reassigned and their history stays.

## Security for shared installs

- Turn on two-factor sign-in for admins, especially if the install is reachable from the internet.
- Sign-in is slowed after repeated wrong passwords.
- Each person can see and end their own sessions, and create personal API tokens for the command line.
- Agents can never create users, invitations or tokens.
