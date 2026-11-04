# agent-control-tower

**You talk to one agent. It dispatches the work to specialised role agents, collects their reports, checks the evidence, and only then calls it done.**

A local control tower for role-based AI agent teams. No SaaS, no database, no daemon — a CLI and a folder of files you can commit, diff and audit.

```
                    ┌──────────────────────────────┐
   your message ───▶│            LEAD              │
                    │  brief → dispatch → verify   │
                    └───────┬──────────┬───────────┘
                            │ task     │ task
                    ┌───────▼───┐  ┌───▼───────┐
                    │  backend  │  │   ui      │   each role owns a directory
                    │  agent    │  │   agent   │   and cannot write outside it
                    └───────┬───┘  └───────┬───┘
                            │ report   │ report
                    ┌───────▼──────────▼───────────┐
                    │  inbox + ledger + verifier   │  ← evidence, not vibes
                    └──────────────────────────────┘
```

---

## 1. The problem this solves

You can already get one agent to write code, one to write copy, one to review. What breaks is **the handoff between them**, and it breaks in five predictable ways:

| What actually happens | What it costs you |
|---|---|
| **Self-grading.** The agent that did the work also reports "done, all tests pass". It never ran the tests; it wrote a plausible summary. | You ship a bug and only find it in production. |
| **Context rot.** A long session compacts its own history. The deliverable was "in the conversation", so after compaction it is gone. | Re-doing work, contradicting earlier decisions, circular rounds. |
| **Silent collisions.** Two agents edit the same file in the same round. Both report success. Last write wins, nobody notices. | Lost work, regressions nobody can explain. |
| **No audit trail.** Which role changed what, when, on which instruction? Answer: scattered chat logs across several windows. | You cannot review, reproduce, or hand the project to someone else. |
| **Ping-pong.** "Fix it" → "fixed" → "still broken" → "fixed again". Nothing tracks rounds or forces escalation. | Hours burned, and the human becomes the message bus. |

`agent-control-tower` is the missing middle layer: one place where work is **dispatched**, **reported**, **verified** and **recorded**.

---

## 2. Who it is for

**Good fit**

- **You already run 2–8 agent sessions** (DSH, Claude Code, Codex, Cursor, Aider, straight API calls) and you are the one copy-pasting between them.
- **Indie devs / small teams** building something multi-part: an app (backend + frontend + QA), a content site (research → copy → design → SEO), a data pipeline, a release checklist.
- **Agencies / freelancers** who need to show a client *what was done and how it was verified* — the ledger and reports are that answer.
- **Anyone who has been burned** by an agent saying "done" when it was not.
- **CI / automation** that wants agent work to be reproducible: `tower demo` runs the whole loop offline in under a second.

**Not a good fit**

- One-shot tasks ("rename this variable") — a single prompt is faster.
- You want agents to talk to each other freely — this design is deliberately *hub-and-spoke*; roles never message each other.
- You want a hosted dashboard, multi-tenant auth, or a queue service — this is a local CLI by design (add adapters, not a backend).

---

## 3. What you get

**Before / after**

| Without the tower | With the tower |
|---|---|
| You paste briefs between windows | `tower dispatch backend "…"` — the tower renders the brief, contract and write scope |
| "Done ✅" with no evidence | Report must contain **Files** (must exist on disk) and **Checks** (must carry the result); `verify_commands` run for real |
| Deliverables live in chat | Deliverables are files in `roles/<role>/`, reports are `<task>-REPORT.md` |
| Nobody knows who changed what | Append-only `.tower/ledger.jsonl`: every create / dispatch / status change, timestamped |
| Rounds loop forever | `round` counter + `max_rounds`; the tower stops and tells you to escalate |
| Two agents overwrite each other | `write_scopes` per role; one role, one directory |
| Work is lost after context compaction | Task JSON + reports are on disk; `tower rework T-0012` resumes with the note attached |
| You cannot hand the project over | `git log` of reports and tasks *is* the handover document |

**Concrete benefits**

