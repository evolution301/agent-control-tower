# Scene brief — the isometric control room, adapted to this tower

The brief came from a friend's one-off page (a fake simulator). Ours must be the
same *kind* of thing — an isometric glass tower with one robot per agent — but it
renders **this tower's real state** and every button acts on real tasks. Where the
original said "simulation engine", we say "read the files the tower already writes".

## What maps onto what

| Their concept | Ours |
|---|---|
| agent | role in `tower.toml` (one bot session each) |
| department / floor | role, bottom-up: ops, research, content, creative, code, HQ |
| robot status ring | task state: green working · amber needs a decision · red stuck · grey idle |
| pending approval | task with `state = blocked` (question) — max 4 shown |
| stuck agent | task with `status = failed` — max 2 shown, with reason + fix |
| repair (ops bot) | `tower triage` retry; the ops robot walks over, sparks, walks back |
| approve / reply / skip | `accept` / `rework --note` / dismiss the question |
| token cost | **estimated** from adapter runs (rate in `scene.RATE_USD`), labelled "est." |
| scheduled task | task with `due_at` (countdown, run now = `tower supervise`) |
| today's completed | `status = accepted` tasks, with duration and runs |

## Rules we keep from `docs/board-model.md`

1. No number without a formula; the estimate says it is an estimate.
2. Real data only: the scene calls the same `/api/*` the board does.
3. Actions are narrow, explicit and logged (`auto_accepted` / `auto_rework` style
   ledger events). The read-only rule gains one documented exception: a *control
   room* may act, but every action lands in the ledger.
4. No "demo" / "mock" words in the UI; English interface.

## Deliberately deferred (v2)

- agent-to-agent chatting animation, coffee/watering micro-actions
- the full conversation panel with an assistant model; v1 answers from computed data
- 60fps budget tuning on low-end GPUs
