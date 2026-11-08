# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/): the Python package
(`agent-control-tower`) and the DSH bundle (`dsh-tower`) share one version.

## [Unreleased]

### Fixed — the four ways a dispatch used to fail silently (found on a live tower)

All four cost real debugging time in production: the tower reported success or
nothing at all while the work had never started.

- **`tower doctor` never touched the network.** It validated the config's shape
  only, so it printed `problems: none` while `base_url` pointed at a closed port
  and `secret` held a stale value. `doctor` now runs a `session/list` preflight
  per distinct dsh endpoint and reports `unreachable or unauthorized`, plus any
  pinned session id that is not on that host. (`cli._dsh_preflight`)
- **A session with no model selected accepted the prompt and never started a
  turn.** The adapter then waited the full `wait_s` (900s) and returned a bare
  timeout. `DshAdapter` now reads `session/projections` after queueing and fails
  in `turn_start_s` (default 45s) with "this session has no model" when the host
  explicitly projects an empty `modelSelection`. A host that does not project it
  at all is left alone (`model_known` guards the check). The timeout message now
  carries `turns/llmMs/model` so the next reader sees the state. (`adapters/dsh.py`)
- **`session_pressure` always returned `None`.** It called
  `session/projections` with `{"sessionId": …}` but the method requires
  `{"request": {"sessionId": …}}` (the neighbouring `session/list` really does
  want `_request`), so every call came back as an argument error. Context
  pressure in `tower ctx` works again, and it now accepts the host's dict shape
  (`{pressureTokens, contextWindow}`) as well as a bare number.
- **`verify_commands` ran with `cwd = workspace`.** A relative
  `node scripts/lint.mjs` therefore raised `MODULE_NOT_FOUND` and *every* accept
  failed. New `verify_cwd` config key (relative values resolve against the
  workspace), and a 127 / "not found" exit is reported as *"command is not
  runnable from <dir> — likely a relative path"* instead of a generic failure.
  Verify subprocesses now decode as UTF-8 with `errors="replace"`; on a GBK
  Windows console a check that printed non-ASCII text crashed the reader thread.
- **Reported files were resolved against the workspace only.** A deliverable
  written into the project root was reported missing, while a wrong-root write
  *into* the workspace passed. Files now resolve against `workspace`,
  `project_root` (new key) and `verify_cwd`, and the failure message lists the
  roots that were checked.

### Added

- `project_root` and `verify_cwd` config keys: a tower often coordinates a
  project that lives in a sibling checkout.
- `DshAdapter.session_state()` — turns/llmMs/model for one session.
- `tests/test_fixes_2026_11_09.py` — one regression test per failure above.

## [0.10.9] - 2026-11-09

### Fixed — `tower_*` 中文乱码（plugin channel forced cp936 → UTF-8 decode）

John's field report: every `tower_status` / `tower_inbox` / `tower_roles` call
from the plugin returned Chinese briefs as U+FFFD mush (`G:\新网站项目` →
`G:\??????`), because the spawned `tower` CLI encoded stdout with the Windows
code page (cp936) while the plugin decodes UTF-8.

- **CLI entry guard** (`_force_utf8_stdio`): piped stdout/stderr are
  reconfigured to UTF-8 on every run — `tower.exe`, `python -m`, the plugin
  channel, scripts; interactive consoles (isatty) keep their native wrapper, so
  GUI display is untouched. Even `PYTHONIOENCODING=cp936` cannot change piped
  bytes any more.
- **Plugin spawn env**: every CLI/panel child process now runs with
  `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8` (belt over the braces).
- Regression tests: piped CLI output under a forced cp936 environment decodes as
  UTF-8 with the Chinese brief intact and no U+FFFD; tty streams stay native.

## [0.10.8] - 2026-11-09

### Added — charter & standing rules ride every dispatch (John's standing-rules request)

- **`## Role charter`** — `roles/<role>/AGENT.md` (written by `tower role add`)
  was a dead file: never read after session creation. It is now injected into
  every dispatch prompt, truncated at 1500 chars.
- **`## Standing rules（全塔常设规则）`** — the first existing file of
  `docs/bug-report-protocol.md` → `docs/RULES.md` → `RULES.md` is injected,
  truncated at 2000 chars. 改一处、每次派单全体生效 — no more re-notifying
  every bot session one by one.
- Both sections are plain file reads with truncation markers, so oversized
  charters can never blow the context window. Verified live on prl-tower
  (auditor charter) and the evergocasa tower (bug-report-protocol).

### Maintainer receipts

Per the new bug-report protocol, closure receipts for the four reported bugs
were left in the prl-tower inbox (`bugfix_receipt` ×4): ctx units → 0.10.6,
tower_compact inject → 0.10.7, relay session-id → 0.10.7, triage TypeError →
0.8.3.

## [0.10.7] - 2026-11-09

### Fixed — two field bugs from John's live use

- **`tower_compact` failed with `cannot get property "agents" without inject`.**
  The bundle declared `inject: ['tools']` only; the compact tool reads
  `ctx.agents` / `ctx.compaction`. The plugin now declares
  `inject: ['tools', 'agents', 'compaction']` (same contract as the native
  dsh-command-compact), so the in-process compaction works.
- **`tower relay` ignored exact session ids.** The id heuristic required exactly
  four hyphens, but real ids are `session-<uuid>` (five). `session-*` ids (and
  bare uuids) now resolve as exact ids; title search is only the fallback.
  Regression tests cover both.


### Fixed — two field bugs from John's live use

- **`tower_compact` failed with `cannot get property "agents" without inject`.**
  Root cause: the bundle declared `inject: ['tools']` only. `agents` is now
  declared (always available once a session exists); **`compaction` stays
  optional by design** — declaring it made the whole bundle pend when
  dsh-compaction-basic is not loaded/available in the profile (18 tools went
  offline; worse than the bug). The compact tool instead detects the missing
  service at execute time and returns precise guidance (enable
  dsh-compaction-basic, or use /compact in the session GUI).
