"""Work-discipline principle cards, distilled from Lauren Tan's pstack.

Source: https://github.com/cursor/plugins/tree/main/pstack (MIT, © Lauren Tan),
Claude-port https://github.com/michael-denyer/pstack-claude. These cards are
rewritten for tower bots — write scopes, the report contract, the Attention
queue — not copied. The mapping lives in docs/pstack-discipline.md.

Bots get the INDEX (name + when it applies) injected into every dispatch; a full
card is one `tower principles show <name>` away. Keeping the prompt small is
itself one of the principles.
"""

from __future__ import annotations

PRINCIPLES: dict[str, dict[str, str]] = {
    "understand-first": {
        "applies": "every task, before any work",
        "card": (
            "Restate the goal in your own words first (the restatement gate exists "
            "for this). If the evidence rules out what was asked, show it and stop — "
            "never build the thing the premise rules out."
        ),
    },
    "smallest-change": {
        "applies": "sizing any diff, or when tempted to add abstractions",
        "card": (
            "Bias to deletion. The smallest change that solves the problem wins. "
            "No drive-by refactors, no speculative layers, no signal threading a "
            "caller never asked for."
        ),
    },
    "fix-root-causes": {
        "applies": "debugging anything",
        "card": (
            "Reproduce first; fix the fault where it entered the system, not where "
            "the symptom surfaced. A guard is a fix only when the state it handles "
            "is legitimate. Find every place with the same fault."
        ),
    },
    "prove-it-works": {
        "applies": "before you report done",
        "card": (
            "Verify against the real artifact — run it, read the actual value, quote "
            "the assertion's diff. 'It compiles' and truncated output prove nothing. "
            "Write the check as a command a reviewer can re-run; that command goes "
            "in your report's Checks."
        ),
    },
    "test-behavior": {
        "applies": "writing or changing any test",
        "card": (
            "Assert what users can observe, so the test fails when behavior breaks "
            "and stays green when internals change."
        ),
    },
    "subtract-before-add": {
        "applies": "adding a feature, or before a refactor",
        "card": (
            "Remove dead weight first, then build on the simpler base. Name what "
            "you deleted in the report."
        ),
    },
    "attack-the-premise": {
        "applies": "the second fix for the same symptom",
        "card": (
            "Two failed fixes sharing one premise mean the premise is broken. Write "
            "the premise down and test it directly before fixing again."
        ),
    },
    "model-the-domain": {
        "applies": "stateful or heavily-branching logic",
        "card": (
            "Encode the domain in a structure (state machine, table, reducer, the "
            "right collection) instead of scattered conditionals."
        ),
    },
    "boundary-discipline": {
        "applies": "validation, error handling, adapters",
        "card": (
            "Guards live at system boundaries; internal types are trusted; business "
            "logic stays pure."
        ),
    },
    "idempotent-operations": {
        "applies": "commands, lifecycle steps, retries",
        "card": (
            "Running twice converges to the same end state. Retries and re-runs are "
            "normal here — design for them."
        ),
    },
    "never-block-on-human": {
        "applies": "when you want to ask 'should I?' on reversible work",
        "card": (
            "Proceed with the sensible default, present the result, and record the "
            "decision. A person is pinged only via `## Attention` for irreversible "
            "or genuine product calls — that is the tower's rule too."
        ),
    },
    "show-your-work": {
        "applies": "multi-step work and every report",
        "card": (
            "Keep the decision trail: what you tried, what failed, what you chose "
            "and why — in the report's Summary and Open issues. The ledger is "
            "auditable; your report should be too."
        ),
    },
    "minimize-reader-load": {
        "applies": "shaping code and reports others will read",
        "card": (
            "Collapse one-caller wrappers, shrink mutable scope, cut prose that "
            "only restates the code. The reviewer's time is the scarce resource."
        ),
    },
    "explain-the-number": {
        "applies": "reporting any measurement",
        "card": (
            "Before reporting a number (time, count, score), say what produced it "
            "and what limits it. A number without its source is decoration."
        ),
    },
}

ORDER = [
    "understand-first", "smallest-change", "fix-root-causes", "prove-it-works",
    "test-behavior", "subtract-before-add", "attack-the-premise", "model-the-domain",
    "boundary-discipline", "idempotent-operations", "never-block-on-human",
    "show-your-work", "minimize-reader-load", "explain-the-number",
]


def index_lines() -> list[str]:
    """One line per principle: the compact form injected into dispatch prompts."""
    return [f"- {name} — applies when {PRINCIPLES[name]['applies']}." for name in ORDER]


def show(name: str) -> str:
    card = PRINCIPLES.get(name)
    if card is None:
        raise KeyError(f"unknown principle {name!r}; known: {', '.join(ORDER)}")
    return f"# {name}\nApplies when {card['applies']}.\n\n{card['card']}"


def scaffold_section() -> str:
    """The Work-discipline section for the role scaffold + the compact index."""
    rules = [
        "Restate the goal before working (the gate handles this); work only inside",
        "your write scopes; make the smallest change that solves it; fix root",
        "causes, not symptoms; prove it works on the real artifact and put the",
        "re-runnable command in Checks; never block on a human — defaults plus",
        "`## Attention` instead; keep the decision trail in Summary and Open issues.",
    ]
    lines = ["", "## Work discipline (pstack-derived; full card: `tower principles <name>`)", ""]
    lines += ["- " + rule for rule in rules]
    lines.append("")
    lines.append("Principles (read one with `tower principles <name>`):")
    lines += index_lines()
    return "\n".join(lines)
