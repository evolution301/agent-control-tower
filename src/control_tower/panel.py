"""Local web panel: `tower panel`.

One stdlib HTTP server, one embedded page, one JSON endpoint. No build step, no
dependencies, no telemetry — it binds 127.0.0.1 and reads the same files the CLI
reads.

Layout follows the attention principle: **needs-you first**, then who is doing
what, then the ledger. A healthy tower shows almost nothing above the fold.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from .board import Board
from .config import TowerConfig
from .snapshot import build_snapshot

PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__NAME__ — control tower</title>
<style>
  :root { --bg:#f7f5f2; --ink:#111418; --dim:#7a736a; --line:#e2ddd6; --card:#fff;
          --ember:#f97316; --ok:#15803d; --warn:#b45309; --bad:#b91c1c; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font-family:"Geist","Inter",-apple-system,"Segoe UI",Roboto,Arial,sans-serif; }
  .wrap { max-width:1180px; margin:0 auto; padding:32px 28px 72px; }
  header { display:flex; align-items:baseline; gap:16px; padding-bottom:16px; border-bottom:2px solid var(--ink); }
  header h1 { font-size:26px; margin:0; letter-spacing:-.02em; }
  header .ws { color:var(--dim); font-size:13px; font-family:ui-monospace,Consolas,monospace; }
  header .clock { margin-left:auto; color:var(--dim); font-size:12px; font-family:ui-monospace,Consolas,monospace; }
  .stats { display:flex; gap:28px; padding:18px 0 8px; border-bottom:1px solid var(--line); }
  .stat b { display:block; font-size:24px; letter-spacing:-.02em; }
  .stat span { font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--dim); }
  section { margin-top:34px; }
  h2 { font-size:13px; letter-spacing:.14em; text-transform:uppercase; color:var(--dim); margin:0 0 12px; font-weight:600; }
  h2 .n { color:var(--ember); margin-right:10px; }
  .empty { color:var(--dim); font-size:14px; padding:14px 0; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px 18px; margin-bottom:10px; }
  .card.needs { border-left:3px solid var(--ember); }
  .row { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
  .id { font-family:ui-monospace,Consolas,monospace; font-size:12px; color:var(--dim); }
  .role { font-size:13px; font-weight:600; }
  .state { font-size:11px; letter-spacing:.1em; text-transform:uppercase; padding:3px 8px; border-radius:999px;
           border:1px solid var(--line); color:var(--dim); }
  .state.ready { color:var(--ok); border-color:#bbf7d0; background:#f0fdf4; }
  .state.blocked, .state.failed { color:var(--bad); border-color:#fecaca; background:#fef2f2; }
  .state.progress { color:var(--warn); border-color:#fde68a; background:#fffbeb; }
  .brief { font-size:14px; margin-top:8px; line-height:1.5; }
  .q { margin-top:10px; font-size:14px; background:#fff7ed; border:1px solid #fed7aa; color:#9a3412;
       padding:10px 12px; border-radius:8px; }
  table { width:100%; border-collapse:collapse; font-size:14px; }
  th { text-align:left; font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--dim);
       font-weight:600; padding:0 10px 8px 0; border-bottom:1px solid var(--line); }
  td { padding:10px 10px 10px 0; border-bottom:1px solid var(--line); vertical-align:top; }
  td.age { color:var(--dim); font-family:ui-monospace,Consolas,monospace; font-size:12px; white-space:nowrap; }
  .dot { width:8px; height:8px; border-radius:50%; display:inline-block; margin-right:7px; }
  .dot.working { background:var(--ember); }
  .dot.waiting { background:var(--warn); }
  .dot.idle { background:#c7c1b8; }
  .log { font-family:ui-monospace,Consolas,monospace; font-size:12px; color:#3f3a34; }
  .log .ev { color:var(--ember); }
  .log .t { color:#a8a29a; }
  .kpis { display:flex; gap:26px; flex-wrap:wrap; padding:6px 0 16px; }
  .kpi b { display:block; font-size:22px; letter-spacing:-.02em; }
  .kpi span { font-size:11px; letter-spacing:.1em; text-transform:uppercase; color:var(--dim); }
  .bars { margin-top:14px; }
  .bar { display:grid; grid-template-columns:52px 1fr; gap:10px; align-items:center; margin:3px 0; }
  .bar .tick { color:#a8a29a; }
  .track { position:relative; height:14px; }
  .track i { position:absolute; top:0; height:6px; border-radius:3px; display:block; }
  .track i.c { background:#c7c1b8; }
  .track i.a { top:8px; background:var(--ember); }
  footer { margin-top:40px; color:var(--dim); font-size:12px; }
</style></head>
<body><div class="wrap">
  <header>
    <h1>__NAME__</h1>
    <span class="ws">__WS__</span>
    <span class="clock" id="clock">—</span>
  </header>
  <div class="stats" id="stats"></div>

  <section>
    <h2><span class="n">01</span>Needs you</h2>
    <div id="attention"><div class="empty">loading…</div></div>
  </section>

  <section>
    <h2><span class="n">02</span>In flight</h2>
    <div id="flight"></div>
  </section>

  <section>
    <h2><span class="n">03</span>Roles — who is doing what</h2>
    <table><thead><tr><th>Role</th><th>State</th><th>Current task</th><th>Since</th></tr></thead>
    <tbody id="roles"></tbody></table>
  </section>

  <section>
    <h2><span class="n">04</span>Business — is this team working well?</h2>
    <div class="kpis" id="kpis"></div>
    <table><thead><tr><th>Role</th><th>Tasks</th><th>Accepted</th><th>Open</th><th>Late</th><th>Skills</th><th>Avg lead</th></tr></thead>
    <tbody id="bi-roles"></tbody></table>
    <div class="log bars" id="bars"></div>
  </section>

  <section>
    <h2><span class="n">05</span>Ledger</h2>
    <div class="log" id="log"></div>
  </section>

  <footer>Read-only view of <code>.tower/</code> · auto-refresh every 2s · <span id="gen"></span></footer>
</div>
<script>
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const stateClass = (s) => (["ready","blocked","failed","progress"].includes(s) ? s : "");

function card(t) {
  return `<div class="card needs">
    <div class="row"><span class="id">${esc(t.id)}</span>
      <span class="role">${esc(t.role)}</span>
      <span class="state ${stateClass(t.state)}">${esc(t.state)}</span>
      <span class="id">${esc(t.age)} ago · round ${esc(t.round)}/${esc(t.max_rounds)}</span></div>
    <div class="brief">${esc(t.brief)}</div>
    ${t.question ? `<div class="q">→ ${esc(t.question)}</div>` : ""}
  </div>`;
}

function row(t) {
  return `<tr><td><span class="id">${esc(t.id)}</span></td>
    <td><b>${esc(t.role)}</b></td>
    <td>${esc(t.state)}</td>
    <td class="brief">${esc(t.brief)}</td>
    <td class="age">${esc(t.age)}</td></tr>`;
}

const pct = (v) => (v === null || v === undefined) ? "—" : Math.round(v * 100) + "%";
const dur = (s) => {
  if (s === null || s === undefined) return "—";
  s = Math.round(s);
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m";
  if (s < 86400) return Math.floor(s / 3600) + "h";
  return Math.floor(s / 86400) + "d";
};

async function bi() {
  try {
    const r = await fetch("/api/stats", {cache: "no-store"});
    const s = await r.json();
    const t = s.totals || {}, rates = s.rates || {}, lead = s.lead_time || {};
    const collab = s.collaboration || {}, sup = s.supervision || {};
    const skills = Object.values(s.roles || {}).reduce((n, r) => n + (r.skills || 0), 0);
    document.getElementById("kpis").innerHTML = [
      ["verified", pct(rates.verified)],
      ["lead p50", dur(lead.p50_s)],
      ["lead p90", dur(lead.p90_s)],
      ["tasks", t.tasks ?? 0],
      ["accepted", t.accepted ?? 0],
      ["overdue", sup.overdue_now ?? 0],
      ["nudges", sup.nudges ?? 0],
      ["messages", collab.messages ?? 0],
      ["huddles", collab.huddles ?? 0],
      ["skills", skills],
    ].map(([label, value]) => `<div class="kpi"><b>${esc(value)}</b><span>${esc(label)}</span></div>`).join("");

    document.getElementById("bi-roles").innerHTML = Object.entries(s.roles || {}).map(([name, r]) => `
      <tr><td><b>${esc(name)}</b></td><td>${r.tasks}</td><td>${r.accepted}</td>
        <td>${r.open}</td><td>${r.overdue}</td><td>${r.skills}</td>
        <td class="age">${esc(dur(r.avg_lead_s))}</td></tr>`).join("");

    const days = (s.throughput || []).slice(-14);
    const peak = Math.max(1, ...days.map(d => Math.max(d.created, d.accepted)));
    document.getElementById("bars").innerHTML = days.map(d => `
      <div class="bar"><span class="tick">${esc(d.date.slice(5))}</span>
        <span class="track">
          <i class="c" style="width:${Math.round(100 * d.created / peak)}%"></i>
          <i class="a" style="width:${Math.round(100 * d.accepted / peak)}%"></i>
        </span></div>`).join("") || '<div class="empty">no work in the last 14 days</div>';
  } catch (err) {
    document.getElementById("kpis").textContent = "stats unavailable";
  }
}

async function tick() {
  try {
    const r = await fetch("/api/snapshot", {cache: "no-store"});
    const s = await r.json();
    document.getElementById("clock").textContent = s.generated_at;
    document.getElementById("gen").textContent = "generated " + s.generated_at;

    const c = s.counts || {};
    document.getElementById("stats").innerHTML = [
      ["needs you", c.attention ?? 0],
      ["in flight", c.in_flight ?? 0],
      ["tasks", c.total ?? 0],
      ["ready", c.ready ?? 0],
      ["working", c.progress ?? 0],
    ].map(([label, n]) => `<div class="stat"><b>${n}</b><span>${label}</span></div>`).join("");

    document.getElementById("attention").innerHTML =
      (s.attention && s.attention.length) ? s.attention.map(card).join("")
        : `<div class="empty">Nothing needs you. Every task is working, idle or accepted.</div>`;

    document.getElementById("flight").innerHTML =
      (s.in_flight && s.in_flight.length)
        ? `<table><thead><tr><th>Task</th><th>Role</th><th>State</th><th>Brief</th><th>Age</th></tr></thead>
           <tbody>${s.in_flight.map(row).join("")}</tbody></table>`
        : `<div class="empty">Nothing in flight.</div>`;

    document.getElementById("roles").innerHTML = (s.roles || []).map(r => `
      <tr><td><b>${esc(r.name)}</b><br><span class="id">${esc(r.adapter)}</span></td>
        <td><span class="dot ${esc(r.doing)}"></span>${esc(r.doing)}</td>
        <td>${r.task_id ? `<span class="id">${esc(r.task_id)}</span> ${esc(r.brief)}` : `<span class="id">—</span>`}</td>
        <td class="age">${esc(r.age)}</td></tr>`).join("");

    document.getElementById("log").innerHTML = (s.recent || []).map(e =>
      `<div><span class="t">${esc((e.ts || "").replace("T"," ").slice(0,19))}</span> ` +
      `<span class="ev">${esc(e.event)}</span> ${esc(e.task)} ${esc(e.role)} ${esc(e.note)}</div>`).join("");
  } catch (err) {
    document.getElementById("clock").textContent = "panel disconnected — is `tower panel` still running?";
  }
}
tick(); setInterval(tick, 2000);
bi(); setInterval(bi, 5000);
</script>
</body></html>
"""


