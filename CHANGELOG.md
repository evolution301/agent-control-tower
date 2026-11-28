# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/): the Python package
(`agent-control-tower`) and the DSH bundle (`dsh-tower`) share one version.

## [0.19.0] - 2026-11-28

### Added

- **Hybrid Swarm + Control Tower mode (`src/control_tower/swarm/`)**: Combines an open shared-board swarm workflow with Control Tower's evidence-before-done verification gate (`docs/swarm.md`, `tests/test_swarm_mode.py`). All swarm state persists as plain, Git-friendly files under `.tower/swarm/` (`state.json`, `board.jsonl`, `workspaces/`), and key lifecycle events mirror into `.tower/ledger.jsonl`.
- **Shared message board, `@mention`/`@all` wakeup & `stopped` revival (`tower swarm post`, `tower swarm board`)**: Public append-only board recording briefs, replies, model switches, verifications, and monitor nudges. Idle workers sleep in `waiting` at zero cost and wake on `@<name>` or `@all` (with bounded cascading hops via `--max-hops`); `stopped` workers automatically revive when mentioned.
- **Dynamic worker spawning, sharding, memory forking & helper-adds-helper (`tower swarm add`, `tower swarm title`)**: Spawn workers on demand without editing `tower.toml`; fan out homogeneous copies with `--n <N>` (`<name>-1..N`) or shard a task list with `--tasks ...`; fork an existing member's (or `coordinator`'s) conversation history via `--fork <member>`; allow live workers to spawn and manage their own helpers via `--by <worker>` with strict ownership permissions.
- **Workspace isolation + safe merge & cleanup (`inherit` / `share` / `branch`, `tower swarm merge`, `tower swarm kill`)**: `inherit` shares the main repo for read-only reviewers/QA; `share` gives parallel coders isolated `git worktree` working copies (falling back safely to directory copies in non-git workspaces); `branch` creates isolated local clones. `merge` lands changes back to the main workspace; `kill` permanently retires a worker (guarding unmerged work unless `--land` or `--force`) and cleans up its temporary workspace.
- **Multi-backend per-worker model routing & lossless `model` switching (`tower swarm models`, `tower swarm model`)**: Route individual workers across `mock`, `cli`, `openai`, and `dsh` backends (`--model adapter:model` or `--adapter`); hot-switch any waiting worker's (or `coordinator`'s) model via `tower swarm model <name> <new_model> --reason "..."` while preserving its full conversation history and workspace, then auto-resume work.
- **Control Tower `verify --land` gate & `monitor --nudge` watchdog (`tower swarm verify`, `tower swarm monitor`)**: `verify` checks claimed files on disk and runs `verify_commands` inside the worker's workspace, landing changes automatically with `--land` only on PASS and posting real failure output back to `@<name>` on FAIL (`--no-review` records an explicit QA waiver). `monitor` scans for stalled `working` workers (`--stale`), unanswered `@mentions`, and unverified deliverables, with `--nudge` to auto-remind and reset stalled workers.
- **Offline `/swarm` real-time web panel (`src/control_tower/panel_swarm.py`)**: Integrated into `tower panel` alongside `/` and `/scene` using the vendored offline matte theme (zero CDN, light/dark toggle) and the same loopback/same-origin write guard — featuring live 4-state worker lamps (`working` / `waiting` / `stopped` / `killed`), workspace and unmerged-file badges, one-click spawn/model-switch/verify/merge/kill actions, and an interactive `@mention` board composer.
- **Offline 1-second end-to-end demo (`tower swarm demo`)**: Runs a full deterministic swarm walkthrough (spawn, post, switch model, verify, and land) with zero API keys or network.

### How to use / 快速上手

```bash
# 1. Run the 1-second offline demo / 离线 1 秒体验完整蜂群闭环（无需模型、无需网络）
tower swarm demo

# 2. Spawn workers with isolated git-worktree workspaces / 召唤分片工蜂（独立 git worktree 工位）
tower swarm add coder --brief "Implement module and unit tests" --tasks "auth" "billing" --workspace share --model openai:gpt-4.1-mini

# 3. Fork an existing worker's memory for a read-only reviewer / 克隆老工蜂记忆，零冲突复核
tower swarm add qa --brief "Review coder-1 changes" --fork coder-1 --workspace inherit

# 4. Post to the shared board to wake @worker or @all / 在共享消息板 @唤醒 指定工蜂或 @all
tower swarm post "@coder-1 Tighten error handling and re-run tests"

# 5. Losslessly switch model while keeping history & workspace / 无损换模型（保留完整记忆与工位）
tower swarm model coder-1 claude-sonnet-4 --reason "Need stronger reasoning for edge cases"

# 6. Run the Control Tower verify gate and auto-land on PASS / 走控制塔质检门，通过后自动合并回主分支
tower swarm verify coder-1 --land

# 7. Launch the offline real-time web panel / 启动离线实时网页看板
tower panel   # Visit http://127.0.0.1:8787/swarm
```

See [docs/swarm.md](docs/swarm.md) for the full guide, disk layout, and permission rules.

## [0.18.6] - 2026-11-26

### Added

