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

The tools shell out to the Python CLI, so it stays the single implementation. **No runtime
dependencies.**

## Install

```sh
# from npm (once published)
dsh plugin --profile <name> add dsh-tower

# from this checkout
dsh plugin --profile <name> add ./packages/dsh-tower
```

The bundle row is inserted by [`cordis.patch.yml`](cordis.patch.yml) as `id: tower`.

Prerequisite: the Python package must be available in the workspace where DSH runs.

```sh
pip install agent-control-tower     # or: pip install -e /path/to/agent-control-tower
tower init .                        # once, in your project
```

## Configuration

All optional; set them in the profile's `cordis.patch.yml` as a later layer targeting `id: tower`:

```yaml
- insert:
    - id: tower
      name: dsh-tower
      config:
        workspace: /abs/path/to/project   # default: process.cwd()
        towerBin: tower                   # default: "tower"
        # python: python3                 # used with towerBin: "__python__" -> python -m control_tower
        timeoutMs: 1800000                # per-call timeout (30 min)
```

If `tower.toml` is not found in `workspace`, the tools fail with an actionable error instead of
inventing results.

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
