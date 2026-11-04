# -*- coding: utf-8 -*-
"""生成 README 资产：
1. 真实 CLI 输出（tower demo / status / inbox / dispatch）
2. 终端样式 HTML 卡片 -> bsk 截图
3. 架构图 SVG -> bsk 截图
"""
import os
import subprocess
import sys
import tempfile
import textwrap
import json

ROOT = r"C:\Users\Administrator\Desktop\agent-control-tower"
ASSETS = os.path.join(ROOT, "docs", "assets")
GEN = os.path.join(ASSETS, "generated")
os.makedirs(GEN, exist_ok=True)
PY = sys.executable
env = dict(os.environ)
env["PYTHONPATH"] = os.path.join(ROOT, "src")


def run(args, cwd, timeout=180):
    p = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    return (p.stdout or "") + (p.stderr or "")


# ---------------------------------------------------------------- real output
demo_dir = tempfile.mkdtemp(prefix="tower-demo-")
print("demo dir:", demo_dir)

# 1. demo
demo_out = run([PY, "-m", "control_tower", "demo"], cwd=demo_dir)
open(os.path.join(GEN, "01-demo.txt"), "w", encoding="utf-8").write(demo_out)

# 2. 建一个真实工作区，做 dispatch / status / inbox
work = tempfile.mkdtemp(prefix="tower-work-")
init_out = run([PY, "-m", "control_tower", "init", work], cwd=work)
dispatch_out = run([PY, "-m", "control_tower", "dispatch", "backend",
                    "Add pagination to /api/orders and cover it with tests."], cwd=work)
status_out = run([PY, "-m", "control_tower", "status"], cwd=work)
inbox_out = run([PY, "-m", "control_tower", "inbox"], cwd=work)
accept_out = run([PY, "-m", "control_tower", "accept", "T-0001"], cwd=work)
ledger_out = run([PY, "-m", "control_tower", "log", "--tail", "6"], cwd=work)

for name, text in [
    ("02-init.txt", init_out),
    ("03-dispatch.txt", dispatch_out),
    ("04-status.txt", status_out),
    ("05-inbox.txt", inbox_out),
    ("06-accept.txt", accept_out),
    ("07-ledger.txt", ledger_out),
]:
    open(os.path.join(GEN, name), "w", encoding="utf-8").write(text)
    print(f"  {name}: {len(text)} chars")

# ---------------------------------------------------------------- terminal html
TERMINAL = """<!doctype html><html><head><meta charset="utf-8"><style>
  :root {{ --bg:#0f1115; --panel:#151a21; --fg:#e6e6e6; --dim:#8b949e; --accent:#f97316; --ok:#3fb950; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:28px; background:var(--bg);
         font-family:"Geist Mono","JetBrains Mono",ui-monospace,Menlo,Consolas,monospace; }}
  .win {{ width:1080px; border:1px solid #232a33; border-radius:12px; overflow:hidden; background:var(--panel);
          box-shadow:0 24px 60px -30px #000; }}
  .bar {{ display:flex; align-items:center; gap:8px; padding:12px 16px; background:#10151b; border-bottom:1px solid #232a33; }}
  .dot {{ width:11px; height:11px; border-radius:50%; }}
  .title {{ margin-left:10px; color:var(--dim); font-size:13px; }}
  pre {{ margin:0; padding:20px 22px; color:var(--fg); font-size:14px; line-height:1.55; white-space:pre-wrap; }}
  .prompt {{ color:var(--accent); }}
  .ok {{ color:var(--ok); }}
</style></head><body>
  <div class="win">
    <div class="bar">
      <span class="dot" style="background:#ff5f56"></span>
      <span class="dot" style="background:#ffbd2e"></span>
      <span class="dot" style="background:#27c93f"></span>
      <span class="title">{title}</span>
    </div>
    <pre>{body}</pre>
  </div>
</body></html>"""


def html_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_terminal(name, title, command, output, accent_result=None):
    body = f'<span class="prompt">{html_escape(command)}</span>\n{html_escape(output.strip())}'
    if accent_result:
        body += f'\n<span class="ok">{html_escape(accent_result)}</span>'
    path = os.path.join(GEN, f"{name}.html")
    open(path, "w", encoding="utf-8").write(TERMINAL.format(title=html_escape(title), body=body))
    return path


