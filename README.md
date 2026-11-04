# agent-control-tower

**A local control tower for role-based AI agent teams.** You talk to one lead agent; the lead dispatches tasks to specialised role agents, collects their reports, verifies the claims, and only then marks work as done.

```
                    ┌──────────────────────────────┐
   your message ───▶│            LEAD              │
                    │  brief → dispatch → verify   │
                    └───────┬──────────┬───────────┘
                            │ task     │ task
                    ┌───────▼───┐  ┌───▼───────┐
                    │  backend  │  │   ui      │   roles work in isolated
                    │  agent    │  │   agent   │   workspaces, write reports
                    └───────┬───┘  └───────┬───┘
                            │ report   │ report
                    ┌───────▼──────────▼───────────┐
                    │  inbox + ledger + verifier   │  ← evidence, not vibes
                    └──────────────────────────────┘
```

It is deliberately small: a task board, an inbox, a ledger, four adapters, and a strict report contract. No SaaS, no database, no daemon — a CLI plus files you can commit and diff.

---

## Why this exists

Single-agent workflows break down at scale. One agent juggling copy, code, QA and deployment forgets constraints, loses context, and starts grading its own homework.

The pattern that actually works (and that this repo packages):

1. **One lead, many roles.** Work only enters through the lead. Roles never talk to each other; the lead routes.
2. **Every role owns a directory.** No two agents write the same file. Overlap is a merge conflict waiting to happen.
3. **Every task ends in a structured report.** Summary / Files / Checks / Open issues — the same four sections, always.
4. **The lead verifies before accepting.** Reported files must exist. Checks must contain the actual result. Optional `verify_commands` run for real. A report that says "all good" without evidence is rejected, not trusted.
5. **Everything is on disk.** Task JSON, append-only `ledger.jsonl`, `inbox.jsonl`. Git gives you full history, for free.

That's it. No orchestration framework, no graph DSL. About 900 lines of Python.

---

## Built for DSH first

