# -*- coding: utf-8 -*-
"""造一个真实感的演示工作区，用于面板截图与测试"""
import json
import os
import sys
from pathlib import Path

ROOT = r"C:\Users\Administrator\Desktop\agent-control-tower"
sys.path.insert(0, os.path.join(ROOT, "src"))

from control_tower.board import Board  # noqa: E402
from control_tower.config import ensure_layout, parse_config  # noqa: E402
from control_tower.dispatch import parse_report  # noqa: E402

work = os.path.join(ROOT, "docs", "assets", "demo-tower")
os.makedirs(work, exist_ok=True)

config = {
    "name": "product-team",
    "workspace": work,
    "roles": {
        "backend": {"description": "Data, APIs, deployment scripts.", "adapter": "dsh", "write_scopes": ["server/"]},
        "frontend": {"description": "Layout, components, browser verification.", "adapter": "dsh", "write_scopes": ["web/"]},
        "qa": {"description": "Independent verification.", "adapter": "dsh", "write_scopes": []},
        "docs": {"description": "Changelog and docs.", "adapter": "openai", "write_scopes": ["docs/"]},
    },
}
cfg_path = Path(work) / "tower.json"
cfg_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
cfg = parse_config(cfg_path)
ensure_layout(cfg)
board = Board(cfg)
for role in cfg.roles:
    os.makedirs(cfg.role_dir(role), exist_ok=True)

# T-0001: ready, waiting for review
t1 = board.create("backend", "Add pagination to /api/orders and cover it with tests.")
t1.status = "reported"
t1.state = "ready"
t1.report = parse_report("""## Status
ready

## Summary
Pagination is in: `?page` and `?page_size` (max 100), stable ordering by `created_at`.
27 new tests, all green. No schema change.

## Files
- server/api/orders.py — pagination params + query rewrite
- server/tests/test_orders_pagination.py — 27 tests

## Checks
- `pytest -q` → 214 passed
- `ruff check` → clean

## Open issues
- Cursor pagination is out of scope for this round.
""")
t1.assignments = [{"ts": "2026-11-04T09:12:00+0800", "adapter": "dsh", "exit_code": 0}]
t1.updated_at = "2026-11-04T09:41:00+0800"
board.save(t1)

# T-0002: blocked with a question
t2 = board.create("frontend", "Rework the mobile drawer: 390 breakpoint, focus trap, 44px targets.")
t2.status = "reported"
t2.state = "blocked"
t2.attention = {"question": "The drawer currently overlays the sticky CTA. Should it push content down instead?"}
t2.report = {"state": "blocked", "summary": "Drawer rebuilt; blocked on one layout decision.", "files": [], "checks": [], "attention": t2.attention}
t2.assignments = [{"ts": "2026-11-04T10:02:00+0800", "adapter": "dsh", "exit_code": 0}]
t2.updated_at = "2026-11-04T10:26:00+0800"
board.save(t2)

# T-0003: progress, in flight
t3 = board.create("qa", "Reproduce the four accepted UI batches at 1440 and 390; file defects with evidence.")
t3.status = "in_progress"
t3.state = "progress"
t3.assignments = [{"ts": "2026-11-04T11:05:00+0800", "adapter": "dsh", "exit_code": 0}]
t3.updated_at = "2026-11-04T11:18:00+0800"
board.save(t3)

# T-0004: waiting
t4 = board.create("docs", "Update the changelog for v0.2 (structured statuses, attention queue).")
t4.status = "reported"
t4.state = "waiting"
t4.updated_at = "2026-11-04T08:55:00+0800"
board.save(t4)

# T-0005: failed with question
t5 = board.create("backend", "Backfill the orders index for the new pagination ordering.")
t5.status = "reported"
t5.state = "failed"
t5.attention = {"question": "The backfill needs 40 minutes of write lock. Do you want a maintenance window or a background migration?"}
t5.report = {"state": "failed", "summary": "Dry run aborted: lock timeout at 5 minutes.", "files": [], "checks": [], "attention": t5.attention}
t5.assignments = [{"ts": "2026-11-04T11:31:00+0800", "adapter": "dsh", "exit_code": 1}]
t5.updated_at = "2026-11-04T11:52:00+0800"
board.save(t5)

for ev in [
    ("dispatch", "T-0001", "backend", "dispatched to backend"),
    ("task_status", "T-0001", "backend", "report filed"),
    ("dispatch", "T-0002", "frontend", "dispatched to frontend"),
    ("task_status", "T-0002", "frontend", "report filed"),
    ("dispatch", "T-0003", "qa", "dispatched to qa"),
    ("assignment", "T-0003", "qa", "adapter=dsh exit=0"),
    ("dispatch", "T-0005", "backend", "dispatched to backend"),
    ("task_status", "T-0005", "backend", "report filed"),
]:
    board.log(ev[0], task=ev[1], role=ev[2], note=ev[3])

print("demo tower ready:", work)