- Session pool: `adapters.dsh.pool_size` pre-registers spare DSH sessions at bootstrap and registers them in the DSH workspace registry; `tower dsh pool list` shows free/claimed slots, `tower role add` and `tower dsh register --apply` claim a free slot so a new bot appears in the sidebar without restarting DSH; doctor warns when the pool is configured but empty (`tests/test_pool_config_2026_11_25.py`).
- `tower janitor`: zombie-task GC engine (`src/control_tower/janitor.py`, six detection rules: orphan role, no executor, overdue late, superseded, duplicate brief, report-on-disk-unaccepted). Dry-run by default with an evidence table; `--apply` resolves hits through attention with ledger notes; rule 6 is advisory-only and `--accept-reports` routes through the standard accept so verify and review gates still apply; doctor gains a zombie count and `tower projects` gains an open/zombie suffix.
- Cross-process `tower_compact` fallback in the DSH plugin: when the in-process fast path has no live agent, `/compact` is delivered to the owning session through the gateway `commands/execute` wire method (confirmed empirically against a live gateway). The receipt reports acceptance only — token counts are never invented; role misses now list the keys found in each config map plus plugin/CLI versions.
- Panel UI rebuild: Pico CSS vendored offline (`src/control_tower/panel_assets/`, no CDN), matte material for both themes (zero shadow/gradient, hairline borders #D2CDC2/#3B3B48, radius 14px, solid matte status chips, dark-only grain), small project monogram chips instead of oversized logo blocks, "next due" line emitted only when a due value exists and rendered from human-readable due strings, Office `~$` owner-lock and `.tmp`/`.lock` files filtered from the project registry; panel page rendering split into `src/control_tower/panel_hq.py`.

### Fixed

- Huddle/discussion turns now wait as *turns*, not as report files: the adapter marks discussion prompts, watches for true end-of-turn (pairing `step/start`/`step/end`, tolerating gateways without `turn/end`, scanning back across tool-only steps for the newest assistant text), and no longer mistakes a stale rolling report or a first-step preamble for the answer. A wholly failed huddle is recorded `partial` with the absent roles listed instead of being packaged as a clean plan (`tests/test_huddle_discuss_2026_11_25.py`).
- README rebuilt Chinese-first (`README.md` is now the Chinese landing page, English moved to `README.en.md`), with a plain-language explanation of what the tower is, the before/after problems it solves, an ASCII pipeline of how work flows, and the team-meeting / self-evolution / skill-library features.
- `tower kickoff --json` writes one JSON document to stdout; progress lines moved to stderr.
- Panel guard closes sockets only after draining the request body, ending intermittent WinError 10053 403s on real POSTs.

## [0.18.5] - 2026-11-25

### Fixed
- **Panel loopback guard hardened**: `_loopback` parses IPs with `ipaddress` (only `localhost`, `::1`, real `127/8` literals pass), so look-alike origins like `http://127.0.0.1.evil.com:<port>` get 403 and write nothing; missing ports now default to 80/443 instead of being refused.
- **组群开会 (kickoff --discuss) actually fills packages now**: roles answer in their trained report format, so `parse_position` falls back to `## Summary` (questions from `## Open issues`), the round-1 contract says so explicitly, and every turn's raw answer is persisted for replay. Replaying the original H-0001 huddle now fills `plan.best`.
- **Review gate**: `tower accept` refuses a task with no qa verdict while the tower has an effective review role (`--no-review` records the waiver). The adversarial review contract names its `## Findings` heading and the parser shares that single source of truth, so contract-exact reports score their findings; bullets carrying `| command:` are counted as fallback; unproved findings are reported separately.
- **Stuck reviews drain**: auto-queued review tasks record their verdict on the subject task and complete themselves instead of lingering in `rework`.
- **Verify isolation stops false rejections**: the dirty-tree verify worktree now carries the full uncommitted working set; collection errors confined to files outside the task's own list become `verify-deferred`, never `rework`.
- **`tower kickoff --discuss --json` gains a top-level `discuss` key** (`state` / `absent` / `seconds`), keeping `package.discuss` for compatibility.
- **Worker status shows every in-flight job** (`pool: N` plus per-job lines) and `--concurrency N` is honoured; ghost heartbeat entries no longer outlive their process.
- docs/architecture.md no longer overclaims serialisation; doc-drift guards keep it honest.

## [0.18.4] - 2026-11-24

An outside auditor read the 0.18.3 tree, found six defects and four stale
documents, and was right about every one of them. This release is those fixes.
The interesting part is not the list; it is that all six were invisible to a
green test suite, because the tests were written from the same assumptions as
the code.

### Fixed — the lock manager only worked on Windows

`locks.py` imported `msvcrt` unconditionally while its docstring promised a
POSIX branch, so `fcntl` was never used and 18 async tests could only ever pass
on this machine. The backend is now chosen at import (`os.name == "nt"` →
`msvcrt`, otherwise `fcntl.flock(LOCK_EX | LOCK_NB)` with the same
busy-wait-until-timeout), and a host with neither raises
`no file-locking backend: need msvcrt (Windows) or fcntl (POSIX)`.
(`locks.py`)

### Fixed — `worker --status` forgot every job once the pool grew

With the default `workers: 4` the main loop rewrote the heartbeat file between
jobs, so a running job's entry was overwritten about 0.2 s after it started and
`tower worker --status` could only ever show the process, never the work. The
heartbeat now keeps `jobs: {<job id>: …}` and clears one key at a time, so the
same command lists every in-flight job with its task and elapsed time.
(`worker.py`)

### Fixed — `serial_requests` never applied to async work

The worker read `payload["role"]` to decide whether two jobs for one role may
run at once, but no `enqueue()` caller ever set that key, so the setting was
dead for the only path that needs it. The worker now resolves the role from the
task file when the payload lacks it. (`worker.py`)

### Fixed — the panel wrote tasks for anyone who asked

`POST /api/actions` and `POST /api/ask` accepted any request the panel received:
no `Origin` or `Host` check, and `?project=` could address any registered tower.
A page open in the same browser could therefore dispatch text into another
project. Writes now require a loopback `Host` on the panel's own port, an
absent or same-origin `Origin`, and `Sec-Fetch-Site` absent or same-origin,
else `403`; `?project=` stays inside a registered project the panel was started
for. (`panel.py`, `scene_actions.py`)

### Fixed — the acceptance rate could not go down

`analytics.py` divided accepted tasks by the tasks currently in rework, so a
reworked-then-accepted task vanished from the denominator and the tower printed
`verified rate: 100%` no matter what. The denominator is now every task that
reached a settlement, the report prints `accepted / settled`, and
`rework_rate` is reported separately. On this repository it reads
`70% (19 accepted / 27 settled), rework rate 11%`. (`analytics.py`)

### Fixed — adversarial review silently never ran

`review_roles` defaults to `["auditor"]`, a role this tower does not have, so
every verdict was empty and nobody noticed. `effective_review_roles()` now
returns only roles that exist, records the missing ones as
`review_roles_missing`, and `doctor` warns
`adversarial review never runs: review role 'auditor' is not a role here`.
(`config.py`, `cli.py`)

### Docs — four documents described an older tower

The READMEs and `cordis.patch.yml` said the bundle registers `five tools`
(it registers 19); `docs/architecture.md` still described a sequential loop
with no lock manager; `docs/dsh-quickstart.md` said the adapter polls
`session/list` when it polls the report file; `docs/superagent-ideas.md` said
`ready` needs a human when only `blocked` and `failed` do. All five are
corrected and `tests/test_docs_drift_2026_11_24.py` fails if the stale phrasing
returns.

## [0.18.3] - 2026-11-24

Both defects came out of the 0.18.2 run itself: a human acceptance that a late
turn overwrote, and a verification that mistook two other lanes' edits for a
broken change.

### Fixed — a settlement can no longer reopen what a person closed

A task that was accepted while its turn was still running was flipped back to
`reported` by the settlement and auto-triaged as `reworked`. `dispatch()` now
reloads the task before writing any outcome: when the status is `accepted`,
`failed` or `dropped` the settlement is dropped with `settle_ignored: task
already <status>`, the report is kept for the audit trail, and accepting also
cancels the in-flight job so a later turn cannot resurrect the task. (`worker.py`,
`dispatch.py`, `board.py`)

### Fixed — a moving tree is not a defect

`python -m pytest -q` ran in the live tree while two other lanes were editing
`src/` and `packages/`; the run exited 1, triage called it `rework`, and the lane
had to prove with four clean runs and a pristine worktree that nothing was wrong.
`verify_report()` now runs the configured checks in a temporary `git worktree` of
HEAD carrying only the accepted task's own files whenever the live tree is dirty
with edits the task does not claim; a run whose inputs change underneath it is
reported as `verify-deferred` (attention, not rework), never as a failure.
(`verify.py`, `cli.py`)

## [0.18.2] - 2026-11-24

Two acceptance bugs reported by a customer tower (aimigao-zh) after their
T-0019, plus one defect found while reproducing them.

### Fixed — the MCP accept wrapper called a success a failure

`tower_accept` decided the outcome from the output text: `runTower` rejected
whenever `execFile` reported an error and `acceptTool` looked for the literal
`ACCEPTED`, so the informational `resolved workspace = …` line produced
`Error: … failed: resolved workspace = …` for a task the CLI had accepted. Both
now judge by exit code; informational lines travel in the success payload and a
real failure carries the exit code plus the last meaningful lines.
(`packages/dsh-tower/index.js`, `test/tools.test.js`)

### Fixed — accept and verify_gate disagreed about `## Files`

Every line of the report's Files section was stat'ed, so a prose line
(`无新增文件（仅修改）`) failed the whole report with `reported files do not exist`
while `tools/verify_gate.py` — run against the same report — passed. One shared
rule now decides what is a path: a candidate needs a path separator or a filename
extension, the same rule guards brace patterns, and a Files section with no paths
at all means "no files claimed", not "files missing". (`verify.py`)

### Fixed — a dispatch to a group that does not exist died silently

`tower dispatch --group G-0002` with no such group saved the task, queued the
job, and the worker settled it in under a second with `no such group` while the
board kept showing `pending`. The group is now validated before anything is
written, an async dispatch never enqueues a job its own gate would refuse, and a
task that reaches the worker with a missing group is marked `failed` with the
reason. (`dispatch.py`, `cli.py`, `worker.py`)

### Added — a progress report keeps a long turn alive

When half the deadline is gone and no report exists, the lane is asked to file
`Status: progress`. A progress report neither settles nor fails the task: it
refreshes the wait window, is logged as `progress_report`, and is visible in
`tower show`. A real report still settles normally. (`worker.py`, `supervise.py`)

## [0.18.1] - 2026-11-23

Everything in this release came out of using the tower to build the tower: five
defects were reported by a customer tower running 14 roles in production, and
four more were found the same day in our own runs on this repository.

### Fixed — a settlement that nobody was told about

With `notify.kind: inbox` a finished task appended a line to `.tower/inbox.jsonl`
and nothing else, so the secretary never learned that a bot had come back; the
`lead_session` channel existed but was never exercised end to end. The worker now
pushes settlements to the lead session (falling back to the lead role's own
session for inbox towers), the line carries `report: N files, M checks`, and
`tower notify test` proves the channel in one command. (`worker.py`, `cli.py`)

### Fixed — the async worker could starve the whole queue

One job stuck inside a turn blocked a single-threaded worker for 40+ minutes
while the heartbeat kept ticking, so `worker alive: true` meant nothing. Every
job now runs under a deadline, session usability is checked before the prompt is
sent, and the heartbeat records `current_job`, `current_kind`, `started_at` and
`task`, which `tower worker --status` prints as a backlog warning. (`worker.py`)

### Fixed — `--dry-run` created a real task

`tower dispatch … --dry-run` saved the task and answered with an id, so probing a
brief left phantom work on the board. Dry runs now return before anything is
written. (`dispatch.py`, `cli.py`)

### Fixed — `bootstrap` reported success it never had

A bare scalar `base_url` was never rewritten (the rewrite only matched quoted
values), the probe targeted the wrong endpoint, and a failed config write still
printed a success line. All three are gone; bootstrap reports what it changed and
refuses to claim the rest. (`bootstrap.py`)

### Fixed — `session/observeState` cannot be called over plain RPC

It is a streaming method, so `tower queue list|purge` failed against every real
gateway with `stream Remote methods must be opened through the stream carrier`.
The queue helpers now degrade honestly (`QueueUnavailable`) and purge by item id
through `session/updateQueue`, which is the call that actually removes a message.
(`adapters/dsh.py`, `queue.py`)

### Fixed — `tower role list --tree` crashed

The tree renderer keyed rows on `name` while the board returns `role`.
(`cli.py`)

### Fixed — `tower_compact {session: "role:qa"}` could not resolve a role

The tool only read top-level `cfg.sessions`; it now resolves `cfg.sessions` →
`cfg.adapters.dsh.sessions` → `cfg.adapterOptions.dsh.sessions` and names the
places it searched when it still cannot find the role.
(`packages/dsh-tower/index.js`)

### Changed — the acceptance gate tells a timeout apart from a failure

A verify command that exceeds its budget no longer marks the task failed with
`rework`; it is reported as a timeout with the elapsed time, and the budget is
configurable. `tower resolve`/`resolve_attention` now lands a terminal status, so
a dropped task stops appearing as work in progress and is not re-dispatched.
(`verify.py`, `board.py`, `cli.py`)

## [0.18.0] - 2026-11-23

The tower grows a shape: guided teams, a kickoff before the first dispatch, one
room per task, a shared skill library, two layers of command, and a deadline on
every brief. A flat `tower.toml` keeps working — every new block is optional.

### Added — `tower role add --guided` builds a whole team

A working bot was never one file. `tower role add` now asks for a project kind
(`b2b-site`, `service-api`, `content-site`, `generic`), offers that pack's roles
(lead / frontend / backend / seo / content / qa, each with a scope and a
`report_to`), and writes the pieces together: `roles.<name>` in `tower.toml`,
`adapters.dsh.sessions.<name>` and `role_titles.<name>`, the charter under
`roles/<name>/AGENT.md`, and the DSH sidebar registry. It ends by telling the
operator which piece is still missing and that DSH needs a restart — the old
checklist is printed in full, so the two-minute "why is my bot invisible" hunt
is over. (`templates.py`, `roles.py`, `cli.py`)

### Added — the sidebar registry is written, not explained

`tower dsh status` lists every role with its session id and whether the session
is in the DSH sidebar; `tower dsh register --apply` adds the missing session ids
to `$DSH_HOME/storages/workspace.json` (with a timestamped backup) and never
invents a workspace it cannot find. (`sidebar.py`)

### Added — kickoff: agree before dispatching

`tower kickoff "<brief>" --role backend --role qa` opens a room for the task
and writes `.tower/groups/<G>/package.json` with the four things a dispatch used
to assume: a plan (with alternatives and why), assignments with write scopes,
acceptance commands plus thresholds, and the dependency / merge order. A draft
package is honest — `tower group show` lists exactly what is still missing.
With `[kickoff].require = true`, `dispatch` refuses to start until the package
is complete, and every dispatch prompt carries the plan, the role's own part,
the acceptance bar and the merge order as a compact block. The `/showroom/`
three-round story is what this replaces. (`kickoff.py`, `dispatch.py`)

### Added — one task, one room, closed when it ships

Every dispatch attaches its task to a group (auto-created from the brief unless
`[groups].auto = false`); the group walks `created → discussing → working →
reviewing → closed` and `tower accept` closes and archives it to
`.tower/conversations/<G>-closed.md` once no task in it is still open. Context
no longer mixes between unrelated tasks. (`groups.py`, `cli.py`)

### Added — one skill library for every role

Skills written by one role used to be invisible to the next. `.tower/skills`
is now a shared library: `tower skill search <words>`, `tower skill show <id>`,
`tower skill patch <id> --file …` (version bump, previous body archived under
`.tower/skills/.history`), `tower skill stats`. Dispatch injects only the index
of matching skills (four lines), never the bodies. (`skills.py`, `dispatch.py`)

### Added — a lane leader between the secretary and the workers

A role can declare `leader: true`, `report_to: <role>` or `secretary: true`.
When any role declares one, `tower_say` enforces the chain: a worker writes its
lane leader, a leader writes its members or the secretary, and the secretary
writes leaders — not members. A refused note is still filed and answered with
one circuit-breaker line, so nothing is silently dropped. `tower role list
--tree` draws the chart with each role's sidebar state. (`org.py`,
`converse.py`, `cli.py`)