ZH = {
    ">Needs you<": ">需要你处理<",
    ">In flight<": ">进行中<",
    ">Roles — who is doing what<": ">角色 — 谁在干什么<",
    ">Ledger<": ">操作日志<",
    ">Business — is this team working well?<": ">业务 — 这个团队干得好不好？<",
    "<th>Tasks</th>": "<th>任务</th>",
    "<th>Accepted</th>": "<th>已验收</th>",
    "<th>Open</th>": "<th>未完成</th>",
    "<th>Late</th>": "<th>逾期</th>",
    "<th>Skills</th>": "<th>技能</th>",
    "<th>Avg lead</th>": "<th>平均周期</th>",
    "no work in the last 14 days": "最近 14 天没有工作记录",
    "<th>Role</th>": "<th>角色</th>",
    "<th>State</th>": "<th>状态</th>",
    "<th>Current task</th>": "<th>当前任务</th>",
    "<th>Since</th>": "<th>开始于</th>",
    "<th>Task</th>": "<th>任务</th>",
    "<th>Brief</th>": "<th>任务描述</th>",
    "<th>Age</th>": "<th>距今</th>",
    "loading…": "加载中…",
    "Nothing needs you. Every task is working, idle or accepted.": "没有需要你处理的事：任务都在进行、闲置或已验收。",
    "Nothing in flight.": "没有进行中的任务。",
    "Read-only view of <code>.tower/</code> · auto-refresh every 2s · ": "只读视图 <code>.tower/</code> · 每 2 秒自动刷新 · ",
    '"generated "': '"生成于 "',
    '" ago · round "': '" 前 · 轮次 "',
    "panel disconnected — is `tower panel` still running?": "面板断开 —— `tower panel` 还在运行吗？",
    "— control tower": "— 控制塔",
    ">Refresh<": ">刷新<",
}
STATS_JS = {
    "en": [
        '["needs you", c.attention ?? 0]',
        '["in flight", c.in_flight ?? 0]',
        '["tasks", c.total ?? 0]',
        '["ready", c.ready ?? 0]',
        '["working", c.progress ?? 0]',
    ],
    "zh": [
        '["需要你", c.attention ?? 0]',
        '["进行中", c.in_flight ?? 0]',
        '["任务", c.total ?? 0]',
        '["就绪", c.ready ?? 0]',
        '["处理中", c.progress ?? 0]',
    ],
}
STATS_BLOCK = """      ["needs you", c.attention ?? 0],
      ["in flight", c.in_flight ?? 0],
      ["tasks", c.total ?? 0],
      ["ready", c.ready ?? 0],
      ["working", c.progress ?? 0],"""
