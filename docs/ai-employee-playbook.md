# What we borrowed from Lauren Tan's AI-employee playbook — and what we deliberately did not

Lauren Tan ([@poteto](https://x.com/poteto)) runs Grok Bot engineering at xAI. Her
[seven-point guide](https://x.com/poteto/status/2116576229319233596) on building a bot team is about
**how to staff the team**: create a bot that designs bots, keep the team small, make every bot write
down its own skills, hand out real tools and separate accounts, delete the copy-paste steps, onboard a
bot like an intern, and make it restate the brief before it starts.

Her repo [pstack](https://github.com/cursor/plugins/tree/main/pstack) (MIT © 2026 Lauren Tan) is the
same philosophy as executable rules: 24 principles you steer an agent by name, a `/poteto-mode` that
reads them at the start of every multi-step task, and one rule above all — *prove it works*.

The two halves fit around what this repo already is. Her post decides **who is on the team**; pstack
decides **when one worker may be trusted**; agent-control-tower is the only layer in that picture
where "done" is a state machine a report has to walk through. So the borrow is about the two halves we
were missing: staffing and memory.

## Her seven points, mapped

| # | Her rule | What we already have | What is missing |
|---|---|---|---|
| 1 | **Let AI design AI** — add "dr eggbot", a bot whose job is to interview you and generate other bots | `tower role add` writes `tower.toml` + `roles/<name>/AGENT.md` | the interview. Today you must know the schema before you can ask for a teammate |
| 2 | **Few, well-trained bots** beat a large team of half-baked ones | roles are explicit, `tower doctor` validates the obvious | no size or overlap check: nothing warns you when two roles own the same scope or a ninth role appears |
| 3 | **Save experience as Skills** — "tell your bots to create skills for itself as you give it instructions" | every task already records brief, report sections, checks and verdicts in the ledger | nothing writes those back out as a reusable skill, and nothing feeds an old skill into a new dispatch |
| 4 | **Real tools and accounts** — connectors, vault, and a bot with its own email/accounts for separation | each adapter run is one role's process, with the role's own prompt | no per-role credential scope: every role inherits the same environment |
| 5 | **Kill the copy-paste** — every time you move information between tools by hand, ask the bot to do it | `tower batch` fans one brief out to several roles | no chain: research output cannot become the writer's input without a person pasting it |
| 6 | **Onboard like an intern** — introduce your work, your pain, your material, then ask where to start | `dispatch` requires a brief, and the report contract is enforced | no onboarding shape, so briefs are as good as the moment you wrote them |
| 7 | **Voice brain-dump, then "restate this in your own words"** so a misread is caught before work starts | `verify` checks the *result* against the brief (evidence before done) | nothing checks *understanding*: an agent that misread the brief still runs, and we pay for it twice |

The comment thread added two corrections worth keeping: minimal permissions plus a proof that the work
was actually completed ([@CobaltHere](https://x.com/CobaltHere)), and bots that should notice a
repeated manual step and offer to absorb it (the "SOUL.md" thread). We already sit on the first — a
task can only become `accepted` through `verify_report()`. The second is a product feature, not a
missing gate.

## What pstack gives us by name

Five of the 24 principles are already load-bearing here, or become load-bearing with v0.8:

| Principle | Where it lives now |
|---|---|
| **Prove it works** | `verify_report()`, `tower accept` refusing on an unproved claim |
| **Sequence work into verifiable units** | one dispatch, one report, one verdict — a task cannot be half-accepted |
| **Encode lessons in structure** | this doc, `CONTRIBUTING.md`, and the release checks |
| **Guard the context window** | not yet: skill injection (point 3) has to stay index-sized, not dump every skill |
| **Never block on the human** | `tower attention` surfaces only person-states; everything else keeps moving |

## v0.8, in build order

Each item lands as one small commit and one offline test, because the whole point of the repo is that
a claim without a check is not done.

1. **`tower dispatch --restate`** (+ `tower restate ok <task>`) — point 7. The adapter's first answer is
   only `## Restatement`; nothing else runs until a person (or the lead) accepts it. Cheapest gate in
   the list, and it reuses the report parser we already have.
2. **`tower skill save <task-id>`** — point 3. From an accepted task, write
   `roles/<role>/skills/<slug>.md` with three required sections: steps, output format, checks. Dispatch
   then injects *only the index* of that role's skills, one line each.
3. **`tower onboard`** — points 1 and 6. Five questions (what you do, which step costs the most, who
   reviews, where the material lives, what "done" looks like), then it writes the roles and their
   `AGENT.md` files. `--from <notes.md>` for the voice-transcript case.
4. **`tower route <from> <to> …`** — point 5. A declared chain in `tower.toml`; the accepted report body
   becomes the next role's brief, so no human moves text between roles.
5. **Small: point 2 and 4 hardening** — `doctor` warns on more than five roles, on two roles whose
   `write_scopes` overlap, and on a role that resolves a credential from the global environment
   instead of `roles/<name>/.env`.

## What we deliberately do not adopt

| Their concept | Why not |
|---|---|
| Voice mode and transcription | we are a CLI plus a panel; the brain-dump shape is kept as `tower onboard --from <notes.md>` |
| Per-bot email and self-signed-up accounts | there is no account system here, and creating identities for agents is a policy decision, not a feature |
| A bot that watches you and proposes automations (SOUL.md proactivity) | our entry point is a person; `tower attention` is the part of proactivity that pays for itself |
| Model arenas and multi-model voting (`/arena`, `/architect`) | adapters already let each role run a different model; voting is a separate experiment |
| A 24-principle vocabulary for our own docs | one page of rules we actually enforce beats 24 we only cite |

## Attribution

- Post: <https://x.com/poteto/status/2116576229319233596> (Lauren Tan, @poteto).
- Repo: <https://github.com/cursor/plugins/tree/main/pstack> (MIT, © 2026 Lauren Tan) — principles,
  `poteto-mode`, and the guide under `docs/guide/`.
- Nothing was copied: the five principles are implemented as checks already in `verify.py`, and the
  mapping above is written down so the borrow is explicit, the same way
  [`superagent-ideas.md`](superagent-ideas.md) records the other one.