1. **You stop being the message bus.** One entry point, `tower dispatch`, and one exit point, `tower inbox`.
2. **"Done" becomes checkable.** Acceptance is a command that runs checks, not a sentence you have to trust.
3. **Failures surface early and cheaply.** Rejected reports go back with the *reasons* attached (`max_rounds` caps the loop).
4. **The paper trail is free.** Because state is files, git gives you history, blame and diffs without any extra tooling.
5. **Runtime-agnostic.** Roles can be DSH sessions, local agent CLIs, hosted APIs, or a deterministic mock — same contract, switchable per role.
6. **Small enough to read.** ~900 lines of Python, one dependency (`PyYAML`), 11 offline tests. You can audit it before you run it against your repo.

---

## 4. How it works (30 seconds)

1. **Lead** = you, or your main agent session. It is the only entry point.
2. **Role** = an agent with a description and a write scope, defined in `tower.toml`.
3. **Dispatch** = the tower renders a prompt containing: role identity, its workspace, its write scope, the four report sections, and the brief. Then it calls the role's adapter.
4. **Report** = the role must return/leave:

   ```markdown
   ## Summary
   ## Files
   ## Checks        (every line must carry a result: `pytest -q → 41 passed`)
   ## Open issues
   ```

5. **Verify & accept** = the tower rejects reports whose files do not exist or whose checks have no results, runs `verify_commands`, then marks the task accepted — or sends it back with reasons.

That is the whole system. The discipline is the product.

---

## 5. Built for DSH first

