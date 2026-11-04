# Architecture

## One lead, N roles, one direction of flow

```
user ──▶ lead ──▶ dispatch ──▶ adapter ──▶ role workspace
                    ▲                            │
                    │                            ▼
              verify / accept ◀── inbox ◀── REPORT.md
```

Three invariants keep this simple and debuggable:

1. **Every piece of work is a task with an id** (`T-0001`) and a role. Untracked work does not exist.
2. **Every task ends in a report file** with fixed sections. Free-form chat is not a deliverable.
3. **Acceptance requires verification** by the tower, not by the role's own summary.

## Components

| Module | Responsibility |
|---|---|
| `config.py` | find/parse `tower.toml`, define workspace layout, allocate task ids |
| `board.py` | task records, append-only ledger, inbox, state transitions |
| `dispatch.py` | render the prompt contract, call the adapter, parse the report |
| `verify.py` | structural checks + `verify_commands`, produce accept/reject |
| `cli.py` | one verb per command, human-readable output |
| `adapters/*` | the only place that talks to an agent runtime |

## State machine

```
pending ──dispatch──▶ claimed ──▶ in_progress ──ok──▶ reported ──verify──▶ accepted
                                      │                     │
                                      └──adapter error──▶ failed
                                                            │
                                     reject / lead request──┴──▶ rework ──dispatch──▶ (round+1)
```

`rework` increments `round`. When `round > max_rounds`, the tower stops re-dispatching and tells the
caller to escalate to a human. That single rule prevents the classic "two agents ping-pong forever"
failure mode.

## Why files instead of a queue/database

- **Inspectable**: `cat .tower/tasks/T-0001.json` shows everything, including the report.
- **Git-native**: a batch of work is a diff; the ledger is an append-only audit trail.
- **Crash-safe**: no daemon to restart. An interrupted dispatch is visible as a task stuck in
  `in_progress`, and `tower rework <id>` resumes it.

The ledger is the source of truth; task JSON files are a materialised view, safe to rebuild with a
small script if they are ever corrupted.

## Prompt contract

`dispatch.render_prompt()` produces the same skeleton every time:

1. role identity + its workspace path,
2. write scope (the only paths it may modify),
3. the four report sections, verbatim,
4. "never claim a check you did not run",
5. "do not ask the human — decide and record assumptions",
6. the brief.

Uniform prompts are what make heterogeneous roles comparable; a QA role and a UI role produce
reports the same verifier can read.

## Concurrency

Default is sequential: one dispatch at a time, which is what you want for file-based workspaces.
`tower batch` loops over roles with `serial_requests` / `request_gap_s` protecting fragile targets.

If you need true parallelism, run one tower workspace per role (separate working directories) and
have a thin supervisor merge the inboxes. Keeping that outside the core avoids a lock manager here.