### Added — deadlines, heartbeats and overdue escalation

Every dispatch carries a deadline (`--due 90m`, ISO, or `YYYY-MM-DD HH:MM`; the
default is `4h`, `[deadline].default`). The board stamps `last_heartbeat` on
every status change. When a task passes its deadline, `tower supervise` asks the
**lane leader** first — "把结论回给我；不要自己重做这项工作" — and only escalates
to the inbox (`overdue_escalation`) after the second unanswered pass. (`supervise.py`,
`board.py`)

### Compatibility

- A tower with no `leader`/`report_to`/`secretary` keeps the flat behaviour; the
  hierarchy check is a no-op.
- `[kickoff].require`, `[groups].auto` and `[sidebar].register` default to
  `false`, `true` and `true`; `[deadline].default` defaults to `4h`.
- The only new tightening is the deadline default: a dispatch with no `--due`
  now gets four hours instead of no deadline.

## [0.17.3] - 2026-11-23

### Fixed — a bot that outlived its timeout was judged failed

T-0202 ran 52 turns, the adapter gave up at 1800 s and the task turned `failed`
at 12:01; the complete REPORT landed at 12:44 and nobody looked again.

- **A report on disk is work to verify, not a failure.** When an adapter exits
  non-zero, `dispatch` now reads `roles/<role>/<TASK>-REPORT.md`; if it parses to
  a summary, a file list or a check, the task goes to `reported` (note: *adapter
  exit N but the report file landed — verify, do not trust*) and is logged as
  `late_report`. An empty or header-only file still fails. (`dispatch.py`)