LANG_BAR = """<span class="lang"><a href="?lang=zh">中文</a> / <a href="?lang=en">EN</a></span>"""


def pick_lang(query: str, accept_language: str) -> str:
    """`?lang=` wins; otherwise Chinese when the client asks for it."""
    for pair in query.split("&"):
        key, _, value = pair.partition("=")
        if key == "lang" and value in ("zh", "en"):
            return value
    lowered = (accept_language or "").lower()
    return "zh" if "zh" in lowered else "en"


def render_page(cfg, lang: str = "en") -> str:
    """The HQ page (see `panel_hq`): one page, many projects, read-only."""
    from .panel_hq import render as render_hq

    return render_hq(cfg, lang)


def _project_configs(cfg: TowerConfig):
    """Every project the panel knows: the registry, plus the one it was started in.

    A user who never touched the registry keeps the single-project behaviour: the
    panel shows the workspace it was started in.
    """
    from . import registry
    from .config import load_config

    entries = registry.load_projects()
    started = str(Path(cfg.workspace).resolve())
    if not any(str(Path(e["path"]).resolve()) == started for e in entries):
        entries = [{"name": cfg.name, "path": started, "color": registry.PALETTE[0]}] + entries
    active = registry.get_active()
    active_name = active["name"] if active else cfg.name
    configs = {}
    for entry in entries:
        try:
            configs[entry["name"]] = (entry, load_config(Path(entry["path"])))
        except Exception:
            configs[entry["name"]] = (entry, cfg if str(Path(entry["path"]).resolve()) == started else None)
    return entries, configs, active_name


