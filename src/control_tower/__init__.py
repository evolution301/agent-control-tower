"""Control Tower — a local orchestrator for role-based agent teams.

The tower is the only component that talks to every role. Roles never talk to
each other directly: they receive a task, work inside their own workspace
directory, and file a report. The tower records everything in an append-only
ledger so the lead can verify claims instead of trusting them.
"""

__version__ = "0.14.1"