### Fixed — the tools resolved the demo tower

`tower_dispatch` answered `unknown role 'frontend-2'; known: backend, frontend,
qa` while `tower_projects` showed the real project as active: the profile config
still named the mount-time demo directory, and it outranked the active-project
pointer.

- **`tower_use` wins now.** `resolveWorkspace()` orders pointer → profile config
  → last used → cwd, and every tool answer starts with
  `resolved workspace: <path> — from <source>`, plus a drift note when the
  profile config names a different tower. `tower dispatch` records its binding
  for the workspace it really used. (`packages/dsh-tower/index.js`)
- **`unknown role` names the tower.** The message carries the workspace path, the
  roles it really has, and the two ways to re-aim (`--workspace <dir>`,
  `tower projects use <name>`). (`config.unknown_role_message`)

### Changed — the board tells the truth about a dead adapter

The harness changes port on every restart; a stale `base_url` left tasks
`in_progress` for an hour with a worker that died long before.

- **`tower status` probes the adapter** and prints `reachable` or `DOWN — nothing
  is listening … update adapters.dsh.base_url`. (`cli.py`)
- **`tower doctor` calls out a verification chain that verifies nothing** — an
  empty `verify_commands`, or only builtins such as `echo ok` — instead of
  leaving it to accept time. (`cli.py`)
