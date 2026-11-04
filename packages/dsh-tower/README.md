# dsh-tower

DeepSeek Harness bundle for [agent-control-tower](../../README.md): drive a control tower from
inside a DSH session.

Registers five tools on the harness tool registry:

| Tool | What it does |
|---|---|
| `tower_dispatch` | dispatch a brief to a role, return that role's report (`summary` / `files` / `checks` / `open issues`) and the task id |
| `tower_inbox` | list the reports roles have filed, optionally mark them read |
| `tower_accept` | verify a reported task (files exist, checks carry results, `verify_commands` pass) and accept it — or get the rejection reasons |
| `tower_rework` | send a task back with feedback and re-dispatch it (round + 1) |
| `tower_status` | list the task board |
| `tower_roles` | list the roles (bots) with their write scope, round budget and wired DSH session id |
| `tower_role_add` | create a role (bot): appends `tower.toml`, writes `roles/<name>/AGENT.md`, wires the session id |
| `tower_role_remove` | unregister a role (bot), optionally deleting its charter directory |

The tools shell out to the Python CLI, so it stays the single implementation. **No runtime
dependencies.**

## Install

### Prerequisites

- **DSH 0.2.1-rc.5 or newer**, desktop app or CLI. The bundle declares `engines.dsh`, so an
  incompatible harness now refuses it with an explicit message instead of failing at mount time.
- **The Python package on the `PATH` of the process that runs DSH.** Every tool shells out to
  `tower`; installing it into a virtualenv you never activate for DSH makes every call fail with
  `spawn tower ENOENT`.

  ```sh
  python -m pip install agent-control-tower     # or: pip install -e /path/to/agent-control-tower
  tower init .                                  # once, in your project
  ```

### DSH desktop app

The desktop app owns its profile: `dsh plugin --profile desktop ...` refuses with
`profile "desktop" is managed exclusively by the Electron application`. Two working routes:

1. **Plugins page (preferred).** Install the bundle from this checkout through the app's plugin
   manager, then restart the app.
2. **Hand-install into `~/.dsh/profiles/desktop`**:
   - copy the package to `~/.dsh/profiles/desktop/node_modules/dsh-tower` as a **real directory**
     (the loader resolves bundles through the profile's `node_modules`; do not leave a symlink),
   - add `"dsh-tower"` to `dsh.profile.bundles` in the profile's `package.json`, plus a
     `"dsh-tower": "file:..."` entry under `dependencies` so a later `pnpm install` keeps resolving
     the local checkout,
   - restart the app and check the tool list for the five `tower_*` tools.

### DSH CLI profiles

```sh
dsh plugin --profile <name> add ./packages/dsh-tower
```

The bundle row is inserted by [`cordis.patch.yml`](cordis.patch.yml) as `id: tower`.

### If the tools never appear

A bundle that throws inside `apply()` — for example a tool schema the harness rejects — fails to
mount **silently**: no tools, no dialog. Boot the profile from a terminal and read stderr, which is
where the harness prints `tower (dsh-tower): ...`:

```sh
deepseek-harness/resources/runtime/cli/bin/dsh.cmd --profile <profile>
```

To inspect one profile in isolation, copy its `package.json` + `cordis.patch.yml` into a scratch
`DSH_HOME`, boot it there and keep the terminal output.

## Configuration

All optional; set them in the profile's `cordis.patch.yml` as a later layer targeting `id: tower`:

```yaml
- id: tower
  config:
    workspace: C:/abs/path/to/project   # default: process.cwd()
    towerBin: tower                     # default: "tower"
    # python: python3                   # with towerBin: "__python__" -> python -m control_tower
    timeoutMs: 1800000                  # per-call timeout (30 min)
```

The desktop app hot-reloads `cordis.patch.yml`, so configuration changes apply without a restart;
changes to the bundle list itself (or to this package's code) need one.

If no `tower.toml` / `tower.yaml` / `tower.json` is found in `workspace`, the tools fail with an
actionable error instead of inventing results.

## Example: a lease-style loop from a DSH session

1. `tower_dispatch { role: "frontend", brief: "Rework the hero: two-column, image frame, proof band. Deliver before/after screenshots at 1440 and 390." }`
2. `tower_inbox {}` — see what came back.
3. `tower_accept { task: "T-0001" }` — verifies; if it fails you get the reasons.
4. `tower_rework { task: "T-0001", note: "Checks list no results; re-run the suite" }` — when it is not good enough.

The DSH session never has to paste briefs between windows, and "done" has to survive verification.

## Why a bundle and not a skill

The value here is *state*, not prompts: tasks, reports, rounds and a verifier. Skins/skills cannot
hold that between sessions; a bundle can, because the tools below it are plain files.

## License

MIT