- **`tower relay` ignored exact session ids.** The id heuristic required exactly
  four hyphens; real ids are `session-<uuid>` (five). `session-*` (and bare
  uuids) now resolve as exact ids — title search is only the fallback.
  Regression tests for both.

## [0.10.6] - 2026-11-09

### Fixed — `tower ctx` unit math (John's field report)

- `session/projections` returns `contextPressure` as an **object**
  (`pressureTokens / projectedTokens / contextWindow`); the old code read it as a
  scalar and printed raw values like `pressure=79661%`, flagging every session
  COMPACT NOW. The adapter now computes
  `pct = pressureTokens / contextWindow × 100` — the window size comes from the
  data, so desktop (1,048,576) and headless (262,144) sessions both read
  correctly. The RPC call also wraps the session id in the `request` envelope
  the gateway expects (projections for live sessions now resolve; idle ones
  degrade to "projections unavailable").
- Live re-check on prl-tower: `auditor 14.87% (window 1048576)`, `ui 20.64%`,
  `messenger 4.5% (window 1000000)` — units match John's field numbers.

### Added

- **`messenger` role** wired into prl-tower (Evergo 信使, cross-session relay,
  timeout 3600s, session wired) — visible in `tower ctx` / `tower_roles` / doctor.
- **Each panel defaults to its own workspace project.** Both boards used to
  render the globally-active project, so `:8787` and `:8788` looked identical;
  each now opens on the project it serves (`?project=` still overrides).

## [0.10.5] - 2026-11-09

### Fixed

- **A worker running yesterday's code kept claiming new jobs.** The heartbeat now
  records the worker's version; `ensure_worker` treats a version-mismatched
  heartbeat as stale, retires the old process and spawns a fresh one — so a CLI/
  plugin update takes over the queue without manually killing anything.
- **Job ids collided with the done archive.** The enqueue sequence only scanned
  pending/claimed, so a retried id could overwrite an archived job; done is part
  of the scan now.

## [0.10.4] - 2026-11-09

### Fixed — live-run bugs from the prl-tower adversarial pass (2026-11-09)

- **A detached worker kept a stale role list.** `tower review` queued an
  adversarial pass for the freshly added `auditor` role and the worker answered
  "unknown role 'auditor'" — it had been spawned before the tower.toml edit and
  never re-read it. The worker now re-parses the tower file for every job
  (`config_path` + `reload_config`).
- **The board lock starved other dispatches.** `_settle_dispatch` held
  `.tower/.lock` across the whole adapter turn (minutes), timing out Q-00002 with
  "lock held for over 60s". Reworks are now **staged** under the lock (verdict +
  reasons recorded) and the redispatch runs after it — the lock only covers board
  mutations, per design doc §6.
- **GBK crash in the cli-agent adapter** on non-ASCII bot output
  (`encoding="utf-8", errors="replace"`), from the parallel plugin-maintenance
  session.

### Added — multi-root verification (parallel session)

- `project_root` + `verify_cwd` in tower.toml: report file paths and verify
  commands resolve against the project checkout too, for towers whose
  deliverables live in a sibling project directory (prl-tower's exact shape).

## [0.10.3] - 2026-11-09

### Changed — auto-compaction is a native knob, not a plugin (context-guard cancelled)

John found the native answer to the "global 50% auto-compact" request before the
context-guard client plugin was written: DSH ships
`@deepseek-ai/dsh-compaction-basic` mounted by default, and its
`thresholdRatio` (default **0.8**) is the auto-compact threshold. Set it to
**0.5** in the cordis patch and every session compacts itself at 50% — no
plugin, no extra turn. The READMEs now document the knob next to the plugin's
manual `tower_compact` / `tower ctx` helpers, which stay as the one-shot and
visibility tools.

The previously scheduled context-guard client plugin is cancelled. The triage
fix (0.8.3) and async dispatch + relay (0.9.0–0.9.1) were already shipped with
regression tests; nothing pending from that queue.

## [0.10.2] - 2026-11-09

### Added — programmatic context compaction (John's 50% rule)

- **Source-level answer to the compact question.** The harness JSON-RPC surface
  is exactly seven methods (`initialize`, `session/prompt|query|resume|wait`,
  `session/working-directory/get|set`, `shutdown`) — there is **no compact RPC**,
  confirming the 10×404 probe. The real seam is the in-process compaction
  service: `/compact` is a human command whose handler calls
  `ctx.compaction.compactNow(agent, signal, id)` (implemented in
  `@deepseek-ai/dsh-compaction-basic`), with agents resolvable by session id via
  `ctx.agents.get(sessionId)`.
- **`tower_compact { session }` (DSH plugin tool, option A).** Because the
  plugin runs inside the same host context, it calls `compactNow` directly —
  no extra turn, no GUI, no print token. Accepts a DSH session id or
  `role:<name>` (resolved through tower.toml sessions). Errors are surfaced
  with the same human texts the native `/compact` prints (busy / cancelled /
  changed / summary / commit / persistence), plus a 120s idle timeout.
- **`tower ctx` (option C).** Prints `contextPressure` per session configured in
  tower.toml (`[adapters.dsh.sessions]`), read from `session/projections`,
  flagging **⚠ COMPACT NOW at ≥50%** and naming the one-shot trigger
  (`tower_compact`); degrades to "projections unavailable" without crashing.

### Why not B (GUI/CDP automation)

Electron launches without a remote-debugging port, and patching app.asar is
wiped on every app update. The in-process seam is strictly better: it is the
same code path the native /compact uses.

## [0.10.1] - 2026-11-09

### Added — reviewer governance (P2) & learning drafts (P3), per the upgrade plan

- **The auditor is audited.** Every adversarial finding passes a governance pass:
  no run-proof → dropped; naming a file outside the origin's diff → out-of-scope
  dropped. Two audited-away findings void the reviewer's report — one rerun with
  the failure named, a second void becomes an `review_open_issue` instead of
  trusted noise. Review-noise reports (nothing survived) fall back to the
  mechanical verifier rather than blocking the task.
- **Reputation.** `.tower/reviews/<reviewer>.json` tracks reviewed/proved/dropped/
  out_of_scope and a score; `tower status` prints reviewer standings.
- **`tower reflect <task>` + auto-drafts.** A task accepted after ≥2 rework rounds
  distils its rework notes into a draft skill under `roles/<role>/skills/drafts/`
  (never auto-registered; a person adopts it). The tower drafts it automatically
  on acceptance and `tower reflect <task>` re-runs it by hand; duplicates are
  silent no-ops.
- **`tower status` markers**: `a` for async, `⧗review` while a review is pending,
  `✓pass/✓rework` once settled.

## [0.10.0] - 2026-11-09

### Added — the self-review loop (写完 ≠ 完成，审查通过才算)

Per `docs/upgrade-self-review.html` (P0+P1), distilled from pstack's review machinery:

- **The self-review gate.** The report contract gains a mandatory `## Self-review`
  section: before filing, the bot re-reads ONLY its own additions/changes — side
  effects on importers, Checks re-run with real output, AI slop removed — and
  writes one line on what it fixed, or exactly "no findings — checks re-run".
  `verify_report` fails a report without it (`self_review: true` by default;
  the mock adapter and every docs example carry the section now).
- **The adversarial review loop.** When a verified report is ready to accept, the
  tower queues one independent "break it" pass to a configured reviewer
  (`review_roles: [auditor]`, never the author itself, `review_budget` caps the
  rounds). The reviewer's brief demands findings **proved by a run** (command →
  output, one line each; style is out of scope; "no findings" is valid). Proved
  findings rework the origin task with the proof attached; zero proved findings
  accept it. A pending review blocks other triage; the verdict —
  `[tower] T-00xx review verdict: pass|rework | findings=N proved=M` — lands in
  the tower inbox, the lead session (when configured) and `events.jsonl`.
