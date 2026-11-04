# What we borrowed from dsh-superagent — and what we deliberately did not

DeepSeek published the research prototype [`deepseek-ai/dsh-superagent-prototype`](https://github.com/deepseek-ai/dsh-superagent-prototype)
alongside the paper *Exploring Super-agent Design: Towards a More Autonomous Agent System*. It solves
the neighbouring problem to ours:

|  | dsh-superagent | agent-control-tower |
|---|---|---|
| Who starts the work | an **event** (a GitHub notification, a Feishu message) | a **person** (you, or the lead agent you talk to) |
| Who decides whether work is allowed | `Eval` + `Policy` + `Mandate` | the lead, plus `write_scopes` |
| Who executes | an `Executor Session`, one directory per Ticket | a role agent, one directory per role |
| What a person sees | a panel that lists only what needs a person | `tower inbox` / `tower attention` |

Both are "one lead, many workers, evidence before done". Theirs is autonomous and event-driven; ours
is supervised and CLI-driven. Reading it changed five things in this repo (v0.2).

## 1. Structured report state, not prose

**Their model:** every Report carries one of a closed set — `progress` · `waiting` · `ready` ·
`blocked` · `failed` · `dropped` — and the Ticket's stage is *derived* from it. Free text never
decides whether a person is needed.

**Ours (v0.2):** `## Status` is now a required section of every report, validated against the same
vocabulary. `## Attention` carries **one** concrete question when the status is `blocked` or
`failed`. The verifier rejects unknown states and blocks without a question:

```markdown
## Status
blocked

## Attention
Which environment should I use, staging or production?
```

```text
$ tower accept T-0007
REJECTED -> rework
  - status 'blocked' requires one concrete question under '## Attention'
```

## 2. An attention queue instead of an inbox firehose

**Their model:** "A panel that shows only what needs a person. It lists first what waits for a
person. A healthy state takes no space."

**Ours:** `tower attention` lists only tasks in person-states (`ready` / `blocked` / `failed`).
`tower inbox` stays as the full log — it is the audit trail, not the queue.

```text
$ tower attention
T-0007  backend    blocked  Migrate the orders table
      → Which environment should I use, staging or production?
```

This is the single biggest attention saving in the whole comparison: before it, the lead read every
report to decide whether a human was needed.

## 3. Idempotence at the entry point

**Their model:** a Trigger carries the Source's identity, and "accepting a Trigger twice creates one
Ticket".

**Ours:** `tower dispatch --key <idempotency-key>`. Re-running the same request (a retry, a cron, a
webhook that fires twice) returns the existing task instead of creating a duplicate:

```text
$ tower dispatch backend "Produce the artifact." --key req-1
task T-0001 role=backend status=reported

$ tower dispatch backend "Produce the artifact." --key req-1
idempotent hit: task T-0001 already exists for key 'req-1'
```

## 4. Ticket vs Assignment: the work item outlives its executor

**Their model:** a `Ticket` is the unit of authorized work; an `Assignment` binds it to *one*
Executor instance, and a Ticket has several over time. "A Ticket defines its own state instead of
following the Executor's."

**Ours:** every adapter run is recorded on the task (`task.assignments`: adapter, exit code,
timestamp), and `rework` keeps the same task id across rounds. So a task handed from `dsh` to `cli`,
or retried after a crash, still reads as one piece of work with a history.

## 5. Mandate-lite: record why the work may be done

**Their model:** a `Mandate` is immutable and records the Policy version, the scope, the **strength**
(`explicit` — a person asked; `inferred` — a written rule allowed it) and who asked.

**Ours:** `tower dispatch` stores a mandate on the task and accepts `--strength` / `--requester`:

```yaml
mandate:
  requester: alice
  strength: explicit
  policy: tower.toml
```

We did **not** implement Policy versioning or a gate/assessment split. The reason is scope: our work
always starts with a person, so "who may originate work" is answered by the ask itself. Recording the
mandate keeps the audit trail honest and leaves the door open.

## What we deliberately did not adopt

| Their concept | Why not (yet) |
|---|---|
| **Sources / Triggers / Threads / Subscriptions** | That is an event-driven intake layer. Our entry point is a person; adding Sources is a separate product (a webserver, webhooks, identity sync). |
| **Eval: gate + assessment on every inbound event** | Meaningful when events arrive unasked. Here the lead already decides what to dispatch; a second gate would add latency and concepts, not safety. |
| **Policy versions, Mandate revalidation, subscription fan-out** | Real machinery for a multi-tenant deployment where strangers can trigger work. We are one operator on a laptop. |
| **Executor slots, handoff, 30-minute background limit, recovery notes** | Those belong to a long-lived daemon that owns sessions. We deliberately do not own the agent runtime — adapters do. |
| **Channels (GitHub / Feishu replies)** | Out of scope: we report to the person who asked, through the CLI or the DSH session they are already in. |

## The philosophy we did take to heart

Their first principle is *"take as little human attention as possible, and give as much value as
possible"*, judged per message. Every design decision above is an application of it:

- state is a closed vocabulary, because the lead should not have to read paragraphs;
- only person-states surface, because a healthy task needs no space;
- one concrete question, because a question buried in a report never gets answered;
- idempotent entry points, because duplicate work costs attention twice.

That sentence is now the test we apply to new features: **does this save more attention than it
spends?**

## Attribution

- Prototype: <https://github.com/deepseek-ai/dsh-superagent-prototype> (MIT, research code — "not an
  official DeepSeek product").
- Paper: *Exploring Super-agent Design: Towards a More Autonomous Agent System*.
- Nothing was copied: the concepts above are re-implemented in ~120 lines of Python, and the mapping
  is documented here so the borrow is explicit.
