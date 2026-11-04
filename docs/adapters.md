# Adapters

An adapter turns "a role should do this task" into "some agent runtime actually did something".
Everything else in the tower is runtime-agnostic.

## Contract

```python
@dataclass
class AdapterRequest:
    role: str          # "backend"
    prompt: str        # fully rendered prompt (contains the report contract)
    cwd: Path          # the role's own workspace — its only write area
    timeout_s: int
    env: dict | None
    task_id: str       # "T-0001"

@dataclass
class AdapterResult:
    text: str          # the role's report text (markdown with the four sections)
    exit_code: int     # 0 = produced a report; non-zero = failed task
    meta: dict | None  # anything worth recording next to the task
```

An adapter may write files, spawn processes and call APIs. It must **not** mutate task state — that
is the tower's job. Register a new one in `adapters/base.py::get_adapter`.

## `mock`

```yaml
roles:
  backend:
    adapter: mock
```

Writes `<role>/<task>-MOCK-ARTIFACT.md` and returns a well-formed report. Deterministic, no network.
Used by `tower demo` and the whole test suite, so the core loop is testable in CI.

## `cli`

For agent binaries that take a prompt and edit files (Claude Code, Codex CLI, custom wrappers).

```yaml
adapters:
  cli:
    command: ["claude", "-p", "{prompt_file}", "--permission-mode", "acceptEdits"]
    # command: ["codex", "exec", "--cd", "{cwd}", "--full-auto"]
    # command: ["./scripts/my-agent.sh", "{prompt_file}"]
    shell: false
```

Placeholders: `{prompt}`, `{prompt_file}`, `{cwd}`, `{role}`, `{task_id}`.
The prompt is also written to `<cwd>/.tower-prompt.md` and exported as `TOWER_PROMPT_FILE`.

The tower expects the process to leave `<cwd>/<task_id>-REPORT.md` behind (or print the report as its
final message — the CLI adapter returns stdout, and the dispatcher parses whichever it gets).

## `dsh` (DeepSeek Harness)

Drives a live harness session over its local RPC endpoint — the setup used by the reference project,
where roles are long-lived sessions with browser and shell tools.

```yaml
adapters:
  dsh:
    base_url: "http://127.0.0.1:65163"
    sessions:
      backend: "session-1492244d-..."
      frontend: "session-acc99990-..."
    wait_s: 900
    poll_s: 5
```

Flow: `session/prompt` (queued, so it lands at a step boundary) → poll `session/list` until the
session is idle → read `<cwd>/<task_id>-REPORT.md`. Because DSH does not expose a "final answer"
RPC in this version, the file is the contract.

If the session id is missing, the adapter fails fast with `missing_session` instead of hanging.

## `openai`

Any OpenAI-compatible `/chat/completions` endpoint: OpenAI, DeepSeek, Groq, vLLM, Ollama.

```yaml
adapters:
  openai:
    base_url: "https://api.openai.com/v1"
    model: "gpt-4.1-mini"
    api_key_env: "OPENAI_API_KEY"
    max_steps: 12
```

A deliberately minimal agent loop: the model gets one tool, `run_shell`, executed inside the role
workspace; after `max_steps` it must answer. Good enough for bounded jobs (write a file, run a test,
summarise), and honest about its limits — this is not a full coding agent.

## Choosing one

| Situation | Adapter |
|---|---|
| CI / tests / demos | `mock` |
| You already run `claude`/`codex` in a terminal | `cli` |
| Long-lived sessions with browser + shell tools | `dsh` |
| Hosted model, no local agent binary | `openai` |

## Writing a new adapter

```python
from .base import AdapterRequest, AdapterResult

class MyAdapter:
    name = "myagent"

    def run(self, req: AdapterRequest) -> AdapterResult:
        report = call_my_agent(req.prompt, cwd=req.cwd, timeout=req.timeout_s)
        return AdapterResult(text=report, exit_code=0, meta={"backend": "myagent"})
```

Then add `if name == "myagent": return MyAdapter(**opts)` in `get_adapter`, plus a `doctor` check if
the adapter needs credentials.
