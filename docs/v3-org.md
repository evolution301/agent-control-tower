# The room, the team and the deadline

Everything in this page is optional. A tower that never declares a leader, a
secretary or a group runs exactly as it did before 0.18.0. What follows is the
shape a tower takes once more than a handful of roles share one board.

## The five pieces of a role

A bot that answers a dispatch is five files, not one:

| piece | where | written by |
| --- | --- | --- |
| the role entry | `tower.toml` → `roles.<name>` | `tower role add` |
| the DSH session | `tower.toml` → `adapters.dsh.sessions.<name>` | `tower bootstrap`, `role add --session` |
| the sidebar title | `tower.toml` → `adapters.dsh.role_titles.<name>` | `tower bootstrap` |
| the charter | `roles/<name>/AGENT.md` | `tower role add` |
| the sidebar registration | `$DSH_HOME/storages/workspace.json` | `tower dsh register --apply` |

`tower role add --guided --template b2b-site` builds all five for a whole team
and then prints the ones it could not finish. `tower dsh status` says, per role,
which piece is missing; `tower role list --tree` shows the same next to the org
chart. The sidebar file is DSH's, so a restart is required — the command says so.

## Kickoff: agree before the first dispatch

```
tower kickoff "Fix /showroom/LCP from 76 to < 90" --role backend --role qa
tower group show G-0001          # what the package still needs
tower dispatch backend --group G-0001 --due 90m
```

`kickoff` writes `.tower/groups/<G>/package.json` with four sections:

- **plan** — the chosen approach, the alternatives, and why
- **assignments** — role → work → write scope
- **acceptance** — commands and thresholds that decide pass/fail
- **deps / merge_order** — who goes first, who merges

A draft package is fine to look at and refused to dispatch from when
`[kickoff].require = true`. The dispatch prompt carries the plan, the role's own
part, the acceptance command and the merge order — as a block, not as the whole
file.

## One task, one room

A dispatch auto-creates a group from the brief (`[groups].auto`). The room walks

```
created → discussing → working → reviewing → closed
```

and `tower accept` closes it, archiving the room to
`.tower/conversations/<G>-closed.md`, once no task in it is still open. Unrelated
work must not share a room: context that is read once is billed every turn.

## Two layers of command

```toml
[roles.lead]
secretary = true

[roles.frontend-lead]
leader = true
report_to = "lead"

[roles.frontend]
report_to = "frontend-lead"
```

When any of those keys appear, `tower_say` enforces the chain:

- worker → its lane leader
- leader → its members, or the secretary
- secretary → leaders, not members

A refused note is still filed on the board (the secretary can read it) and the
sender gets one circuit-breaker line, never a silent drop.

## Deadlines and heartbeats

Every dispatch carries a deadline: `--due 90m`, `--due 2h`, `--due 3d`,
`--due 2026-11-24 18:00`, or the default `4h` (`[deadline].default`). The board
stamps `last_heartbeat` on every status change, so a task that has not moved is
visible as such.

`tower supervise` asks the **lane leader** first when a deadline passes, and the
leader is told to report the conclusion rather than redo the work. Only after the
second unanswered pass does the escalation reach the secretary's inbox as
`overdue_escalation`.

## The shared skill library

`.tower/skills` is one library for the whole tower:

```
tower skill search "cloudflare cache hit"
tower skill show cf-cache-hit
tower skill patch cf-cache-hit --file /tmp/new-body.md --note "works with enable_js on"
tower skill stats
```

A patch bumps the version and keeps the previous body under
`.tower/skills/.history/`. Dispatch injects only the matching index lines, so a
library of fifty skills costs four lines per prompt.