- **`tower review <task>`** and the plugin's **`tower_review`** tool (17 tools)
  trigger a manual pass.
- Config: `self_review`, `review_roles`, `review_budget`.

## [0.9.2] - 2026-11-08

### Added — work discipline distilled from pstack

After studying [pstack](https://github.com/cursor/plugins/tree/main/pstack)
(Lauren Tan's agent skill stack; Claude port
[michael-denyer/pstack-claude](https://github.com/michael-denyer/pstack-claude)),
the tower now carries that discipline itself:

- **14 principle cards** (`src/control_tower/principles.py`) — rewritten for tower
  bots, not copied: understand-first, smallest-change, fix-root-causes,
  prove-it-works, test-behavior, subtract-before-add, attack-the-premise,
  model-the-domain, boundary-discipline, idempotent-operations,
  never-block-on-human, show-your-work, minimize-reader-load, explain-the-number.
- **Every dispatch prompt** appends a Work-discipline section: eight
  non-negotiables plus the compact index (one line per principle — the context
  window is a principle too). Full card: `tower principles <name>`. Off switch:
  `principles: false` in `tower.toml`.
- **`tower principles [name]`** — list or show a card.
- **`docs/pstack-discipline.md`** — the attribution and the mapping table
  (pstack concept → the tower mechanism that already implements it: restatement
  gate ↔ in-your-own-words, verifier ↔ prove-it-works, supervise ↔ babysit,
  Attention ↔ never-block-on-the-human, ledger ↔ show-me-your-work).

## [0.9.1] - 2026-11-08

### Added — per the async/relay design doc (`plugin-async-relay-design.md`)

- **Worker pool.** `workers: N` (default 4) threads inside one `tower worker` process; the atomic
  claim keeps them from doubling work, `serial_requests` becomes a per-role semaphore inside the pool
  (same-role serial, cross-role parallel), and enqueueing is concurrency-safe (in-process claim lock +
  unique temp file + `os.replace`, with an id-bump retry for cross-process collisions). Verified by a
  10-concurrent-enqueue stress test.
- **`.tower/.lock`** — advisory file lock (msvcrt/fcntl) around board-mutating worker and triage
  sections, per design §6.
- **`.tower/events.jsonl`** — the lifecycle audit trail (`enqueued → settled → notified`, plus relay
  events), per design §2, via the new `Board.event`.
- **Task fields** `mode` / `waiter` / `settled_at` / `notified` on `T-xxxx.json`, backward compatible
  (`from_dict` fills defaults for old files).
- **Settlement notices in the doc's format** — `[tower] T-00xx settled: accepted | role=… |
  summary=…` — always in the inbox, and with `notify.kind: lead_session` + `notify.session` also
  pushed to the lead's DSH session through the settled task's own adapter.
- **`async_roles: [seo, ops]`** list override alongside `roles.<name>.dispatch_mode`.
- **Relay deltas**: `--to role:<name>` (the role's own session), `--wait <file>` (the file is the
  receipt — its content, once it appears), `--timeout 45m` (same duration grammar as `--due`).
- **`--notify session:<id>|inbox`** on dispatch, stamped into the task's `waiter`.
- **Visibility**: snapshot counts expose `async_in_flight`.

## [0.9.0] - 2026-11-08

### Added

- **Async dispatch.** `tower dispatch --async` (and `dispatch_mode: async` in `tower.toml`, with a
  per-role `roles.<name>.dispatch_mode` override) writes the task into `.tower/queue/pending/` and
  returns the id in under a second (measured: ~90 ms). A `tower worker` process — spawned detached
  and automatically on first use, heartbeat-guarded — claims jobs atomically (`os.replace`), runs the
  adapter exactly like the sync path (report → peer-message relay → auto-triage with the shared
  `redispatch_with_reasons`), archives the settled job, and lands an `async_settled` notice in
  `tower inbox`. Claimed jobs older than 15 minutes are presumed dead and requeued. Sync mode is
  byte-for-byte the old behaviour.
- **Generic relay.** `tower relay --to <target> -m "..."` delivers a message to any DSH session — a
  session id, a unique session-title substring (ambiguous titles are refused, never guessed), or the
  reserved alias `tower` meaning this tower's own inbox. With `--await-receipt` a worker watches for
  a reply carrying the relay id — the peer answers with `tower relay --to tower --reply <id> -m
  "..."` — and lands a `relay_receipt` (or `relay_timeout`) notice. Every relay is filed under
  `.tower/relay/` and in the ledger (`relay_sent`).
- **Plugin surface.** `tower_dispatch` gains `async: true` (queued call returns `queued/job/mode`),
  and a new `tower_relay` tool exposes the relay (16 tools now). `tower worker [--once]` runs the
  queue by hand for scripts and CI.
- **`Board.notify`** — the generic inbox append used by settlements, receipts and timeouts.

## [0.8.3] - 2026-11-08

### Fixed

- **`tower dispatch` crashed (exit 1) during auto-triage** when a verified-failed or adapter-failed
  task sat on the board: `triage.py` calls `dispatch_fn(board, task, reasons)` (three args) while
  `cli._triage_dispatch(cfg, board, task, reasons)` takes four — `TypeError: missing 1 required
  positional argument: 'reasons'`. The contract is now written down (`triage.py` docstrings: three
  positional args, no cfg — the callback closes over its own config), the closure binding in
  `cli._auto_triage`/`cmd_triage` is the formal shape, and three regression tests pin it: the 3-arg
  contract works, a 4-arg callable reproduces the original TypeError, and a dispatch with a failed
  task on the board completes (exit 0) and the queue pass retries that task.
- **Adapter-failed tasks were owned by the wrong queue.** A failed task with retries left was caught
  by a `needs_person` catch-all, so the tower's own retry pass never saw it — the human queue got a
  card for work the tower could (and should) retry itself. Failed tasks with budget now live in
  `needs_lead`; only an escalation (`state = blocked/failed`, set when the budget is spent) reaches a
  person. The scene's stuck cards follow the same rule.
- **A failed restatement gate is explicitly a person decision.** `dispatch_restate` now sets
  `state = blocked` with a concrete question (rewrite the brief), instead of relying on the removed
  catch-all.

## [0.8.2] - 2026-11-08

### Added

- **Repair visits.** Pressing 维修/Repair sends the Operations robot on a real walk: to the lift, up
  (or down) to the stuck floor, to the broken bot's side — sparks fly for three seconds — then home
  again. The retry itself already ran server-side; the visit is the visible half.
- **Reactions.** Approve makes the bot hop with a heart, reply pops a "!" bubble, skip earns a shrug.
  Presentation only; the ledger carries the real event.
- **Cost panel depth.** An end-of-day projection (`projected_usd` = spend ÷ fraction-of-day) and the
  per-floor split (top three by spend).
- **Completed-today rows expand** to show the original instruction.
- **Activity log scope**: 全部/本层 (all / this floor only).
- **Agent detail card gains a 💬 对话 button** that opens the chat drawer addressed to that agent.
- **`account` in `tower.toml`** is shown on the top bar (empty → "local operator"), so the brief's
  X.com label is a config value, not a hardcoded string.

## [0.8.1] - 2026-11-08

### Added

- **The control room is bilingual.** 中文/EN everywhere — panels, buttons, floor pills, canvas floor
  labels, computed answers — switched by the `中/EN` button, `?lang=`, remembered in localStorage,
  default zh.
- **The conversation drawer is real.** Free text goes to `/api/ask`, which creates a genuine task for
  the least-busy role and dispatches it (`scene_ask` in the ledger); the drawer polls `/api/task` and
  shows the agent's report when it lands. Quick tags still answer instantly from computed data. The
  "model" on the other end is whatever adapter the role runs — no canned replies.
- **Idle micro-actions.** Resting robots now drink coffee (steam), water plants, chat face-to-face in
  pairs (speech bubble), or flop onto a bean bag; they re-seat around their floor with eased walking.
- **Floor focus deep links** (`?floor=N`), verified upper-floor lift-and-fade in the screenshot.
- **A live FPS counter** in the top bar and JS errors surfaced instead of failing silently.

### Fixed

- **The scene page died on load** after the first i18n attempt: the language wiring referenced `LANG`
  before its declaration (TDZ), so the whole script aborted and the canvas stayed empty. The page was
  rewritten in one piece instead of being patched again, and the boot order is now checked by a stub.

## [0.8.0] - 2026-11-08

### Added

- **The control room (`/scene`).** A single HTML page, Canvas 2D, no build step: an isometric glass
  tower where every role is a robot on its own floor, LED strips carry the floor's alert state
  (working / waiting / stuck / resting), an elevator rides between levels, and the plaza has roads,
  moving cars and trees. Day and night themes, click-to-focus floors, hover preview, wheel zoom, drag
  pan, double-click fit, `1–6`/`0` floor keys, `W`/`S` jump to the next waiting/stuck item and
  `A`/`R`/`X`/`F` act on it.
- **`/api/scene` and `docs/scene-brief.md`.** The payload is assembled from the tower's own task files
  by `scene.py`: floors = roles, robots = roles, pending = `blocked`/`failed` (at most 4 cards, at most
  2 stuck, matching the brief), spend = runs × a stated rate (labelled "estimated"), completed-today
  from accept events, activity from the ledger. Nothing is simulated.
- **The control room can act, and only through four verbs.** `approve` (runs the verifier first and
  refuses if it would fail), `reply` (rework with your note, re-dispatched in the background), `skip`
  (dismiss the question) and `repair` (the tower's retry path). Each action is written to the ledger,
  and `docs/board-model.md` records this as the single documented exception to "the board is read-only".
- **The page's "ask" box answers from the payload** — summary, who is waiting, what is stuck, spend,
  or an agent by name — so a first-time reader gets an answer without knowing the schema.

### Deliberately deferred (documented in `docs/scene-brief.md`)

- idle micro-actions (coffee, watering, chatting), the full conversation panel backed by a model,
  and 60fps budget tuning on low-end GPUs.

## [0.7.10] - 2026-11-08

### Fixed

- **A release that only bumped the version did not restart the panel.** The self-restart fingerprint
  watched the page and model modules but not `__init__.py`, where `__version__` lives — so an update
  that changed nothing else left the board serving the previous build. `__init__.py` is watched now,
  and a test pins it.

## [0.7.9] - 2026-11-08

### Fixed

- **The test suite rewrote the real `~/.tower/binding.json`.** `tower dispatch` binds the tower that
  did the work, so every test that dispatched left the developer's real binding pointing at a pytest
  temp directory — the panel then "reconnected" to a tower called `triage-tower`. `tests/conftest.py`
  now gives every test its own `TOWER_HOME`.
- **`--workspace` after a subcommand was silently ignored.** The subparser's `--workspace` (default
  `None`) overwrote the global value, so `tower panel --workspace <dir>` fell back to cwd/binding. The
  global flag now has its own destination and the two are combined, whichever one was given.

## [0.7.8] - 2026-11-08

### Added

- **The panel owns a stable URL.** `tower panel` reuses the port this workspace used last time
  (`.tower/panel-port`), else 8787, else the next free port, and remembers the result — so a
  bookmarked board keeps working instead of dying when the default port is taken. `--port` still
  forces an exact port (the DSH bundle passes one and reads it back).
- **The panel restarts itself when the installed code changes.** It fingerprints its own sources on
  boot and re-execs when they move, and `/api/health` now carries `version`, `instance` and `pid`; the
  page polls it and reloads when the instance changes. A plugin update can no longer leave a board
  running yesterday's build.
- **`--workspace` works on the `panel` subcommand** (`tower panel --workspace <dir>`), the way a person
  naturally types it; the global flag still works.

### Fixed

- **Two panels could share one port.** `ThreadingHTTPServer` sets `allow_reuse_address`, which on
  Windows lets a second process bind a port that is already serving — requests then hit whichever
  process won the race, including a stale build. The panel server now refuses the second bind with an
  actionable message.

## [0.7.7] - 2026-11-08

### Added

- **`tower triage` — the tower closes the ordinary loop itself.** A reported task is verified; if it
  passes it is accepted (`auto_accepted` in the ledger), if it fails it is sent back **quoting its own
  verification problems** and retried inside the role's `max_rounds`, and a task that runs out of
  rounds is escalated with one concrete question. `tower dispatch`/`batch`/`restate ok` run the same
  pass automatically (`--no-auto` opts out), so a job the verifier already accepted never waits for a
  person. `auto_triage` in `tower.toml` turns it off globally.
- **`docs/board-model.md` — one written model for the board.** Entities (project → bot → task), the
  four mutually exclusive queues with their exact predicates, the formula behind every number on the
  page, the three ways work can enter a tower, and the aim precedence. The page, the CLI and the panel
  now read the same decisions instead of each deciding for itself.

### Fixed

- **"Needs you" counted finished work.** `PERSON_STATES` included `ready`, so every accepted task
  stayed in the human queue: a real tower showed `需要你 32` while only one task actually needed a
  decision. Accepted tasks are now excluded everywhere, and the board shows `需要你 2` for the same
  tower.
- **The board disagreed with itself.** A restating task (which carries an attention question) was
  counted as a person decision in the snapshot but as tower work in the project badge; an
  `in_progress` task was counted as tower queue. The snapshot now reuses `Board.needs_person()` /
  `Board.needs_lead()` verbatim, so badge, KPI and dock cannot drift.
- **The page called itself `my-team`.** The board title is the registered project name (falling back
  to `tower.toml`), so a person sees `evergocasa-new`, not a scaffold placeholder.
- **The board was noisy for a first-time reader.** Five KPIs with written tooltips, only three
  queues, an inline "这是什么？点开看规则" legend, a collapsed 统计 section for the business numbers,
  and an empty state that says how to dispatch the first task.

## [0.7.6] - 2026-11-08

### Added

- **`tower stats` / `tower stats --all`** — a business view over files the tower already writes:
  verified rate (accepted ÷ decided), rework and restate rates, lead time p50/p90, 14-day throughput,
  per-role totals plus skills, overdue/nudges, conversation and huddle counts, adapter runs. No
  database, no telemetry; an empty tower reports `—`, never a fake 0 %.
- **Multi-project registry: `tower projects list|scan|add|remove|use`** — each project keeps its own
  `tower.toml` and `.tower/`; the registry (`~/.tower/projects.json`) only remembers where they are and
  which one is active (`~/.tower/active`). `stats --all` and the panel aggregate them; a broken
  registered project is reported as broken instead of taking the board down.
- **The panel is now a board for many projects** — project pills and an *all projects* view, a
  now/next bar, per-role robot faces that carry the state (idle / working / failed / stopped), a dark
  theme (`?theme=dark`, remembered in `localStorage`), a task drawer, and deep links
  (`?project=`, `?lang=`, `?theme=`). Layout follows the 值守台HQ rules: three tenses apart, collapsed
  by default, red only for "needs you", read-only.
- **`tower_projects` and `tower_use` in the DSH bundle** — list the registered projects and switch the
  active one from inside a session; the bundle's `tower_*` tools read the same pointer, so a switch
  takes effect without restarting DSH.

### Fixed

- **A plugin update could silently re-aim the remote at another tower.** The bundle
  resolved `workspace` from the profile config with no memory of what it had been using, so a lost
  or reset config line dropped the tools into `cwd` (in practice: a demo tower) — tasks appeared to
  vanish. The aim now has an explicit precedence — configured workspace, then
  `~/.tower/binding.json` (the last tower that really did work, written by the bundle on mount, by
  `tower projects use`, and by the CLI when it dispatches), then the active project — and
  `tower doctor` prints the aim so a mismatch is visible before anything is dispatched. Tower data
  was never touched by an update; only the aim was lost, and that is now impossible to lose silently.
- **The panel could show another project's data.** A panel started in project A answered
  `/api/snapshot` with the registry's active project; with the switcher in place the started workspace
  is authoritative and `?project=` selects the rest.

## [0.7.5] - 2026-11-08

### Fixed

- **The Tower settings section never rendered.** The client entry read `ctx.slots` without declaring
  the service, so the host threw `cannot get property "slots" without inject` on every boot and the
  section stayed blank (the host caught the error, so it never failed the boot — it just never
  appeared). The entry now declares `exports.inject = ['slots']`, the same contract the sidebar
  plugin uses, and `package.json` carries the manifest-level `dsh.client.inject` too.
- **`tower_dispatch` and friends were not reachable from the bundle registry.** Five tools were added
  in 0.7.4 (`tower_say`, `tower_huddle`, `tower_supervise`, `tower_skill_save`,
  `tower_restate_ok`) plus `due`/`restate` on `tower_dispatch`; the bundle test now pins all thirteen
  tools so a missing registration cannot ship again.

## [0.7.4] - 2026-11-08

### Added

- **Bot-to-bot conversation: `tower say <from> <to> "…"`** — bots talk to each other in parallel, the
  way the playbook means it: when A tells B something, two sends go out at once and A is done — the
  note is queued straight into B's own session (`session/prompt` in queue mode), and the same text is
  filed under `.tower/conversations/` plus the ledger *before delivery runs*, which is the lead's copy.
  No round trip, no waiting on a reply. B answers in its own time the same way (`tower say B A "…"`, or
  a `## Message to A` block in its next report, which `dispatch` relays automatically). A failed or
  unreachable delivery is recorded on the message — the record carries the bad news, not the sender.
- **Huddles: `tower huddle <roles…> --topic "…"`** — the lead opens a room for at most six roles, two
  bounded rounds (position + questions, then decision + commitments), minutes written to
  `.tower/huddles/H-*.md`. Bounded rounds are the feature: after round 2 every participant has a
  commitment on the record and everybody goes back to work.
- **Deadline supervision: `tower dispatch --due 90m` (or `3d`, or `2026-11-09 18:00`) and
  `tower supervise`** — a task is a job with a due time. When it passes without a report the lead
  pulls the role up in its own session. Three recorded outcomes: a late but real report is recovered
  and verified like any other; a status-only answer leaves the task open, marked overdue, with the
  answer attached; an unreachable role accumulates nudges. `tower status` and the board show
  `due in …` / `overdue …` with a warning flag.
- **Skills: `tower skill save <task>`** — an accepted (verified) task is distilled into
  `roles/<role>/skills/<slug>.md` with steps, output format and checks, plus a one-line index entry.
  Dispatch injects **only the index**, never the bodies — one page of rules we enforce beats a dump
  that bloats the context window. `tower skill list` shows what each role knows.
- **Onboarding: `tower onboard --from notes.md`** — playbook points 1 and 6. The notes' `## <role>`
  sections (free text = description, `scope:` lines = write scopes) grow the team and write a PLAN.md
  with the operator's answers; more than six roles is refused, because a few well-trained bots beat a
  crowd.
- **`tower dispatch --restate` and `tower restate ok <task>`** — playbook point 7: make the role restate
  the brief in its own words before any work runs. The first turn is a restatement only
  (`## Restatement` / `## Assumptions` / `## Done looks like`); the task waits in `restating`, appears
  in `tower attention` and the board's **needs you**, and cannot be accepted because no report exists.
  `tower restate ok` copies the accepted text into the work prompt as `## Confirmed understanding`,
  then dispatches. The gate fails closed: a non-zero adapter exit or a missing section leaves the task
  `failed`, never `restating`. `tower accept` on a restating task is refused with the one command that
  opens the gate.
- **Doctor hardening** — warnings (non-fatal) for more than five roles, for two roles whose
  `write_scopes` overlap, and for a role that carries env vars without its own `roles/<name>/.env`.
- **DSH bundle: five new tools** — `tower_say`, `tower_huddle`, `tower_supervise`, `tower_skill_save`
  and `tower_restate_ok`, plus `due` and `restate` parameters on `tower_dispatch`. Everything the CLI
  can do is now reachable from a DSH session.

### Fixed

- **The DSH adapter hung for the full timeout on a 401.** The error path read the response body of a
  kept-alive socket that never sent one; wrong-secret probes now fail in seconds with `<no error
  body>` instead of blocking for 90.
- **`tower bootstrap` mistook a slow host for a wrong secret.** The probe allowed 3 seconds for
  `session/list`, but a long-lived desktop host answers with hundreds of rows; the probe now allows
  20 and states why.

## [0.7.3] - 2026-11-07

### Fixed

- **A machine ended up with two `tower panel` processes.** Every mount of the bundle checked the
  port, then spawned; two DSH windows (or two profiles) mounted at the same time both saw a closed
  port and both started a panel. The one that lost the bind race never exited, so `python.exe`
  processes accumulated, and the disposer made it worse: `tower` on Windows is a launcher exe in
  front of `python.exe`, so `child.kill()` killed the launcher and left the server running.
  Starting is now exclusive through `.tower/panel-<port>.lock` (a stale lock whose pid is gone is
  replaced, so a crash cannot block the section forever), the child is only kept once the port
  actually answers, a child that never comes up is killed with its whole process tree, and unload
  releases the lock it owns.
- **`VERSION` in the bundle had drifted from `package.json`** (`0.2.0` since the 0.2 line). It now
  follows the shared version, and a test fails if the two ever disagree.

## [0.7.2] - 2026-11-06

### Added

- **The panel speaks Chinese.** `tower panel` now carries a ZH string table covering every
  user-visible label — headings, stat chips, table headers, empty states and the panel-disconnected
  notice — plus bilingual stat labels (`en` / `zh`), so the Tower section in DSH reads
  需要你处理 / 进行中 / 角色 instead of half-translated English. `tests/test_i18n.py` walks the
  rendered page and fails on any English literal that has no ZH counterpart, so a new label can no
  longer ship untranslated.

### Fixed

- **`session/create` retries without `cwd` when the host rejects it.** A dispatch from a DSH session
  whose working directory the host would not accept died at session creation, before the role agent
  ever saw the brief; `DshAdapter` now falls back to letting the host choose the directory.

## [0.7.1] - 2026-11-04

### Fixed

- **The settings section made the whole app fail to boot: `web boot: 1 entry did not activate` /
  `dsh-tower: pending (waiting for service: @deepseek-ai/dsh-client-ui-slots)`.** The client module
  exported `inject` with a *package* name where the loader expects a *service* id, so the entry
  stayed pending forever and the web boot aborted. The export is gone: plugins such as
  `dsh-claude-style` declare no inject at all, and this module already reads `ctx.slots` defensively
  inside `apply`, so a missing slot service costs the section, not the boot. The client test now
  asserts `inject` stays undefined, and the manifest no longer lists a client inject.

## [0.7.0] - 2026-11-04

### Added

- **A `Tower` section in the DSH settings sidebar.** The bundle now ships a client module
  (`lib/client.js`, hand-written in the harness' client module format, so no bundler is needed) that
  registers on `settings.section` and embeds the local control panel in an iframe, with 刷新 and
  新窗口打开 buttons.
- **The host owns the panel process.** On mount the plugin checks `127.0.0.1:<panelPort>` and starts
  `tower panel --port <panelPort>` detached when nothing listens; it stops that child on unload.
  `panel: false` disables the supervision and `panelPort` (default 8787) picks the port.

### Notes

- Read-only: the section renders the panel, it never writes to the board. Dispatch/accept still go
  through the `tower_*` tools.

## [0.6.2] - 2026-11-04

### Fixed

- **A provider that validates tool schemas strictly failed every turn once the bundle was
  installed.** The tools described `parameters` with the harness' field-map shorthand
  (`{ role: { type: 'string', required: true, description: ... } }`), which the harness accepts but an
  OpenAI-compatible endpoint validated as JSON Schema and rejected with
  `Invalid schema for function 'tower_role_add': {...} is not of type "string"`. Every tool now emits
  an explicit `{ type: 'object', properties, required, additionalProperties: false }` schema, so the
  tools load on strict and lenient providers alike.

## [0.6.1] - 2026-11-04

### Added

- **`tower plugin install`** puts the `dsh-tower` bundle into a DSH profile in one command. The
  desktop app owns its profile, so `dsh plugin --profile desktop ...` refuses to touch it and the
  bundle has to be written into `<profile>/node_modules/dsh-tower` and added to
  `dsh.profile.bundles` by hand. The command does both, from a local checkout (`--source`) or from
  the repository (`--profile` + download), is idempotent, and prints the one action left: restart the
  app. `--dry-run` shows the plan without writing.

## [0.6.0] - 2026-11-04

### Added

- **`tower bootstrap` wires a machine in one run.** It scans the listening ports for the desktop
  harness (confirming with `session/list`, and telling a token-protected host from a dead port),
  recovers the base64 HMAC secret from the environment or an existing bridge script, writes
  `base_url` + `secret` into the tower config, replaces verify commands whose executable is missing
  on this machine, resolves a session for every role (cache -> title match on the RPC row or the
  disk projection cache -> create) and pins the ids into `tower.toml`, then optionally sends one real
  brief (`--smoke <role>`) and waits for its report. `--delete-session <id>` removes a stray twin.
  Re-running is idempotent: it costs one `session/list`.

## [0.5.1] - 2026-11-04

Three defects found while migrating a live hand-rolled `.agents/` system onto tower.

### Fixed

- **Title lookup created duplicate sessions.** A long-lived desktop host can answer `session/list`
  with empty `projections.rows`, so no title was visible and `auto` fell through to
  `session/create`, producing a second session with the same title. The lookup now falls back to the
  on-disk projection cache (`~/.dsh/storages/session_projcache/sessions/*.json` ->
  `record.rows.title.val`), which keeps titles the RPC row does not carry. An RPC title still wins.
- **`tower role list` crashed on a GBK console.** A charter copied from an editor carries a UTF-8 BOM
  and a markdown heading; both reached `description` and `UnicodeEncodeError` killed the command.
  Descriptions are normalised on write and on read (BOM, leading `#`, collapsed whitespace), and the
  CLI degrades unencodable output with `errors="replace"` instead of raising.
- **`tower doctor` was silent about verify commands that cannot run.** A config using `python` on a
  machine without it on `PATH` failed every accept with exit 9009 and no diagnosis. `doctor` now
  resolves each verify command's first token and reports `is not on PATH` (shell builtins such as
  `echo` are recognised, explicit paths are checked directly).

## [0.5.0] - 2026-11-04

### Added

- `tower board` / `tower board --watch` and `tower panel` (local read-only web panel on
  127.0.0.1:8787, refreshes every 2s): one snapshot of the task board, two renderings.
- `docs/assets/panel.png` screenshot and a panel section in both READMEs.

## [0.4.0] - 2026-11-04

### Added

- **Roles no longer need hand-copied session ids.** `sessions` may map a role to an id, to `"auto"`,
  or to nothing at all; the adapter then resolves it in this order:
  1. `.tower/dsh-sessions.json` from a previous run,
  2. `session/list` matched by title (`role_titles`, falling back to the role name), which reuses an
     existing conversation such as "PRL UI前端",
  3. `session/create` in `create_cwd`, then `session/rename` to the role title.
  The resolved id is cached per workspace, so only the first dispatch pays for the lookup.
- `scripts/collect-dsh-env.ps1 -ApplyTo <tower.toml>` writes the detected `base_url` and `secret`
  straight into a tower config, so a machine can be wired with one command and no copy-paste.

## [0.3.2] - 2026-11-04

### Added

- **`scripts/collect-dsh-env.ps1`** — one command on a DSH machine prints the three values a `dsh`
  adapter needs: the harness `base_url`, the base64 HMAC `secret` (read from an existing bridge
  script or `DSH_BRIDGE_SECRET`), and every session whose working directory matches a workspace,
  with its title. Ports are found by probing the listening sockets of the running harness with an
  authenticated `session/list` call, so a GUI behind token auth is still detected (it answers 401
  rather than 404).

## [0.3.1] - 2026-11-04

### Fixed

- **The `dsh` adapter could not talk to a real DSH host at all.** It posted to
  `/rpc/<method>` with no authentication and a bare `{"request": ...}` body, while the
  harness serves `/api/<method>` and requires an HMAC-signed `dsh-auth-<sha256(authority)>`
  cookie plus a `{"type":"client-request", ..., "payload":{"args":...}}` envelope — an
  unauthenticated bridge simply gets `401`. The adapter now builds that cookie from a
  base64 `secret` (or runs anonymously when none is configured), sends the correct
  envelope, and calls `session/prompt` with the `payload.args.request` shape the host's
  strict codec expects (`sessionId`, `requestId`, `mode`, `content`, `clientTimeZone`).
- **The reply path no longer guesses.** `session/page` needs a `throughSeq` from a live
  `session/follow` stream, and on-disk transcripts are zstd-compressed, so the adapter
  now has the brief itself tell the role to write `<task_id>-REPORT.md` and polls that
  file (configurable: `report_file`, `wait_s`, `poll_s`). Stale reports from a previous
  round are deleted before the prompt is sent, so a dead session cannot fake a result.

### Added

- `authority`, `secret`, `timezone` and `report_file` options on `[adapters.dsh]`.
- Adapter tests against a local HTTP server: cookie construction and signature, the
  client-request envelope, `/api/` routing, the prompt argument shape, the missing
  session path, and the timeout path.

## [0.3.0] - 2026-11-04

### Added

- **Conversational bot management.** The lead agent can now build and reshape the team while you
  talk, instead of asking you to edit YAML:
  - `tower role list` / `tower_roles` — every role with its adapter, write scope, round budget and
    the DSH session id wired to it.
  - `tower role add <name> --description "..." --scope content/ --session session-...` /
    `tower_role_add` — appends the role to `tower.toml`, writes `roles/<name>/AGENT.md` from the
    description and scopes, and records the session id under `adapters.dsh.sessions`.
  - `tower role remove <name> [--purge]` / `tower_role_remove` — unregisters the bot (and drops
    its session mapping); `--purge` also deletes its charter directory.
- The edits are line-based: comments, ordering and every untouched role stay byte-identical, so a
  hand-written `tower.toml` is safe to grow from a conversation.
- Adding the first role works even in a config that has no `roles:` section yet (the bootstrap case
  when the lead creates the first bot).

The bundle therefore registers **eight** tools: the five from 0.2.x plus the three role tools.

## [0.2.2] - 2026-11-04

### Fixed

- **`tower batch <roles...> "<brief>"` skipped the brief instead of dispatching it.** `roles`
  uses `nargs="+"`, so argparse fed the trailing brief in as one more role and every task was
  rejected with `skip unknown role`. A trailing argument that is not a configured role is now
  taken as the brief (and announced on stderr); an empty brief fails with a clear message instead
  of creating report-less tasks.

## [0.2.1] - 2026-11-04

### Fixed

- **The DSH bundle could not mount on DSH 0.2.1-rc.5+, and failed silently.**
  Each tool's `output.schema` wrote `required: true` *inside* property nodes
  (`{ type: 'string', required: true }`). The harness validator
  (`assertSupportedJsonSchema`) rejects that spelling, so
  `ctx.tools.register()` threw `JsonSchemaError` from `apply()`, the bundle
  never mounted, and the five `tower_*` tools simply never appeared — with no
  error in the UI. `output.schema` is now normalised so per-property
  `required: true` flags are hoisted into the object node's `required` array.
- **`tower dispatch --json` printed the human report before the JSON**, so any
  machine consumer got prose plus JSON. It broke the DSH bundle
  (`"value.task" must be an object` because the payload degraded to a string)
  and any script parsing `--json` with `json.loads`. `--json` now writes exactly
  one JSON document to stdout; human lines move to stderr in that mode.
  The idempotency-hit path emits the existing task as JSON too.
- **The bundle now tolerates older CLIs** that still leak prose into `--json`
  output: `parseJsonOutput` tries every `{`/`[` start instead of only the first.
- **Docs cover the DSH desktop installation**, where the `desktop` profile is
  owned by the Electron application (`dsh plugin --profile desktop ...` refuses
  to touch it) and the `tower` CLI must be on the host process's `PATH`.

### Added

- Regression tests: output schemas validated against the harness rules (node),
  `--json` purity and human-mode output (python).
- The bundle declares `engines.dsh`, so an incompatible harness reports the
  mismatch explicitly instead of failing at mount time.
