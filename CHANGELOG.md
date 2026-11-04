# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/): the Python package
(`agent-control-tower`) and the DSH bundle (`dsh-tower`) share one version.

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