- **`tower role add` prints the four-piece checklist** (role, session, sidebar
  title, charter) with the missing pieces unticked. (`cli.py`)

### Fixed — SIGTERM drains instead of abandoning a turn

`kill` used to end the worker mid-turn; the DSH session then accepted messages
but never started another turn and `session/cancel` could not revive it
(three sessions, 2026-11-22).

- **SIGTERM/SIGBREAK stop claiming and wait for the in-flight job**, exactly like
  a first Ctrl-C; a second signal abandons it. Handlers are restored on exit.
  (`worker.py`)

## [0.17.2] - 2026-11-23

### Fixed — a rejected queue call looked like a successful purge

Found on a live tower with 181 messages stuck across three bots. The clean-up
script read `{"ok": false, ...}` as "no error" and printed *cleared* while not a
single message was removed.

- **`DshAdapter.rpc()` now raises `DshRpcError`** instead of handing back a
  reply the caller can misread. A top-level `error`, a `result.ok is False`, or
  an `error` next to no `value` is an exception carrying the gateway code
  (`gateway/arguments-invalid`, `http/401`, …). `_result_value()` raises for the
  same shapes rather than returning `None`. Scripts can no longer mistake a
  refusal for an empty result. (`adapters/dsh.py`)
- **Flat arguments are wrapped for you.** `rpc("session/updateQueue", {…})`
  sends `{"request": {…}}`; `session/list` keeps its `{"_request": {}}` shape.
  This was the reported failure: `session/updateQueue` with a flat payload
  answers `missing "request"; unexpected "sessionId", "itemId", "action"`.
- **`tower queue list <role>` / `tower queue purge <role>`** (`--only
  peer|notice|all`, `--max N`, `--session ID`, `--json`) list and clear a
  session's queue through `session/observeState` + `session/updateQueue`. Purge
  re-reads the queue afterwards and prints `queue N → M`, so *"0 removed"* is
  visible instead of implied success; a refusal exits non-zero with the error.
  (`cli.cmd_queue`, `queue.py`)
- **Peer notes are rate-limited and direction-aware.** New `[peer_notes]`
  config: `mode` = `quota` (default) | `lead-only` | `open`, `lead` = `lead`,
  `per_hour` = `10`. Bots may always write to the lead; anything over quota is
  filed with `delivered: false, throttled: true` and answered once per hour with
  a breaker note telling the sender to fold it into the next report. Work briefs
  are never throttled. (`peer_policy.py`, `converse.say`)

### Changed — less context burned per dispatch

- **The contract is sent in full once per role.** Later dispatches get a compact
  header (task, report file, scopes, status vocabulary) unless the contract text
  or write scopes changed, or `contract.repeat_every` (default 5) full sends have
  passed. One live session had eaten 19 full copies. (`dispatch.contract_plan`)
- **Settlement notices coalesce per task.** Repeated `dispatched → rework →
  settled` states update one unread inbox line (`count`, `first_ts`) instead of
  appending a new one. (`board.notify`)
- **`tower inbox --limit/--offset`** pages the inbox (default 20, `0` = all) and
  prints `inbox: 1-20 of 74 — more: --offset 20`; `tower_inbox` in the bundle
  takes the same two parameters. A single `all=true` read used to dump 74KB.
- Queues above `queue_max` (default 20) auto-prune only the oldest
  notification items before a delivery, and the delivery result carries
  `queue: N (auto-pruned M)`.

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