frames = []
frames.append(write_terminal(
    "frame-1", "tower demo  —  the whole loop, offline",
    "$ tower demo",
    demo_out.strip(),
))
frames.append(write_terminal(
    "frame-2", "tower dispatch  —  one brief in, one verified report out",
    "$ tower dispatch backend \"Add pagination to /api/orders and cover it with tests.\"",
    "\n".join(dispatch_out.strip().splitlines()[:16]),
))
frames.append(write_terminal(
    "frame-3", "tower accept  —  evidence checked before anything is accepted",
    "$ tower accept T-0001",
    accept_out.strip(),
))
frames.append(write_terminal(
    "frame-4", "tower status + log  —  the audit trail is just files",
    "$ tower status && tower log --tail 3",
    status_out.strip() + "\n\n" + "\n".join(ledger_out.strip().splitlines()[:3]),
))
json.dump(frames, open(os.path.join(GEN, "frames.json"), "w", encoding="utf-8"), indent=2)

# ---------------------------------------------------------------- architecture svg
ARCH = """<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="680" viewBox="0 0 1200 680">
  <defs>
    <style>
      .bg { fill:#f7f5f2; }
      .box { fill:#ffffff; stroke:#d9d4cd; stroke-width:2; }
      .box-dark { fill:#111418; }
      .t  { font-family:"Geist","Helvetica Neue",Arial,sans-serif; fill:#111418; }
      .tw { font-family:"Geist","Helvetica Neue",Arial,sans-serif; fill:#ffffff; }
      .m  { font-family:"Geist Mono",ui-monospace,Consolas,monospace; fill:#7a736a; font-size:15px; }
      .mw { font-family:"Geist Mono",ui-monospace,Consolas,monospace; fill:#a8a29a; font-size:14px; }
      .accent { fill:#f97316; }
      .line { stroke:#f97316; stroke-width:2.5; fill:none; }
      .line-dash { stroke:#c9c3ba; stroke-width:2; fill:none; stroke-dasharray:6 7; }
    </style>
  </defs>
  <rect class="bg" width="1200" height="680"/>

  <text class="m" x="60" y="70">01 / YOU</text>
  <rect class="box" x="60" y="90" width="300" height="92" rx="12"/>
  <text class="t" x="84" y="132" font-size="24" font-weight="600">One message</text>
  <text class="m" x="84" y="160">"build the thing"</text>

  <text class="m" x="470" y="70">02 / LEAD DISPATCHES</text>
  <rect class="box" x="470" y="90" width="300" height="92" rx="12"/>
  <text class="t" x="494" y="132" font-size="24" font-weight="600">tower dispatch</text>
  <text class="m" x="494" y="160">role · brief · write scope</text>

  <text class="m" x="880" y="70">03 / ROLES WORK</text>
  <rect class="box" x="880" y="90" width="260" height="92" rx="12"/>
  <text class="t" x="904" y="126" font-size="20" font-weight="600">backend · frontend</text>
  <text class="t" x="904" y="152" font-size="20" font-weight="600">qa · docs</text>

  <path class="line" d="M360 136 H 458" marker-end="url(#a)"/>
  <path class="line" d="M770 136 H 868"/>
  <circle cx="818" cy="136" r="4" class="accent"/>

  <path class="line-dash" d="M1010 182 V 300"/>
  <path class="line-dash" d="M620 182 V 300"/>
  <rect class="box" x="470" y="300" width="670" height="120" rx="14"/>
  <text class="t" x="494" y="340" font-size="22" font-weight="600">REPORT — the only accepted shape</text>
  <text class="m" x="494" y="370">## Summary    ## Files    ## Checks → result    ## Open issues</text>

  <path class="line-dash" d="M470 360 H 360 V 182"/>
  <circle cx="410" cy="360" r="4" class="accent"/>

  <rect class="box-dark" x="60" y="470" width="1080" height="150" rx="14"/>
  <text class="tw" x="88" y="508" font-size="22" font-weight="600">VERIFY, THEN ACCEPT</text>
  <text class="mw" x="88" y="540">files must exist   ·   every check must carry its result   ·   verify_commands run for real</text>
  <text class="mw" x="88" y="566">accept &amp; ledger.jsonl  →  or rework with reasons  (round + 1, capped by max_rounds)</text>
  <text class="mw" x="88" y="596">state is files: tasks/T-0001.json   .tower/ledger.jsonl   .tower/inbox.jsonl</text>

  <path class="line" d="M620 420 V 458"/>
  <text class="t" x="640" y="446" font-size="16">evidence, not vibes</text>
</svg>"""

open(os.path.join(ASSETS, "architecture.svg"), "w", encoding="utf-8").write(ARCH)
open(os.path.join(GEN, "architecture.html"), "w", encoding="utf-8").write(
    "<!doctype html><html><body style='margin:0'>" + ARCH.replace('xmlns="http://www.w3.org/2000/svg"', "") + "</body></html>"
)
print("assets written to", ASSETS)