def make_handler(cfg: TowerConfig):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):  # keep the terminal quiet
            return

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            if path == "/scene":
                from .panel_scene import render as render_scene

                lang = pick_lang(self.path.split("?", 1)[1] if "?" in self.path else "",
                                 self.headers.get("Accept-Language", ""))
                self._send(200, render_scene(cfg, lang).encode("utf-8"),
                           "text/html; charset=utf-8")
                return
            if path in ("/", "/index.html", "/hq"):
                lang = pick_lang(self.path.split("?", 1)[1] if "?" in self.path else "",
                                 self.headers.get("Accept-Language", ""))
                page = render_page(cfg, lang)
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/api/projects":
                from . import registry

                entries, _configs, active_name = _project_configs(cfg)
                data = registry.overview(entries)
                data["active"] = active_name
                self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if path == "/api/snapshot":
                target = self._cfg_for(query, cfg)
                if target is None:
                    self._send(404, b'{"error":"unknown project"}', "application/json")
                    return
                snap = build_snapshot(target, Board(target)).as_dict()
                self._send(200, json.dumps(snap, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if path == "/api/stats":
                from .analytics import build_report

                target = self._cfg_for(query, cfg)
                if target is None:
                    self._send(404, b'{"error":"unknown project"}', "application/json")
                    return
                report = build_report(target, Board(target), days=14)
                self._send(200, json.dumps(report, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if path == "/api/scene":
                from .scene import build_scene

                target = self._cfg_for(query, cfg)
                if target is None:
                    self._send(404, b'{"error":"unknown project"}', "application/json")
                    return
                try:
                    from . import registry

                    name = getattr(target, "name", "")
                    colour = next((item.get("color") for item in registry.load_projects()
                                   if item.get("name") == name), "#ea7a0c")
                except Exception:
                    colour = "#ea7a0c"
                payload = build_scene(target, Board(target), project_color=colour or "#ea7a0c")
                self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if path in ("/healthz", "/api/health"):
                from . import __version__

                body = json.dumps({"ok": True, "version": __version__, "instance": INSTANCE,
                                   "started_at": STARTED_AT, "pid": os.getpid()})
                self._send(200, body.encode("utf-8"), "application/json; charset=utf-8")
                return
            self._send(404, b"not found", "text/plain; charset=utf-8")

        def do_POST(self):  # noqa: N802
            path = self.path.split("?")[0]
            if path != "/api/actions":
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            try:
                length = min(int(self.headers.get("Content-Length", "0")), 64 * 1024)
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except (ValueError, OSError):
                self._send(400, b'{"ok":false,"error":"bad json"}', "application/json")
                return
            query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            target = self._cfg_for(query, cfg)
            board = Board(target)
            try:
                from .scene_actions import perform

                result = perform(target, board, str(body.get("action", "")),
                                 str(body.get("task", "")), str(body.get("note", "")))
            except KeyError as exc:
                self._send(400, json.dumps({"ok": False, "error": str(exc)}).encode(), "application/json")
                return
            except ValueError as exc:
                self._send(409, json.dumps({"ok": False, "error": str(exc)}).encode(), "application/json")
                return
            except FileNotFoundError as exc:
                self._send(404, json.dumps({"ok": False, "error": str(exc)}).encode(), "application/json")
                return
            self._send(200, json.dumps(result, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _cfg_for(self, query: dict, fallback: TowerConfig):
            """Project from `?project=`, else the workspace the panel was started in.

            The started workspace wins over the registry's active project: a panel
            launched in project A must never silently answer with project B. An
            unknown name (a stale bookmark after a rename) falls back instead of
            404-ing the whole board.
            """
            name = (query.get("project") or [""])[0]
            if not name or name == "__all__":
                return fallback
            _entries, configs, _active = _project_configs(fallback)
            chosen = configs.get(name)
            return chosen[1] if chosen and chosen[1] is not None else fallback

    return Handler


class PanelServer(ThreadingHTTPServer):
    """One panel per port, strictly.

    `allow_reuse_address` is what lets a second process bind a port that is already
    serving; on Windows that silently produced two panels on 8787, and requests
    were answered by whichever process won the race — including an old build after
    an upgrade. Refusing the second bind is the honest behaviour.
    """

    allow_reuse_address = False
    daemon_threads = True


INSTANCE = uuid.uuid4().hex[:8]
STARTED_AT = time.strftime("%Y-%m-%d %H:%M:%S")
SOURCE_FILES = ("__init__.py", "panel.py", "panel_hq.py", "snapshot.py", "registry.py",
                "analytics.py", "board.py", "config.py", "verify.py",
                "scene.py", "scene_actions.py", "panel_scene.py")


def source_fingerprint(directory: Path | None = None) -> str:
    """Cheap 'is this still the code I booted with?' marker."""
    import hashlib

    base = Path(directory or Path(__file__).parent)
    digest = hashlib.sha256()
    for name in SOURCE_FILES:
        path = base / name
        try:
            stat = path.stat()
            digest.update(f"{name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
        except OSError:
            digest.update(f"{name}:missing".encode())
    return digest.hexdigest()[:16]


def should_restart(booted: str, current: str) -> bool:
    """The whole rule, in one testable line."""
    return bool(booted) and booted != current


def restart_argv() -> list[str]:
    """argv to come back with, minus the interpreter."""
    argv = list(sys.argv[1:])
    return argv or []


def _watch_and_restart(booted: str, interval: float = 2.0) -> None:
    """Re-exec ourselves when the installed code changes under us."""

    def loop() -> None:
        while True:
            time.sleep(interval)
            if should_restart(booted, source_fingerprint()):
                try:
                    os.execv(sys.executable, [sys.executable, "-m", "control_tower", *restart_argv()])
                except OSError:
                    return

    threading.Thread(target=loop, daemon=True).start()


DEFAULT_PORT = 8787
PORT_SCAN = 20          # how many ports to try before giving up
PORT_MEMO = "panel-port"  # remembered inside .tower/


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    """Can we bind there right now?"""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def remembered_port(cfg: TowerConfig) -> int | None:
    path = cfg.state_dir / PORT_MEMO
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return value if 1 <= value <= 65535 else None


def remember_port(cfg: TowerConfig, port: int) -> None:
    try:
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        (cfg.state_dir / PORT_MEMO).write_text(str(port), encoding="utf-8")
    except OSError:
        pass


def choose_port(cfg: TowerConfig, requested: int | None = None,
                host: str = "127.0.0.1") -> int:
    """Pick the port the panel should own — and remember it.

    Order, because a board a person bookmarked must keep working:

    1. an explicit `--port`: that exact port, no surprises (the DSH bundle passes
       its configured port and reads it back);
    2. the port this workspace used last time (`.tower/panel-port`);
    3. 8787;
    4. 8787+1 … 8787+19, the first free one.

    The chosen port is written back, so the next run lands on the same URL instead
    of drifting every time somebody else's tool grabs the default.
    """
    if requested:
        remember_port(cfg, requested)
        return requested
    candidates = []
    last = remembered_port(cfg)
    if last:
        candidates.append(last)
    candidates.append(DEFAULT_PORT)
    candidates.extend(DEFAULT_PORT + i for i in range(1, PORT_SCAN))
    for port in candidates:
        if port_free(port, host):
            remember_port(cfg, port)
            return port
    raise OSError(f"ports {DEFAULT_PORT}..{DEFAULT_PORT + PORT_SCAN - 1} are all in use")


def serve(cfg: TowerConfig, host: str = "127.0.0.1", port: int | None = None,
          open_browser: bool = False) -> None:
    """Block on the panel server. Ctrl-C stops it."""
    chosen = choose_port(cfg, port, host)
    try:
        httpd = PanelServer((host, chosen), make_handler(cfg))
    except OSError as exc:
        raise SystemExit(
            f"port {chosen} is taken on {host} ({exc}). Pass another one: tower panel --port {chosen + 1}"
        ) from exc
    url = f"http://{host}:{chosen}/"
    from . import __version__

    print(f"tower panel → {url}  (workspace: {cfg.workspace})")
    print(f"v{__version__} · instance {INSTANCE} · port {chosen} remembered for this workspace")
    print("Ctrl-C to stop. This page is read-only; it reloads itself when the code changes.")
    booted = source_fingerprint()
    _watch_and_restart(booted)
    if open_browser:
        try:
            import webbrowser

            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\npanel stopped")
    finally:
        httpd.server_close()
