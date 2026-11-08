# Work discipline — distilled from pstack

Source: [pstack](https://github.com/cursor/plugins/tree/main/pstack) — Lauren
Tan's opinionated agent skill stack (MIT). Claude/Codex port:
[michael-denyer/pstack-claude](https://github.com/michael-denyer/pstack-claude).
These are **distillations rewritten for tower bots** (write scopes, the report
contract, the Attention queue), not copies; the mapping below says what came
from where and what the tower already had.

## Mapping: pstack concept → tower mechanism

| pstack | Tower mechanism |
|---|---|
| `poteto-mode` router | the lead (总控) + `tower dispatch` routing to roles |
| `in-your-own-words` | the restatement gate (`--restate` + `tower restate ok`) |
| `show-me-your-work` (decision trail) | report Summary/Open issues + `.tower/ledger.jsonl` + `events.jsonl` |
| `prove-it-works` | the verifier (`verify_report`) + `Checks` must be re-runnable commands |
| `babysit` / PR babysitting | `tower supervise` + due dates + nudges |
| `swarm` (fan-out) | `tower batch` + parallel roles |
| `never block on the human` | `## Attention` for genuine calls; defaults elsewhere |
| `tdd` / `thermo-nuclear review` | role skills (accepted work distilled via `tower skill save`) |
| Non-negotiables + Principles | `src/control_tower/principles.py` cards, injected into every dispatch prompt |

## What a dispatched bot actually receives

`render_prompt()` appends a **Work discipline** section: eight non-negotiable
rules plus the compact principle index (one line each — the context window is a
principle too). The full card for any principle is `tower principles <name>`.
Turn the injection off with `principles: false` in `tower.toml`.

## Attribution

pstack's ideas are Lauren Tan's; the Claude/Codex port is michael-denyer's.
Our cards restate the ideas in the tower's vocabulary and shrink them; any
misreading is ours. pstack is MIT-licensed; this repo is MIT too — keep this
file and the NOTICE spirit when redistributing.
