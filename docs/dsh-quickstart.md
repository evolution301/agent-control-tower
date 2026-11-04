# DSH quickstart (primary setup)

agent-control-tower is **DSH-first**: the pattern it packages was extracted from a real
[DeepSeek Harness](https://github.com/) deployment where one lead steered several long-lived role
sessions through a long build (website research → copy → IA → UI → deploy → SEO), verifying every
delivery before accepting it.

This page walks through that exact setup. If you only have a model API, use the
[`openai` adapter](adapters.md#openai) instead — nothing else changes.

---

## 1. Mental model: roles are sessions

In DSH a *session* is a durable agent with its own context, tools and working directory. That maps
one-to-one onto a tower *role*:

| Tower concept | DSH concept |
|---|---|
| role | session |
| role workspace | the directory that session works in |
| dispatch | `session/prompt` (queued, delivered at a step boundary) |
| report | a file the session writes, read back by the adapter |
| accept | the lead verifies on its own, then marks the task accepted |

Because sessions keep context, the lead does not have to restate the project every round. Because the
report is a file, context compaction no longer loses the deliverable.

---

## 2. Create the role sessions

Create one DSH session per role. Give each a short, explicit charter (this becomes
`roles/<role>/AGENT.md`):

```markdown
# frontend

You own layout, components, styling and browser verification.

- Work only inside `roles/frontend/`.
- Deliver CSS/HTML plus before/after screenshots at 1440 and 390.
- Every claim in your report must be backed by a measurement you actually took.
```

Record each session id — `session/list` returns them:

```bash
curl -s -X POST http://127.0.0.1:65163/rpc/session/list \
  -H 'Content-Type: application/json' \
  -d '{"_request":{}}'
```

You only need the `sessionId` values.

---

## 3. Configure the tower

```yaml
# tower.toml
name: my-product
workspace: .

roles:
  backend:
    description: Data, APIs, infrastructure.
    adapter: dsh
    write_scopes: ["server/"]
    max_rounds: 3
    timeout_s: 3600
  frontend:
    description: Layout, components, styling, browser verification.
    adapter: dsh
    write_scopes: ["web/"]
    max_rounds: 3
  qa:
    description: Independent verification; reproduces claims, files defects.
    adapter: dsh
    write_scopes: []
    max_rounds: 2

adapters:
  dsh:
    base_url: "http://127.0.0.1:65163"
    sessions:
      backend: "session-1492244d-..."
      frontend: "session-acc99990-..."
      qa: "session-4dbe65d4-..."
    wait_s: 1800
    poll_s: 5

verify_commands:
  - "pytest -q"
```

`tower doctor` checks that every DSH role has a session id before you start.

---

## 4. Run the loop

```bash
tower dispatch frontend "Rework the home page layout: hero, proof band, evidence grid, FAQ, CTA.
Deliver before/after screenshots at 1440 and 390 plus a section-by-section note."
```

What happens:

1. the tower renders the prompt contract (role, write scope, report sections, "never claim an
   unverified check"), writes it to `.tower/tasks/T-0001/prompt.md`;
2. the DSH adapter queues it into the session (`session/prompt`, `mode: queue`) so the session picks
   it up at its next step boundary instead of mid-turn;
3. the adapter polls `session/list` until the session is idle again;
4. the tower reads `roles/frontend/T-0001-REPORT.md`, parses the four sections, files it in the inbox
   and flips the task to `reported`.

```bash
tower inbox
tower accept T-0001
```

`accept` re-checks the report (files exist, every check has a result) and runs `verify_commands`.
If anything fails the task goes back to `rework` with the reasons attached — your note gets appended
to the brief and the same session gets the follow-up.

---

## 5. What the reference project learned

These are the hard-won bits baked into the code, not folklore:

- **Queue, do not interrupt.** `mode: queue` lands the task at a step boundary. `session/cancel`
  mid-turn froze one role for hours in the reference run.
- **The report is a file.** DSH does not expose a "final answer" RPC in this version; reading
  `roles/<role>/<task>-REPORT.md` is deterministic and survives compaction.
- **One writer per file.** Two roles editing the same CSS file produced a silent tug-of-war that took
  three rounds to notice. `write_scopes` in the config is not decoration.
- **The lead verifies independently.** In the reference run, a role reported "H1 is fine" while the
  live page had a different font size; only a second measurement caught it. That is why `accept`
  exists.
- **Serial requests for fragile hosts.** A 1 GB VPS fell over when two roles fetched pages at once.
  `serial_requests: true` and `request_gap_s: 2` made it survivable.

---

## 6. Mixing adapters

Nothing requires all roles to be DSH. A common split:

```yaml
roles:
  backend:
    adapter: dsh        # long-running, tool-heavy
  docs:
    adapter: openai     # cheap, bounded: "update the changelog and report"
  qa:
    adapter: cli        # a script you trust
```

The tower only requires that each role returns a report with the four sections.