## [0.17.1] - 2026-11-22

### Added — in-chat slash command `/塔台引导` (`/tower-guide`)

The user who needs onboarding most is standing **in the DSH chat**, not in a
terminal. Both names (ASCII + 中文) now register a host command that renders a
**live** checklist built from `tower doctor`: DSH connection (with port), role
count, secret 401 hint (where `.credentials.yaml` lives), unbound-session
warning (the "dispatch runs forever" failure), queue/worker state — each ❌
mapped to its step number of the four bindings. The closing text carries the
two required sentences verbatim: sidebar visibility needs a DSH restart, and
the graduation test (dispatch a "reply `ready` only" task through
dispatch → report → verify → accept, naming the broken step).

- Registration is guarded: hosts without a `commands` service (headless) simply
  don't get the command — the plugin never pends (the compaction lesson).
- `runTowerText` resolves CLI text output even on non-zero exit (`doctor`
  exits 1 exactly when the guide has something to show).
- 4 node tests cover: all-green, 401→secret hint, no-roles→step 1,
  unbound-sessions→the forever-running warning. Live-smoked against the
  evergocasa tower (port 62721, 9 roles, warnings rendered).

## [0.17.0] - 2026-11-22

### Fixed — the 5-bug field batch (21 real reports run; the loop works, the edges bit)

**B1 — `tower_use` could never take effect (mount-time binding outranked the
pointer).** `targetWorkspace()` resolved configured → binding → pointer, and
`apply()` recorded a binding on every mount, so the first mount pinned the demo
tower forever. Now: the pointer (an explicit `tower projects use`) **outranks**
the binding, the mount-time record is gone (the first successful *dispatch*
records instead), and `projects use` also re-aims `binding.json` — one file, one
order. `unknown role` errors now carry the workspace.

**B2 — `bootstrap` exited 0 with a wrong secret.** A 401 with no secret is now a
hard error (non-zero exit) that lists every location checked
(`$DSH_BRIDGE_SECRET` → `SECRET_B64` in `*.py` → `~/.dsh/.credentials.yaml`) and
where the harness keeps it; nothing gets wired. The credentials file is a new
secret source (read-only, never echoed).

**B3 — stopping the worker left DSH sessions half-dead and tasks `running`
forever.** Ctrl-C now **drains**: in-flight task ids are printed and the worker
waits (second Ctrl-C or `--force` abandons). On start, `_adopt_orphans` sends
tasks that were claimed/in_progress, stale, and uncovered by a live queue claim
back to **rework** with a `task_interrupted` audit event. `doctor` flags running
tasks past the adapter timeout with no worker alive.

**B4 — a DSH restart (new port) made dispatches hang instead of failing.** Every
dispatch pre-flights the dsh adapter (`session/list`, 3 s): unreachable/401 →
`dispatch REFUSED (pre-flight)` with the doctor wording, exit 2 — before a task
id exists that will never move.

**B5 — `verify_commands: ["echo ok"]` was the default (self-grading).** `tower
init` ships the option commented with real examples; `bootstrap` **comments out**
unrunnable commands in place instead of replacing them with `echo ok`; `tower
accept` prints a loud WARN while every configured check is a builtin.

### Added — the "four bindings" model

A bot is four artifacts (session / `roles.<name>` / `sessions`+`role_titles` /
`AGENT.md`), and missing #3 is the classic "dispatch runs forever". `tower
onboard --from notes.md` now understands `session:` and `title:` lines per role
and wires the session pin + charter in the same pass; its result carries a
`next` field with the four-bindings checklist and the graduation test.
`docs/dsh-quickstart.md` gained the four-bindings table; `doctor` already flags
pinned sessions that are not on the harness.

## [0.16.1] - 2026-11-20

### Fixed — `deploy check/pull` 读不到远端主题文件（read_file 路径口径）

`trans.php` 的 `read_file` 只认**主题目录内相对路径 + `./` 前缀**（w.py 内部就是
`'./' + path`）。`deploy_fetch` 原样透传 `wp-content/themes/<theme>/functions.php`，
得到 `HTTP 400 Invalid path`，守卫完全不可用（2026-11-20 实测）。

- `control_tower.deploy_fetch` 新增 `normalize_remote()`：接受 `./x`、`x`、
  `wp-content/themes/<theme>/x`、`/html/wp-content/themes/<theme>/x`、反斜杠路径、
  主题目录下的绝对路径，统一归一化为 `./x`；`--theme` 可覆盖主题名（默认取 `--cwd` 目录名）。
- 错误信息现在带上实际发送的路径；`Invalid path` 时直接给出 `./` 写法指引。
- **基线改为按本地文件键控**（记录里仍保存 remote）：换 remote 写法不再丢基线；
  旧的 `local+remote` 记录会被自动采纳（取最新 `baseline_at`），`status` 每个本地文件
  只显示一行。
- `_render_command` 只替换五个已知占位符（不再 `str.format` 整条命令），命令里合法的
  `{}`（python 一行式、shell `${VAR}`、JSON 片段）不会再被吃掉。
- 生产复核：evergocasa `functions.php` 用 `--remote wp-content/themes/generatepress-child/functions.php`
  与 `--remote ./functions.php` 两种写法均 `in_sync`；基线已刷新为
  `39025b1c0d3359bb`（receipt 20261120-090624）。

## [0.16.0] - 2026-11-11

### Added — deploy guard（线上直改未同步 → 上传静默覆盖，T-0102 事故）

- `tower deploy check|init|pull|done|status <local> --remote <path>`：对
  「本地 ↔ 远端」维护 baseline，三方比较：
  `remote == local` = in_sync；`remote == baseline` = local_changed（可上传）；
  否则 **remote_drift → 拒绝上传**，必须先 `pull` 或人工合并。
- `pull` 先把本地旧版备份到 `.tower/deploy/backup/`，再写入线上内容并推进基线；
  `done` 在上传后强制验证「远端 == 本地」，不成立即报 "upload did not land"。
- 回执：`.tower/deploy/receipts/<ts>-<key>-<action>.md`；`--role <role>` 时追加
  `roles/<role>/DEPLOY-SYNC.md`；全部动作进 ledger（`deploy_init/check/pull/done`）。
- `control_tower.deploy_fetch`：`python -m control_tower.deploy_fetch --cwd <theme-dir>
  --remote <path>` 通过项目自带 `w.py` 的 `sync_request read_file` 取远端内容，对任何
  w.py 站点通用；tower.toml 里 `deploy.fetch` 配置一次（支持 `{remote}` `{local}`
  `{workspace}` `{project_root}` `{python}` 占位符）。
- 部署纪律随派单注入：`docs/deploy-rules.md` 会进入每次 dispatch prompt 的
  `## Deploy rules` 段。