The pattern in this repo was extracted from a real [DeepSeek Harness](https://github.com/) deployment:
one lead session steering several long-lived role sessions through a multi-week build (research →
copy → architecture → UI → deploy → SEO), with every delivery verified before acceptance.

So **DSH is the primary adapter**: roles are DSH sessions, dispatch is a queued `session/prompt`, and
the role's report is a file the tower reads back. Session ids live in `tower.toml`; `tower doctor`
refuses to start if one is missing.

Start here → **[docs/dsh-quickstart.md](docs/dsh-quickstart.md)** (create the role sessions, wire the
config, run the loop, plus the five failure modes the reference project hit and how the code prevents
them).

Nothing else depends on DSH: `cli`, `openai` and `mock` adapters follow the same contract, so you can
run the tower without the harness and switch later. See [docs/adapters.md](docs/adapters.md).

---

## Install

```bash
git clone https://github.com/<you>/agent-control-tower.git
cd agent-control-tower
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

Requires Python 3.10+. One runtime dependency (`PyYAML`).

## 60-second demo (no model, no network)

```bash
tower demo
```

```
initialised tower in /tmp/tmp9r9zysyg
[1] dispatched T-0001 -> reported
[2] verify: PASS
[3] accepted T-0001
[4] ledger lines: 5
```

The demo uses the `mock` adapter: it writes a placeholder artifact and a real report, so you can see the whole loop — dispatch, report, verify, accept — deterministically and in CI.

## Real usage

```bash
tower init my-project && cd my-project
```

This creates `tower.toml`, `roles/{backend,frontend,qa}/AGENT.md`, and a `.gitignore`.

Point a role at a real agent (see [adapters](docs/adapters.md) for all four):

```yaml
# tower.toml
roles:
  backend:
    description: Implements data, APIs and infrastructure.
    adapter: cli
    write_scopes: ["server/"]
```

```yaml
adapters:
  cli:
    command: ["claude", "-p", "{prompt_file}", "--permission-mode", "acceptEdits"]
```

Then the loop is five commands:

```bash
tower dispatch backend "Add pagination to /api/orders and cover it with tests"
tower inbox              # what came back
tower accept T-0001      # verifies files + checks + verify_commands, then accepts
# or send it back:
tower rework T-0001 --note "checks section lists no results; re-run the test suite"
```

`rework` re-dispatches the same task with your note appended and increments its round counter. When `max_rounds` is exhausted the tower tells you to escalate to a human instead of looping forever.

---

## Adapters

The adapter is the only place that knows *how* to make an agent act. Same contract, four backends:

| Adapter | What it drives | When to use |
|---|---|---|
| `mock` | nothing (writes a placeholder artifact) | tests, demos, CI |
| `cli` | any agent binary that accepts a prompt (`claude -p`, `codex exec`, custom scripts) | you already have agent CLIs |
| `dsh` | a live [DeepSeek Harness](https://github.com/) session over the local harness RPC | long-running role sessions with browser tools |
| `openai` | any OpenAI-compatible endpoint with a minimal `run_shell` tool loop | hosted models, vLLM, Ollama |

Adding another adapter is ~40 lines: implement `run(AdapterRequest) -> AdapterResult` and register the name in `adapters/base.py`. See [docs/adapters.md](docs/adapters.md).

---

## The report contract

Every dispatched prompt ends with this contract, and roles must satisfy it:

```markdown
## Summary
<2-5 sentences>

## Files
- <path> — <what changed>

## Checks
- <command you actually ran> → <result you actually saw>

## Open issues
- <anything unfinished, or "none">
```

The verifier enforces three things mechanically:

- the required sections are present and non-empty (`accept_requires` in config);
- every line under **Checks** contains a result (`→`), so `- pytest` alone fails;
- every path listed under **Files** exists inside the workspace.

And optionally, `verify_commands` from `tower.toml` run for real before acceptance:

```yaml
verify_commands:
  - "pytest -q"
  - "npm run build"
```

This is the difference between "the agent said it works" and "the tower checked".

---

## Commands

| Command | Purpose |
|---|---|
| `tower init <dir>` | scaffold config + role folders |
| `tower dispatch <role> "<brief>"` | create and run one task |
| `tower batch role1 role2 --brief "..."` | fan one brief out to several roles |
| `tower inbox [--all] [--read]` | reports filed by roles |
| `tower accept <task>` | verify, then accept (or auto-reject to rework) |
| `tower rework <task> --note "..."` | send back and re-dispatch |
| `tower status [--json]` | task board |
| `tower show <task>` / `tower log` | inspect one task / tail the ledger |
| `tower demo` | offline end-to-end demo |
| `tower doctor` | validate config, adapter options, keys, sessions |

## What's on disk

```
my-project/
├── tower.toml              # roles, adapters, verify commands
├── roles/
│   ├── backend/            # this role's workspace (its only write area)
│   │   ├── AGENT.md
│   │   └── T-0001-REPORT.md
│   ├── frontend/
│   └── qa/
└── .tower/                 # machine state — gitignore it, or commit it for audit
    ├── tasks/T-0001.json   # materialised task view
    ├── ledger.jsonl        # append-only event log (source of truth)
    └── inbox.jsonl         # reports, unread flag
```

---

## Design notes worth stealing

- **Roles cannot spawn roles.** Fan-out is a lead decision, which keeps the task tree inspectable.
- **`serial_requests` + `request_gap_s`.** For fragile servers or rate-limited APIs, dispatch one role at a time with a gap. Real deployments need this; the reference project was a 1 GB VPS that fell over under parallel requests.
- **Verification is a first-class CLI verb, not advice in a prompt.** Agents optimise for "report success"; the tower removes the incentive.
- **Reports are files, not messages.** They survive context compaction, are reviewable in PRs, and can be re-parsed months later.

## Roadmap

- [ ] `tower watch` — poll adapters, auto-file reports for long-running CLI agents
- [ ] webhooks/Slack/Telegram notification sink (file-based inbox is default)
- [ ] `verify_commands` per-role overrides
- [ ] cost/token accounting per role
- [ ] `tower report --html` — single-page audit report for a batch

## Docs

- [DSH quickstart](docs/dsh-quickstart.md) — the primary setup, end to end
- [Architecture](docs/architecture.md) — components, state machine, why files instead of a queue
- [Adapters](docs/adapters.md) — mock / cli / dsh / openai, and how to add your own
- [Contributing](CONTRIBUTING.md) — ground rules and PR checklist

## License

MIT — see [LICENSE](LICENSE).
