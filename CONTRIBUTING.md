# Contributing to agent-control-tower

Thanks for wanting to help. This project has one rule above all others:

> **Evidence over claims.** Any change that makes the tower accept work on the strength of an agent's
> own summary is a regression, even if it looks convenient.

## Ways to contribute

- **Bug reports** — especially adapters that hang, mis-parse reports, or lose ledger events.
- **New adapters** — Cursor, Aider, OpenHands, Gemini CLI, your in-house runner. See below.
- **Docs** — the DSH quickstart can always be clearer; screenshots and GIFs welcome.
- **Tests** — anything that runs offline is gold.

## Development setup

```bash
git clone https://github.com/<you>/agent-control-tower.git
cd agent-control-tower
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q            # must stay green
tower demo           # end-to-end offline demo, must print verify: PASS
```

Python 3.10+. Runtime dependency: `PyYAML` only. Please keep it that way; the tower should stay
installable on a potato.

## Ground rules

1. **No secrets in the repo.** API keys and DSH session ids go in `tower.toml` (gitignored) or env
   vars. Never hardcode them, never log them.
2. **Adapters stay stateless.** They call an agent and return text; they do not touch task state.
3. **Reports are parsed, not guessed.** If you change the contract, update
   `dispatch.parse_report()` and its tests in the same PR.
4. **`verify` must not be bypassable by config alone.** A task can only become `accepted` through
   `verify_report()` returning ok.
5. **One verb per command.** The CLI stays boring on purpose.

## Pull request checklist

- [ ] `pytest -q` passes (add a test for the behaviour you changed)
- [ ] `tower demo` prints `verify: PASS`
- [ ] `tower doctor` is clean on the example configs in `examples/`
- [ ] README / docs updated if the behaviour or config format changed
- [ ] No tokens, session ids, or absolute machine paths committed

## Writing an adapter

1. Create `src/control_tower/adapters/<name>.py` implementing `run(AdapterRequest) -> AdapterResult`.
2. Register it in `adapters/base.py::get_adapter`.
3. Add a `doctor` check if it needs credentials or a session id (`cli.py::cmd_doctor`).
4. Document it in `docs/adapters.md` with a config snippet.
5. Add a test using a fake transport (no network in tests).

Adapters that can hang must respect `timeout_s` and return `exit_code=124` on timeout. A hung
adapter is worse than a failed one.

## Writing docs

- Show the command, then the expected output.
- Prefer real transcripts over invented ones.
- If something is not implemented yet, say so and put it in the roadmap instead of implying it works.

## Commit style

Conventional-ish, imperative, scoped:

```
feat(dsh): read report file when session has no final-answer RPC
fix(verify): reject checks without a result
docs(readme): clarify DSH-first setup
```

## Reporting a security issue

Open a private security advisory on GitHub rather than a public issue. The tower runs local agent
processes with whatever permissions you give them — treat `roles/*/AGENT.md` and adapter commands as
part of your security surface.

## License

By contributing you agree your work is released under the [MIT License](LICENSE).