The pattern was extracted from a real [DeepSeek Harness](https://github.com/) deployment: one lead session steering several long-lived role sessions through a multi-week build (research → copy → architecture → UI → deployment → SEO), verifying every delivery.

So **DSH is the primary adapter**: roles are DSH sessions, dispatch is a queued `session/prompt`, and the report is a file the tower reads back. Session ids live in `tower.toml`; `tower doctor` refuses to start if one is missing.

→ **[docs/dsh-quickstart.md](docs/dsh-quickstart.md)** — create role sessions, wire config, run the loop, plus the five failure modes the reference project hit and how the code prevents them.

Other runtimes are supported by the same contract: `cli` (Claude Code / Codex / Aider), `openai` (any OpenAI-compatible endpoint), `mock` (tests and demos).

### Drive it from inside DSH: `packages/dsh-tower`

A companion bundle registers five tools on the harness tool registry, so a DSH session can dispatch,
read the inbox, verify, accept and rework without leaving the conversation:

```sh
dsh plugin --profile <name> add ./packages/dsh-tower
```

`tower_dispatch` · `tower_inbox` · `tower_accept` · `tower_rework` · `tower_status`
→ [packages/dsh-tower/README.md](packages/dsh-tower/README.md)

---

## 6. Install

```bash
git clone https://github.com/evolution301/agent-control-tower.git
cd agent-control-tower
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

Python 3.10+. One runtime dependency (`PyYAML`).

## 7. 60-second demo (no model, no network, no API key)

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

Uses the `mock` adapter, which writes a placeholder artifact plus a real report — so the full
dispatch → report → verify → accept loop is deterministic and CI-friendly.

## 8. Real usage

```bash
tower init my-project && cd my-project
```

This creates `tower.toml`, `roles/{backend,frontend,qa}/AGENT.md`, and `.gitignore`.

Wire a role to a real runtime (DSH shown; see [adapters](docs/adapters.md) for the rest):

```yaml
# tower.toml
roles:
  frontend:
    description: Layout, components, styling, browser verification at 1440/390.
    adapter: dsh
    write_scopes: ["web/"]
    max_rounds: 3

adapters:
  dsh:
    base_url: "http://127.0.0.1:65163"
    sessions:
      frontend: "session-acc99990-..."
```

Then the loop is five commands:

```bash
tower dispatch frontend "Rework the home layout: hero, proof band, evidence grid, FAQ, CTA.
Deliver before/after screenshots at 1440 and 390 plus a section-by-section note."

tower inbox                 # what came back
tower accept T-0001         # verifies files + checks + verify_commands, then accepts
tower rework T-0001 --note "Checks list no results; re-run the test suite"
```

---

## 9. Adapters

An adapter is the only component that knows how to make an agent act. Same contract for all four:

| Adapter | Drives | Use when |
|---|---|---|
| `dsh` | a live DeepSeek Harness session (`session/prompt`, queued) | **primary**: long-lived roles with browser + shell tools |
| `cli` | any agent binary that takes a prompt (`claude -p`, `codex exec`, Aider, your script) | you already run agent CLIs |
| `openai` | any OpenAI-compatible `/chat/completions` with a minimal `run_shell` loop | hosted models, vLLM, Ollama |
| `mock` | nothing (writes a placeholder artifact) | tests, demos, CI |

Adding one is ~40 lines: implement `run(AdapterRequest) -> AdapterResult`, register the name.
See [docs/adapters.md](docs/adapters.md).

## 10. The report contract (and why the verifier exists)

```markdown
## Summary        → 2–5 sentences
## Files          → - <path> — <what changed>          (must exist on disk)
## Checks         → - <command run> → <result seen>    (a bare command fails)
## Open issues    → anything unfinished, or "none"
```

`tower accept` mechanically rejects:

- a missing/empty required section (`accept_requires` in config);
- a check line with no result (no `→`);
- a listed file that does not exist in the workspace;
- any failing entry in `verify_commands`:

  ```yaml
  verify_commands:
    - "pytest -q"
    - "npm run build"
  ```

That is the difference between *"the agent said it works"* and *"the tower checked"*.

## 11. Commands

| Command | Purpose |
|---|---|
| `tower init <dir>` | scaffold config + role folders |
| `tower dispatch <role> "<brief>"` | create and run one task |
| `tower batch role1 role2 --brief "…"` | fan one brief out to several roles |
| `tower inbox [--all] [--read]` | reports filed by roles |
| `tower accept <task>` | verify, then accept (auto-rejects to rework) |
| `tower rework <task> --note "…"` | send back with reasons and re-dispatch |
| `tower status [--json]` | task board |
| `tower show <task>` / `tower log` | inspect one task / tail the ledger |
| `tower demo` | offline end-to-end demo |
| `tower doctor` | validate config, adapter options, keys, sessions |

## 12. What's on disk

```
my-project/
├── tower.toml              # roles, adapters, verify commands
├── roles/
│   ├── backend/            # this role's only write area
│   │   ├── AGENT.md
│   │   └── T-0001-REPORT.md
│   ├── frontend/
│   └── qa/
└── .tower/
    ├── tasks/T-0001.json   # materialised task view
    ├── ledger.jsonl        # append-only event log (source of truth)
    └── inbox.jsonl         # reports + unread flag
```

Commit `.tower/` if you want the audit trail in git; gitignore it if you only want the deliverables.
Either way nothing lives in a database you cannot read.

## 13. Design notes worth stealing

- **Roles cannot spawn roles.** Fan-out stays a lead decision, so the task tree stays inspectable.
- **`serial_requests` + `request_gap_s`.** For fragile hosts or rate-limited APIs: one dispatch at a time, with a gap. The reference project ran on a 1 GB VPS that fell over under parallel requests.
- **Verification is a CLI verb, not prompt advice.** Agents optimise for "report success"; the tower removes the incentive.
- **Reports are files, not messages.** They survive context compaction, review in PRs, and are re-parseable months later.

## 14. Roadmap

- [ ] `tower watch` — poll adapters and auto-file reports for long-running CLI agents
- [ ] notification sinks (Slack / Telegram / webhook) on top of the file inbox
- [ ] per-role `verify_commands` overrides
- [ ] token / cost accounting per role
- [ ] `tower report --html` — one-page audit report per batch

## 15. Docs

- [DSH quickstart](docs/dsh-quickstart.md) — the primary setup, end to end
- [Architecture](docs/architecture.md) — components, state machine, why files instead of a queue
- [Adapters](docs/adapters.md) — mock / cli / dsh / openai, and how to add your own
- [Contributing](CONTRIBUTING.md) — ground rules and PR checklist

## License

MIT — see [LICENSE](LICENSE).
