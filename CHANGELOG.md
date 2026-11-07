# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/): the Python package
(`agent-control-tower`) and the DSH bundle (`dsh-tower`) share one version.

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
