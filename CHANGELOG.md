# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/): the Python package
(`agent-control-tower`) and the DSH bundle (`dsh-tower`) share one version.

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