- 生产实测：evergocasa `functions.php` 已纳管（baseline 78c69e1e…），`check` 返回
  `in_sync`，回执与 `roles/frontend/DEPLOY-SYNC.md` 已生成。

## [0.15.0] - 2026-11-10

### Breaking — the CLI no longer falls back to the global active-project pointer

With three towers alive (evergocasa / prl-ops / offroadleagues), running a command
in a directory that has no `tower.toml` silently resolved against the *active*
project: `tower say ops deputy "…"` answered `unknown role 'deputy'` (its roles
came from another tower), and the same path would have misrouted `dispatch` /
`accept` / `relay`. Field report 2026-11-10.

- **Resolution is now exactly `--workspace` > `tower.toml` found walking up from
  cwd > a loud error.** `~/.tower/binding.json` and the `tower projects use`
  pointer are no longer consulted by the CLI; they exist for the GUI panel and
  plugin reconnection only.
- **Every write command announces its target**: `resolved workspace = <path>` on
  stderr (stdout stays a single JSON document under `--json`). Covered:
  `dispatch`, `accept`, `rework`, `relay`, `say`, `escalate`, `batch`, `restate`,
  `review`, `attention claim|resolve`.
- READMEs document the rule and the pointer's scope; regression tests pin that a
  registered+active tower elsewhere is never touched from a foreign cwd.

## [0.14.1] - 2026-11-10

### Fixed — false "no worker" alarm right after a spawn

`ensure_worker` returns the instant the detached process is created, but the
heartbeat lands 1–3 s later (cold Python start). The consumer check added in
0.14.0 trusted the spawn result and could report "worker could not be started"
while the worker was in fact booting (reproduced in a parallel-dispatch smoke
test: 3 tasks queued, worker pid alive seconds later).

- One `_ensure_consumer()` path now waits up to 10 s for the heartbeat whenever
  a worker is expected, and only then fails (exit 1). Single dispatch, parallel
  dispatch and `tower review` all use it.
- Tests that fake a spawn now fake `worker_alive` too (a fake consumer must
  answer both questions).

## [0.14.0] - 2026-11-10

### Added — parallel dispatch (a bot can hold several jobs) + queue supervision

- **`tower dispatch <role> --brief-file A --brief-file B …`** queues one task per
  brief and returns immediately — no more waiting for brief #1's 30-minute adapter
  turn before brief #2 is even submitted. Also `--parallel` for a single
  no-wait submit. Each task runs async; the bot's own session may serialise them.
- **`tower worker --status`** prints the consumer state for the workspace:
  alive/pid/version/heartbeat age, queue depths, oldest pending age, backlog alarm.
- **enqueue auto-starts a worker** for its own workspace — the 2026-11-10
  incident: 12 adversarial reviews sat 33 hours because the only worker belonged
  to another project and accept-time enqueue never ensured a consumer.
- **Backlog alarm**: `tower status` / `tower inbox` print a ⚠ line when pending
  work has no worker, or the oldest pending job exceeds `queue_alert_minutes`
  (default 15); `tower doctor` reports it as a problem.
- **`dispatch --async` / `tower review` fail loudly** (exit 1) when no consumer is
  available after the spawn attempt, instead of silently queueing forever.

### Fixed — Files section separator `·`

- `accept` verification splits multi-path lines on `·`/`•`/tab as well, so
  `a.json·b.json` is two paths, not one nonexistent one (T-0062 round-1 case).

## [0.13.1] - 2026-11-09

### Fixed — relay contract taught a flag the CLI did not accept (B17)

`RELAY_CONTRACT` (the text delivered to every relayed session) and the module
docstring said `tower relay --to <target> -m "..."`, while the CLI only parsed a
positional target. A peer following the contract got `unrecognized arguments:
--to` and never answered, so `--await-receipt` waits could hang for nothing.

- The CLI now **accepts both**: `tower relay <target> -m "…"` (canonical) and
  `tower relay --to <target> -m "…"` (alias). A missing target is a clear exit-2.
- The contract and docstring now teach the positional form (with `--to` named as
  an accepted alias), so either style a peer copies works.
- Regression test covers both forms and the contract wording.

## [0.13.0] - 2026-11-09

### Fixed — prl-ops field batch B11–B16

- **B11 「待总控」虚高**: queue tasks older than `stale_hours` (default 12) are
  now 陈旧 — excluded from `needs_lead()`/「待总控」, counted as `stale` and listed
  separately (`/api/snapshot.stale`, HQ line 「陈旧 N（不在待总控内）」). Terminal
  tasks (`accepted`, `status/state = dropped`) appear in no queue at all. Field
  check on prl-tower: 待总控 11 → 6, 陈旧 0 (15 hand-dropped tasks closed).
