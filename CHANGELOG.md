# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/): the Python package
(`agent-control-tower`) and the DSH bundle (`dsh-tower`) share one version.

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