- **B12 `tower triage` hung on stale/failed re-dispatches**: re-dispatching is now
  opt-in (`tower triage --retry`); the default pass only verifies/accepts. A retry
  turn is capped by `--budget` seconds (default 300) instead of the role's full
  timeout, so one adapter cannot hang the lead for 10+ minutes.
- **B13 `tower status --json` broke pipes** (`Invalid \escape`): JSON output is now
  pure ASCII (`ensure_ascii=True`), so PowerShell 5.1's cp936 decoding of native
  output can no longer corrupt it. Verified: 1.3 MB output, all bytes < 128.
- **B14 multi-path Files lines**: one line listing `a.json、b.json` is split on
  `、 , ; ， ； |` and every segment is checked (brace patterns stay intact).
- **B15 `tower accept` used the cached report**: `verify_report` now prefers the
  freshest `<task>-REPORT.md` on disk over `.tower/tasks/T-….json`'s cached parse,
  so edits to the Markdown take effect on accept.
- **B16 old reports rejected for missing `## Self-review`**: compatibility via
  `self_review_since` (reports created before it skip the gate) plus
  `tower accept --legacy` for a forced one-off override.

## [0.12.1] - 2026-11-09

### Fixed — delivery to the issues bot IS the claim

The moment a blocked/failed task is auto-delivered to `escalation.role`, the
attention is claimed on the issues bot's behalf (`claimed_by=issues`), so the task
leaves 「需要你」 immediately and counts as 处理中 — it no longer waits for the
bot's next turn to claim it. Verified end-to-end with the real CLI: exhausted
task → auto-escalated → `attention 0 · attention_claimed 1` → issues ticket
dispatched and accepted.

## [0.12.0] - 2026-11-09

### Added — 问题同步：issues bot + 自动收口 + 秘书链路（John 高优先级）

看板上「需要你 5」的任务其实早已被后续工作取代/属外部额度，但没人收口，一直挂到
John 亲自来问。这一版把**收口变成塔台自动行为**：

- **自动投递**（关键）：`dispatch`/worker 结束若 `state=blocked` 或 `status=failed`
  （重试用尽），塔台把该任务作为**问题工单**投给 `escalation.role`（默认 `issues`），
  而不是留在「需要你」等人。同一任务幂等（`task.escalation` 记录 delivered/issues_task/
  attempts），工单未关闭前不重复投递。

  ```yaml
  escalation:
    enabled: true
    role: issues
    max_attempts: 1
  ```

- **attention 生命周期**：`open → claimed(by issues bot) → resolved(reason)`。
  `tower attention claim <task> --by issues --note …` 后任务进「处理中」，**不再计入
  「需要你」**；`tower attention resolve <task> --as dropped|rework|accepted --note …`
  落终态并清 attention。已 resolve 的任务，triage **不会**再抛回 blocked。
- **秘书链路可审计**：`tower escalate <task> --to deputy|planner|… --note …` 生成承接
  任务并记录 `escalation.chain`（issues → deputy → planner），原任务 ledger/events 留痕。
- **面板同步**：`/api/snapshot` 新增 `attention_claimed` / `attention_resolved`；
  HQ 顶部状态行显示「处理中 N · 已处理 M」，与 `needs_person()` 实时一致。
- 工单模板（kind: tower-bug|exec|infra|policy，severity，回写要求）随投递 brief 一起
  下发；链路与判断标准见各塔 `docs/issues-flow.md`。

### Wired

- evergocasa 塔：新增 `issues` 角色（Evergo 问题处理，session 已接）、
  `roles/issues/AGENT.md`（来自 `_charters/issues.md`）、`escalation` 配置；
  `tower doctor` = problems: none。

## [0.11.0] - 2026-11-09

### Fixed — the prl-ops field batch (B1–B3, B9, B10)

- **B1 — a long-running worker dispatched with a stale tower.toml** (swapped role
  session ids, tasks still went to the old sessions). Two layers: the worker
  re-parses config per job (0.10.4) **and** the heartbeat now carries a config
  hash — `ensure_worker` retires a worker whose tower.toml changed and spawns a
  fresh one.
- **B2 — pytest leaked detached workers** (44 immortal `pytest-of-Administrator`
  workers at full CPU). The autouse `never_spawn_worker` fixture blocks real
  spawns in every test; workers also exit when their workspace disappears.
- **B3 — tasks stuck in `claimed`/`in_progress` with no live worker had no
  escape hatch.** New `tower unstick <task> [--as pending|dropped]` (pending
  re-queues, dropped closes as failed; ledgered), plus a `⧗stale` marker on
  `tower status` when a claim sits longer than `staleness_minutes` (default 30).
- **B9 — `tower inbox` crashed with `error: 'role'`** on entries that predate
  the role field: the printer now defaults missing ts/task/role and falls back
  to a top-level `summary`.
- **B10 — long briefs fought PowerShell quoting.** `tower dispatch --brief-file
  <path>` (UTF-8) — `'<'`/`'>'` no longer get eaten as redirects.

### Noted — DSH-side gaps (B7/B8, documented, not tower bugs)

`session/create` accepting only `cwd` leaves sessions ungrouped and
`workspace/move-invalid` has no `workspace/accountSession` escape; empty-arg
`session/create` returns ok and creates a stray session; no `session/archive`/
`session/delete`/`workspace/list`. Workarounds recorded in
`docs/bug-report-protocol.md`.

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
