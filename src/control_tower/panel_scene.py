"""The isometric control room: one HTML file, Canvas 2D, no build step.

Everything drawn comes from `/api/scene`, which comes from the tower's own files.
The walk cycles, blinking, humming LED strips and the elevator are motion — they
are the only invented things here, and they never claim to be data.

Buttons are real: approve / reply / skip / repair POST to `/api/actions` and land in
the ledger (`docs/scene-brief.md` explains the mapping).
"""

from __future__ import annotations

PAGE = r"""<!doctype html>
<html lang="__LANG__" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__NAME__ · Control Room</title>
<style>
  :root{
    --bg:#070b12; --bg2:#0b1220; --panel:rgba(13,19,30,.86); --ink:#e9eef6; --dim:#8195ad;
    --line:rgba(120,150,200,.16); --line2:rgba(120,150,200,.3);
    --accent:#3b82f6; --ok:#22c55e; --warn:#f59e0b; --bad:#ef4444; --idle:#64748b;
    --mono:"Cascadia Mono",Consolas,ui-monospace,monospace;
    --sans:"Segoe UI","PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  }
  html[data-theme="light"]{
    --bg:#eef2f8; --bg2:#e3e9f3; --panel:rgba(255,255,255,.9); --ink:#101828; --dim:#5a6b82;
    --line:rgba(16,24,40,.12); --line2:rgba(16,24,40,.22);
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;overflow:hidden;background:var(--bg);color:var(--ink);
            font:13px/1.5 var(--sans)}
  #stage{position:absolute;inset:0}
  canvas{display:block;width:100%;height:100%}
  .glass{background:var(--panel);border:1px solid var(--line);border-radius:16px;
         backdrop-filter:blur(12px);box-shadow:0 24px 60px -30px rgba(0,0,0,.8)}
  /* top bar */
  #top{position:absolute;top:14px;left:50%;transform:translateX(-50%);z-index:5;
       display:flex;align-items:center;gap:10px;padding:8px 12px;white-space:nowrap}
  #top .brand{font-weight:750;letter-spacing:-.01em}
  #top .acct{font:11px var(--mono);color:var(--dim)}
  .chip{display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border-radius:999px;
        border:1px solid var(--line);font:11px var(--mono);color:var(--dim);cursor:pointer}
  .chip b{color:var(--ink)}
  .chip.on{border-color:var(--line2);background:rgba(120,150,200,.1)}
  #search{width:150px;padding:5px 9px;border-radius:9px;border:1px solid var(--line);
          background:transparent;color:var(--ink);font:12px var(--sans)}
  #search:focus{outline:none;border-color:var(--accent)}
  .ibtn{width:28px;height:28px;border-radius:8px;border:1px solid var(--line);
        background:transparent;color:var(--ink);cursor:pointer}
  /* side panels */
  #left,#right{position:absolute;top:66px;bottom:14px;width:308px;z-index:4;
               padding:12px;overflow:auto;scrollbar-width:thin}
  #left{left:14px} #right{right:14px}
  @media (max-width:1180px){ #left,#right{display:none} #mobileTabs{display:flex} }
  h3{margin:0 0 8px;font:600 10.5px var(--mono);letter-spacing:.16em;text-transform:uppercase;
     color:var(--dim)}
  .block{margin-bottom:14px}
  .floor{display:grid;grid-template-columns:26px 1fr auto;gap:8px;align-items:center;
         padding:6px 8px;border-radius:9px;cursor:pointer;border:1px solid transparent}
  .floor:hover{background:rgba(120,150,200,.08)}
  .floor.sel{border-color:var(--accent);background:rgba(59,130,246,.12)}
  .floor .lv{font:11px var(--mono);color:var(--dim);text-align:center}
  .floor .nm{font-weight:650}
  .floor .sub{font-size:11px;color:var(--dim)}
  .pill{font:600 10px var(--mono);padding:1px 6px;border-radius:5px}
  .pill.ok{background:rgba(34,197,94,.16);color:#4ade80}
  .pill.warn{background:rgba(245,158,11,.16);color:#fbbf24}
  .pill.bad{background:rgba(239,68,68,.18);color:#f87171}
  .pill.idle{background:rgba(100,116,139,.2);color:#94a3b8}
  .card{border:1px solid var(--line);border-radius:12px;padding:9px 10px;margin-bottom:8px;
        background:rgba(120,150,200,.05)}
  .card.stuck{border-color:rgba(239,68,68,.4)}
  .card .who{font:600 10.5px var(--mono);color:var(--dim);letter-spacing:.08em;text-transform:uppercase}
  .card .why{font-size:12.5px;margin:4px 0 7px;line-height:1.45}
  .acts{display:flex;gap:6px;flex-wrap:wrap}
  .btn{flex:0 0 auto;border:1px solid var(--line2);background:transparent;color:var(--ink);
       border-radius:8px;padding:4px 9px;font:11.5px var(--sans);cursor:pointer}
  .btn:hover{border-color:var(--accent);color:var(--accent)}
  .btn.primary{background:rgba(34,197,94,.16);border-color:rgba(34,197,94,.4);color:#4ade80}
  .btn.danger{background:rgba(239,68,68,.14);border-color:rgba(239,68,68,.4);color:#f87171}
  .done{opacity:.55}
  .kv{display:flex;justify-content:space-between;gap:8px;font:11.5px var(--mono);padding:3px 0}
  .kv span{color:var(--dim)}
  .bar{height:7px;border-radius:4px;background:rgba(120,150,200,.18);overflow:hidden;margin:6px 0 2px}
  .bar i{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--ok))}
  #log{max-height:150px;overflow:auto;font:11px/1.6 var(--mono);color:var(--dim)}
  #log b{color:var(--warn);font-weight:600}
  #assist{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px}
  #answer{font-size:12.5px;line-height:1.5;color:var(--ink);min-height:32px}
  /* scene helpers */
  #hint{position:absolute;left:50%;transform:translateX(-50%);bottom:16px;z-index:3;
        font:11px var(--mono);color:var(--dim);text-align:center;line-height:1.7}
  #toast{position:absolute;left:50%;top:70px;transform:translateX(-50%) translateY(-8px);z-index:9;
         padding:7px 12px;border-radius:999px;background:rgba(34,197,94,.16);
         border:1px solid rgba(34,197,94,.4);color:#4ade80;font:12px var(--mono);
         opacity:0;transition:.25s;pointer-events:none}
  #toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
  #mobileTabs{position:absolute;bottom:0;left:0;right:0;display:none;z-index:6;
              border-top:1px solid var(--line);background:var(--panel);padding:8px 12px;gap:8px}
  #mobileTabs button{flex:1;padding:8px;border-radius:10px;border:1px solid var(--line);
                     background:transparent;color:var(--ink);font:12px var(--sans)}
  @media (max-width:1180px){
    #top{left:12px;right:12px;transform:none;overflow:auto}
    #hint{bottom:64px}
    body.sheet-left #left,body.sheet-right #right{display:block;top:auto;bottom:56px;height:52vh}
    body.sheet-left #left{left:8px;right:8px;width:auto}
    body.sheet-right #right{left:8px;right:8px;width:auto}
  }
</style>
</head>
<body>
<div id="stage"><canvas id="c"></canvas></div>

<div id="top" class="glass">
  <span class="brand">__NAME__</span>
  <span class="acct">local operator</span>
  <span class="chip" id="clock">--:--</span>
  <span class="chip" data-filter="working">Working <b id="k-working">0</b></span>
  <span class="chip" data-filter="waiting">Waiting <b id="k-waiting">0</b></span>
  <span class="chip" data-filter="stuck">Stuck <b id="k-stuck">0</b></span>
  <span class="chip" data-filter="resting">Resting <b id="k-resting">0</b></span>
  <span class="chip" id="k-spend">Spend <b>$0.00</b></span>
  <input id="search" placeholder="search agents…" autocomplete="off">
  <button class="ibtn" id="themeBtn" title="theme (T)">◐</button>
  <button class="ibtn" id="fitBtn" title="fit view (0)">⤢</button>
</div>

<div id="left" class="glass">
  <div class="block">
    <h3>Floors</h3>
    <div id="floors"></div>
  </div>
  <div class="block">
    <h3>Needs you <span id="pendingCount" style="color:var(--bad)"></span></h3>
    <div id="pending"><div class="kv"><span>nothing pending</span></div></div>
  </div>
  <div class="block">
    <h3>Scheduled</h3>
    <div id="scheduled"><div class="kv"><span>no deadlines</span></div></div>
  </div>
  <div class="block">
    <h3>Completed today <span id="compCount" style="color:var(--dim)"></span></h3>
    <div id="completed"></div>
  </div>
</div>

<div id="right" class="glass">
  <div class="block">
    <h3 id="ctxTitle">Building</h3>
    <div id="ctx"></div>
  </div>
  <div class="block">
    <h3>Spend <span style="color:var(--dim);text-transform:none;letter-spacing:0">(estimated)</span></h3>
    <div class="kv"><span>today</span><b id="spend">$0.00</b></div>
    <div class="bar"><i id="spendBar" style="width:0%"></i></div>
    <div class="kv"><span id="budgetNote">budget —</span><span id="topBurner"></span></div>
  </div>
  <div class="block">
    <h3>Activity</h3>
    <div id="log"></div>
  </div>
  <div class="block">
    <h3>Ask</h3>
    <div id="assist">
      <button class="btn" data-q="summary">Summary</button>
      <button class="btn" data-q="waiting">Who is waiting</button>
      <button class="btn" data-q="errors">What is stuck</button>
      <button class="btn" data-q="spend">Spend</button>
    </div>
    <div id="answer">Ask about the tower: “summary”, “who is waiting”, “what is stuck”, “spend”.</div>
  </div>
</div>

<div id="hint">click a floor to focus · wheel to zoom · drag to pan · double-click to fit · keys: 1–6 floors, 0 building, W next waiting, S next stuck, A/R/S approve,reply,skip, F repair, T theme</div>
<div id="toast"></div>
<div id="mobileTabs">
  <button data-sheet="left">Floors & queue</button>
  <button data-sheet="right">Details</button>
  <button data-sheet="">Scene</button>
</div>

<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const QS = new URLSearchParams(location.search);
const THEME = "tower-scene-theme";
document.documentElement.setAttribute("data-theme", QS.get("theme") || localStorage.getItem(THEME) || "dark");
$("themeBtn").onclick = () => {
  const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem(THEME, next);
};

const BUDGET = 80;
const COLOURS = { working:"#22c55e", waiting:"#f59e0b", stuck:"#ef4444", resting:"#64748b" };
let DATA = null;
let selectedFloor = null;        // null = whole building
let selectedAgent = null;
let hoverFloor = null;
let filter = null;
let query = "";
let toastTimer = null;

// ---------- camera ----------
const cam = { x: 0, y: 0, zoom: 1, tx: 0, ty: 0, tz: 1, floor: null };
const canvas = $("c");
const ctx = canvas.getContext("2d");
const MONO = '"Cascadia Mono",Consolas,ui-monospace,monospace';

function resize() {
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = Math.floor(innerWidth * dpr);
  canvas.height = Math.floor(innerHeight * dpr);
  canvas.style.width = innerWidth + "px";
  canvas.style.height = innerHeight + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
addEventListener("resize", () => { resize(); fit(true); });
resize();

// ---------- isometric helpers ----------
// Everything below draws in SCENE UNITS: the context is translated+scaled once by
// the camera, so no drawing call multiplies by zoom again (that double-scaled the
// whole tower and pushed it off-canvas in earlier drafts).
const TILE = 46, FLOOR_H = 62;
function iso(x, y, z) {
  return { x: (x - y) * TILE * 0.866, y: (x + y) * TILE * 0.5 - z * FLOOR_H };
}

// ---------- model of the scene (decorative only) ----------
const scene = { bots: [] };

function buildScene() {
  scene.bots = [];
  if (!DATA) return;
  DATA.agents.forEach((agent, index) => {
    const x = 1 + (index % 3) * 1.05;
    const y = 1 + Math.floor(index / 3) * 1.0;
    scene.bots.push({
      id: agent.id, floor: agent.floor, accent: agent.accent, status: agent.status,
      x, y, homeX: x, homeY: y,
      phase: Math.random() * Math.PI * 2,
      blink: 1.5 + Math.random() * 2.5,
    });
  });
}

// ---------- drawing ----------
function drawRoom() {
  const night = document.documentElement.getAttribute("data-theme") === "dark";
  const sky = ctx.createLinearGradient(0, 0, 0, innerHeight);
  sky.addColorStop(0, night ? "#060a12" : "#dbe7f7");
  sky.addColorStop(1, night ? "#0b1220" : "#eef3fa");
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, innerWidth, innerHeight);

  ctx.save();
  ctx.translate(cam.x, cam.y);
  ctx.scale(cam.zoom, cam.zoom);

  // plaza
  const plaza = [iso(-2.4, -2.4, 0), iso(6.4, -2.4, 0), iso(6.4, 6.4, 0), iso(-2.4, 6.4, 0)];
  ctx.beginPath();
  plaza.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
  ctx.closePath();
  ctx.fillStyle = night ? "rgba(30,45,68,.55)" : "rgba(170,188,212,.55)";
  ctx.fill();

  // roads with one moving car each
  [[-2.0, 6.3, 6.4, 6.3], [6.3, -2.0, 6.3, 6.4]].forEach(([ax, ay, bx, by], lane) => {
    const a = iso(ax, ay, 0), b = iso(bx, by, 0);
    ctx.strokeStyle = night ? "rgba(70,95,125,.75)" : "rgba(120,140,168,.75)";
    ctx.lineWidth = 26; ctx.lineCap = "round";
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    const t = ((performance.now() / 7000) + lane * 0.5) % 1;
    const cx = a.x + (b.x - a.x) * t, cy = a.y + (b.y - a.y) * t;
    ctx.fillStyle = night ? "#7dd3fc" : "#3b82f6";
    ctx.beginPath(); ctx.ellipse(cx, cy - 7, 6, 3.4, 0, 0, 7); ctx.fill();
  });

  // trees
  [[-2.0, 1.0], [-2.0, 3.6], [5.6, 1.2], [5.6, 4.4], [4.6, -2.0], [1.0, -2.0]].forEach(([x, y]) => {
    const p = iso(x, y, 0);
    ctx.fillStyle = night ? "#1e3a2a" : "#2f6b46";
    ctx.beginPath(); ctx.ellipse(p.x, p.y - 20, 13, 17, 0, 0, 7); ctx.fill();
    ctx.fillStyle = night ? "#3b2a1a" : "#5b4227";
    ctx.fillRect(p.x - 2, p.y - 8, 4, 10);
  });

  const floors = DATA ? DATA.floors : [];
  drawRoof(floors.length, night);
  floors.forEach((floor) => {
    const dim = cam.floor !== null && floor.level > cam.floor;
    const lift = hoverFloor === floor.level && cam.floor === null ? 6 : 0;
    drawFloor(floor, lift, dim, night);
  });
  drawLift(night);

  const sorted = [...scene.bots].sort((a, b) => (a.floor - b.floor) || (a.x + a.y) - (b.x + b.y));
  sorted.forEach((bot) => drawBot(bot, night));
  ctx.restore();
}

function drawRoof(levels, night) {
  if (!levels) return;
  const z = levels + 0.02;
  const pad = 0.35, w = 4.05;
  const roof = [iso(pad, pad, z), iso(w, pad, z), iso(w, w, z), iso(pad, w, z)];
  ctx.beginPath();
  roof.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
  ctx.closePath();
  ctx.fillStyle = night ? "#101a2c" : "#dfe8f4";
  ctx.fill();
  ctx.strokeStyle = night ? "rgba(140,180,235,.5)" : "rgba(90,120,170,.5)";
  ctx.lineWidth = 1.4; ctx.stroke();
  // antenna with a beacon
  const base = iso(2.2, 2.2, z);
  ctx.strokeStyle = night ? "rgba(160,200,250,.7)" : "rgba(70,100,140,.8)";
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(base.x, base.y); ctx.lineTo(base.x, base.y - 34); ctx.stroke();
  const on = Math.sin(performance.now() / 700) > 0;
  ctx.fillStyle = on ? "#ef4444" : "rgba(239,68,68,.25)";
  ctx.beginPath(); ctx.arc(base.x, base.y - 36, 3, 0, 7); ctx.fill();
}

function drawFloor(floor, lift, dim, night) {
  const base = floor.level;
  const yl = lift ? -lift / FLOOR_H : 0;
  const z = base + yl;
  const w = 4.0, d = 4.0, pad = 0.4;
  const st = floor.status;
  const acc = (DATA.agents.find((a) => a.floor === floor.level) || {}).accent || "#3b82f6";

  ctx.save();
  ctx.globalAlpha = dim ? 0.24 : 1;

  // ---- side walls (drawn first: painter order) ----
  const H = 0.95;
  const quad = (a, b, c, e, fill, stroke) => {
    ctx.beginPath();
    [a, b, c, e].forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
    ctx.closePath();
    ctx.fillStyle = fill; ctx.fill();
    ctx.strokeStyle = stroke; ctx.lineWidth = 0.9; ctx.stroke();
  };
  const glassFill = night ? "rgba(96,148,214,.13)" : "rgba(168,206,244,.24)";
  const glassLine = night ? "rgba(150,195,245,.30)" : "rgba(110,150,196,.42)";
  // left-back face (x = pad)
  quad(iso(pad, pad, z), iso(pad, d, z), iso(pad, d, z + H), iso(pad, pad, z + H), glassFill, glassLine);
  // right-back face (y = pad)
  quad(iso(pad, pad, z), iso(w, pad, z), iso(w, pad, z + H), iso(pad, pad, z + H), glassFill, glassLine);
  // front faces: near-transparent glass, enough to close the box
  quad(iso(pad, d, z), iso(pad, d, z + H), iso(w, d, z + H), iso(w, d, z),
       night ? "rgba(90,140,205,.05)" : "rgba(150,190,230,.10)", glassLine);
  quad(iso(w, pad, z), iso(w, d, z), iso(w, d, z + H), iso(w, pad, z + H),
       night ? "rgba(90,140,205,.05)" : "rgba(150,190,230,.10)", glassLine);

  // mullions
  for (let i = 1; i < 5; i += 1) {
    const u = pad + (w - pad) * (i / 5);
    let a = iso(u, pad, z), b = iso(u, pad, z + H);
    ctx.strokeStyle = glassLine; ctx.lineWidth = 0.7;
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    a = iso(pad, u, z); b = iso(pad, u, z + H);
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    a = iso(u, d, z); b = iso(u, d, z + H);
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
  }

  // ---- slab ----
  const slab = [iso(pad, pad, z), iso(w, pad, z), iso(w, d, z), iso(pad, d, z)];
  ctx.beginPath();
  slab.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
  ctx.closePath();
  ctx.fillStyle = night ? "#1a2740" : "#f7fafd";
  ctx.fill();
  ctx.strokeStyle = night ? "rgba(140,180,235,.55)" : "rgba(90,120,170,.5)";
  ctx.lineWidth = 1.4; ctx.stroke();

  // slab thickness (front two faces)
  const TH = 0.12;
  quad(iso(pad, d, z), iso(w, d, z), iso(w, d, z - TH), iso(pad, d, z - TH),
       night ? "#131e33" : "#e2e9f4", night ? "rgba(140,180,235,.35)" : "rgba(90,120,170,.35)");
  quad(iso(pad, pad, z), iso(pad, d, z), iso(pad, d, z - TH), iso(pad, pad, z - TH),
       night ? "#16233a" : "#e8eef7", night ? "rgba(140,180,235,.3)" : "rgba(90,120,170,.3)");

  // ---- interior: desks + one signature prop per department ----
  const desk = (dx, dy, tone) => {
    const p0 = iso(dx, dy, z);
    ctx.fillStyle = tone || (night ? "rgba(205,225,245,.85)" : "rgba(80,100,132,.8)");
    ctx.fillRect(p0.x - 12, p0.y - 5, 24, 7);
    const screen = night ? "rgba(125,211,252,.9)" : "rgba(59,130,246,.85)";
    ctx.fillStyle = st === "working" ? screen : (night ? "rgba(100,130,170,.5)" : "rgba(140,160,190,.6)");
    ctx.fillRect(p0.x - 9, p0.y - 19, 18, 11);
    ctx.strokeStyle = night ? "rgba(20,30,48,.9)" : "rgba(230,236,244,.95)";
    ctx.lineWidth = 1; ctx.strokeRect(p0.x - 9, p0.y - 19, 18, 11);
  };
  desk(1.15, 1.2); desk(2.45, 1.2);

  const prop = (dx, dy, ww, hh, colour, label) => {
    const p0 = iso(dx, dy, z);
    ctx.fillStyle = colour;
    ctx.fillRect(p0.x - ww / 2, p0.y - hh, ww, hh);
    ctx.strokeStyle = "rgba(255,255,255,.18)"; ctx.lineWidth = 1;
    ctx.strokeRect(p0.x - ww / 2, p0.y - hh, ww, hh);
    if (label) {
      ctx.fillStyle = night ? "rgba(220,235,255,.75)" : "rgba(30,50,80,.8)";
      ctx.font = `600 7px ${MONO}`; ctx.textAlign = "center";
      ctx.fillText(label, p0.x, p0.y + 3);
    }
  };
  const dept = floor.dept;
  if (dept === "Code") {
    prop(3.2, 1.0, 16, 30, "#1f2937", "RACK");
    prop(3.2, 1.7, 16, 30, "#1f2937", "GPU");
    prop(3.2, 2.4, 16, 30, "#334155", "DB");
  } else if (dept === "Operations") {
    prop(3.1, 1.1, 34, 22, "#0f172a", "MONITOR WALL");
    prop(1.2, 2.7, 20, 12, "#334155", "PACKAGES");
  } else if (dept === "Content") {
    prop(3.1, 1.3, 30, 26, "rgba(236,72,153,.35)", "ON AIR");
    prop(1.3, 2.7, 18, 14, "#334155", "PANTRY");
  } else if (dept === "Research") {
    prop(3.1, 1.2, 26, 26, "rgba(34,197,94,.32)", "LIBRARY");
    prop(1.3, 2.6, 26, 18, "rgba(59,130,246,.3)", "LIVE MAP");
  } else if (dept === "Creative") {
    prop(3.1, 1.2, 30, 24, "rgba(245,158,11,.35)", "RENDER");
    prop(1.3, 2.7, 22, 12, "#334155", "BEAN BAGS");
  } else if (dept === "Strategy") {
    prop(2.2, 2.6, 30, 10, "rgba(100,116,139,.55)", "ROUND TABLE");
  } else {
    prop(3.1, 1.2, 26, 22, "rgba(148,163,184,.35)", "DESK");
  }

  // ---- LED strip: a thin quad inside the slab's front edge ----
  const blink = st === "stuck" ? (Math.sin(performance.now() / 200) > 0 ? 1 : 0.18) : 1;
  ctx.globalAlpha = (dim ? 0.15 : 1) * blink;
  const lw = 0.12;
  const strip = [iso(pad + 0.06, d - 0.02, z + 0.005), iso(w - 0.06, d - 0.02, z + 0.005),
                 iso(w - 0.06, d - 0.02 - lw, z + 0.005), iso(pad + 0.06, d - 0.02 - lw, z + 0.005)];
  ctx.beginPath();
  strip.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
  ctx.closePath();
  ctx.fillStyle = COLOURS[st] || COLOURS.resting;
  ctx.fill();
  ctx.globalAlpha = dim ? 0.24 : 1;

  // ---- floor label ----
  const label = iso(w + 0.18, d - 0.5, z + 0.3);
  ctx.fillStyle = night ? "rgba(210,230,252,.9)" : "rgba(30,50,80,.9)";
  ctx.font = `600 10px ${MONO}`; ctx.textAlign = "left";
  ctx.fillText(`L${floor.level} · ${floor.dept.toUpperCase()}`, label.x + 6, label.y);
  ctx.restore();
}

function drawLift(night) {
  const levels = DATA ? DATA.floors.length : 0;
  if (!levels) return;
  const x = 4.16, y = 4.16;              // inside the footprint: reads as attached
  const bottom = iso(x, y, 0), top = iso(x, y, levels + 0.5);
  ctx.save();
  // rails
  [-0.16, 0.16].forEach((offset) => {
    const a = iso(x + offset, y + offset, 0), b = iso(x + offset, y + offset, levels + 0.5);
    ctx.strokeStyle = night ? "rgba(150,195,245,.45)" : "rgba(90,130,180,.55)";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
  });
  // glass shaft
  const shaft = [iso(x - 0.4, y - 0.4, 0), iso(x + 0.4, y - 0.4, 0),
                 iso(x + 0.4, y - 0.4, levels + 0.5), iso(x - 0.4, y - 0.4, levels + 0.5)];
  ctx.beginPath();
  shaft.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
  ctx.closePath();
  ctx.fillStyle = night ? "rgba(110,160,220,.10)" : "rgba(170,206,242,.22)";
  ctx.fill();
  ctx.strokeStyle = night ? "rgba(150,195,245,.35)" : "rgba(90,130,180,.45)";
  ctx.lineWidth = 1; ctx.stroke();

  // building name on the roof deck
  const nameAt = iso(1.2, 3.4, levels + 0.06);
  ctx.fillStyle = night ? "rgba(190,215,250,.55)" : "rgba(50,75,110,.6)";
  ctx.font = `700 9px ${MONO}`; ctx.textAlign = "left";
  ctx.fillText((DATA.project.name || "").toUpperCase().slice(0, 22), nameAt.x, nameAt.y);

  // the cab rides up and down between floors
  const span = Math.max(1, levels - 1);
  const t = (Math.sin(performance.now() / 5200) * 0.5 + 0.5) * span;
  const cab = iso(x, y, t + 0.12);
  ctx.fillStyle = night ? "rgba(125,211,252,.92)" : "rgba(59,130,246,.92)";
  ctx.beginPath(); ctx.roundRect(cab.x - 9, cab.y - 13, 18, 22, 3); ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,.65)"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.roundRect(cab.x - 9, cab.y - 13, 18, 22, 3); ctx.stroke();
  ctx.restore();
}

function drawBot(bot, night) {
  const now = performance.now() / 1000;
  const bob = Math.sin(now * 2 + bot.phase) * 1.5;
  const p = iso(bot.x, bot.y, bot.floor);
  ctx.save();
  ctx.globalAlpha = cam.floor !== null && bot.floor > cam.floor ? 0.22 : 1;

  // status ring
  const pulse = bot.status === "waiting" ? 1 + 0.1 * Math.sin(now * 4 + bot.phase) : 1;
  ctx.strokeStyle = COLOURS[bot.status] || COLOURS.resting;
  ctx.lineWidth = 2.4;
  ctx.beginPath(); ctx.ellipse(p.x, p.y, 12 * pulse, 5.5 * pulse, 0, 0, 7); ctx.stroke();

  // stuck: smoke puffs
  if (bot.status === "stuck") {
    for (let i = 0; i < 3; i += 1) {
      const t = (now * 0.55 + i * 0.33) % 1;
      ctx.globalAlpha = (1 - t) * 0.45;
      ctx.fillStyle = "#cbd5e1";
      ctx.beginPath();
      ctx.ellipse(p.x + Math.sin(t * 8 + i) * 5, p.y - (16 + t * 24), 4.5, 4.5, 0, 0, 7);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  const y = p.y - 26 + bob;
  // legs
  ctx.fillStyle = "#0f172a";
  ctx.fillRect(p.x - 6, y + 15, 4, 7);
  ctx.fillRect(p.x + 2, y + 15, 4, 7);
  // torso
  ctx.fillStyle = bot.accent;
  ctx.beginPath(); ctx.roundRect(p.x - 9, y + 4, 18, 13, 5); ctx.fill();
  // head capsule
  ctx.fillStyle = night ? "#151b25" : "#202834";
  ctx.beginPath(); ctx.roundRect(p.x - 11, y - 12, 22, 18, 8.5); ctx.fill();
  // highlight
  ctx.fillStyle = "rgba(255,255,255,.18)";
  ctx.beginPath(); ctx.ellipse(p.x - 4, y - 8.5, 6, 3.6, -0.15, 0, 7); ctx.fill();
  // eyes: // slashes, blinking on a slow cycle
  const closed = ((now + bot.blink) % (3.4 + (bot.phase % 1.4))) > 3.25;
  ctx.strokeStyle = "#f8fafc"; ctx.lineWidth = 2.4; ctx.lineCap = "round";
  if (closed) {
    ctx.beginPath(); ctx.moveTo(p.x - 6, y - 2); ctx.lineTo(p.x - 2, y - 2); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(p.x + 2, y - 2); ctx.lineTo(p.x + 6, y - 2); ctx.stroke();
  } else {
    ctx.beginPath(); ctx.moveTo(p.x - 6, y - 5); ctx.lineTo(p.x - 2.5, y + 1); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(p.x + 2.5, y - 5); ctx.lineTo(p.x + 6, y + 1); ctx.stroke();
  }
  // antenna
  ctx.strokeStyle = "#94a3b8"; ctx.lineWidth = 1.2;
  ctx.beginPath(); ctx.moveTo(p.x, y - 12); ctx.lineTo(p.x, y - 17); ctx.stroke();
  ctx.fillStyle = bot.accent;
  ctx.beginPath(); ctx.arc(p.x, y - 18.5, 2.2, 0, 7); ctx.fill();

  // waiting: amber question bubble
  if (bot.status === "waiting") {
    ctx.fillStyle = "rgba(245,158,11,.95)";
    ctx.beginPath(); ctx.arc(p.x + 15, y - 22, 6, 0, 7); ctx.fill();
    ctx.fillStyle = "#0b1220"; ctx.font = `700 8px ${MONO}`;
    ctx.textAlign = "center"; ctx.fillText("?", p.x + 15, y - 19.2);
  }
  // hidden by search/filter: fade out
  if (bot.hidden) ctx.globalAlpha *= 0.25;
  ctx.restore();
}

// ---------- interaction ----------
let dragging = false, lastX = 0, lastY = 0;
canvas.addEventListener("pointerdown", (e) => { dragging = true; lastX = e.clientX; lastY = e.clientY; });
canvas.addEventListener("pointerup", () => { dragging = false; });
canvas.addEventListener("pointermove", (e) => {
  if (dragging) {
    cam.tx += e.clientX - lastX; cam.ty += e.clientY - lastY;
    lastX = e.clientX; lastY = e.clientY;
  }
});
canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  cam.tz = Math.max(0.55, Math.min(2.2, cam.tz * (e.deltaY > 0 ? 0.93 : 1.07)));
}, { passive: false });
canvas.addEventListener("dblclick", () => fit(true));

$("fitBtn").onclick = () => fit(true);

function fit(animate) {
  if (!DATA) return;
  const levels = DATA.floors.length || 3;
  const side = innerWidth > 1180 ? 340 : 0;          // the two glass panels
  const availW = Math.max(320, innerWidth - side * 2 - 32);
  const availH = Math.max(320, innerHeight - 150);
  // bounding box of the drawn tower, in scene units
  const top = iso(0.4, 0.4, levels + 0.6), bottom = iso(4.0, 4.0, 0);
  const boxW = Math.abs(bottom.x - top.x) + 150;
  const boxH = Math.abs(bottom.y - top.y) + 160;
  cam.tz = Math.max(0.32, Math.min(1.5, Math.min(availW / boxW, availH / boxH)));
  // centre of the box
  const cx = (top.x + bottom.x) / 2, cy = (top.y + bottom.y) / 2;
  cam.tx = side + availW / 2 - cx * cam.tz;
  cam.ty = 90 + availH / 2 - cy * cam.tz;
  if (!animate) { cam.zoom = cam.tz; cam.x = cam.tx; cam.y = cam.ty; }
  selectedFloor = null; selectedAgent = null; cam.floor = null;
  renderPanels();
}

$("search").oninput = (e) => { query = e.target.value.trim().toLowerCase(); renderPanels(); };
document.querySelectorAll("[data-filter]").forEach((chip) => {
  chip.onclick = () => {
    filter = filter === chip.dataset.filter ? null : chip.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach((c) => c.classList.toggle("on", c.dataset.filter === filter));
    renderPanels();
  };
});

// ---------- panels ----------
function agentById(id) { return (DATA.agents || []).find((a) => a.id === id); }

function renderPanels() {
  if (!DATA) return;
  $("k-working").textContent = DATA.kpis.working;
  $("k-waiting").textContent = DATA.kpis.waiting;
  $("k-stuck").textContent = DATA.kpis.stuck;
  $("k-resting").textContent = DATA.kpis.resting;
  $("k-spend").innerHTML = `Spend <b>$${DATA.cost.est_usd.toFixed(2)}</b>`;

  // floors
  $("floors").innerHTML = DATA.floors.map((f) => `
    <div class="floor ${selectedFloor === f.level ? "sel" : ""}" data-level="${f.level}">
      <span class="lv">L${f.level}</span>
      <span><span class="nm">${esc(f.dept)}</span><br><span class="sub">${esc(f.id)} · ${f.open} open</span></span>
      <span class="pill ${f.status === "stuck" ? "bad" : f.status === "waiting" ? "warn" : f.status === "working" ? "ok" : "idle"}">
        ${esc(f.status)}</span>
    </div>`).join("");
  $("floors").querySelectorAll("[data-level]").forEach((el) => {
    el.onclick = () => { selectFloor(Number(el.dataset.level)); };
  });

  // pending
  const pending = DATA.pending || [];
  $("pendingCount").textContent = DATA.pending_total ? `(${DATA.pending_total})` : "";
  $("pending").innerHTML = pending.length ? pending.map((p) => `
    <div class="card ${p.kind === "stuck" ? "stuck" : ""}" data-task="${esc(p.task)}">
      <div class="who">${esc(p.agent)} · ${esc(p.kind)} · ${esc(p.task)}</div>
      <div class="why">${esc(p.reason)}</div>
      <div class="acts">
        <button class="btn primary" data-act="approve" data-task="${esc(p.task)}">Approve</button>
        <button class="btn" data-act="reply" data-task="${esc(p.task)}">Reply</button>
        <button class="btn" data-act="skip" data-task="${esc(p.task)}">Skip</button>
        <button class="btn danger" data-act="repair" data-task="${esc(p.task)}">Repair</button>
      </div>
    </div>`).join("") : `<div class="kv"><span>nothing pending</span></div>`;

  // scheduled
  const sched = DATA.scheduled || [];
  $("scheduled").innerHTML = sched.length ? sched.map((s) => `
    <div class="kv"><span>${esc(s.agent)} · ${esc(s.task)}</span>
      <b>${fmtCountdown(s.in_s)}</b></div>`).join("")
    : `<div class="kv"><span>no deadlines</span></div>`;

  // completed
  const comp = DATA.completed_today || [];
  $("compCount").textContent = `(${comp.length})`;
  $("completed").innerHTML = comp.slice(0, 6).map((c) => `
    <div class="kv done"><span>${esc(c.agent)} · ${esc(c.task)}</span>
      <span>${c.cost.runs} runs · $${c.cost.est_usd.toFixed(2)}</span></div>`).join("")
    || `<div class="kv"><span>nothing completed today</span></div>`;

  // context
  const ctxTitle = selectedAgent ? `Agent · ${selectedAgent}` : selectedFloor ? `Floor · L${selectedFloor}` : "Building";
  $("ctxTitle").textContent = ctxTitle;
  if (selectedAgent) {
    const a = agentById(selectedAgent);
    const t = a && a.task;
    $("ctx").innerHTML = a ? `
      <div class="kv"><span>dept</span><b>${esc(a.dept)}</b></div>
      <div class="kv"><span>model</span><b>${esc(a.model)}</b></div>
      <div class="kv"><span>status</span><b style="color:${COLOURS[a.status]}">${esc(a.status)}</b></div>
      <div class="kv"><span>tasks</span><b>${a.tasks}</b></div>
      <div class="kv"><span>runs · est</span><b>${a.cost.runs} · $${a.cost.est_usd.toFixed(2)}</b></div>
      ${t ? `<div class="card"><div class="who">current · ${esc(t.id)}</div>
        <div class="why">${esc(t.brief)}</div>
        ${t.steps.map((s) => `<div class="kv"><span>${s.done ? "✓" : "•"}</span><span>${esc(s.label)}</span></div>`).join("")}
      </div>` : ""}` : "";
  } else if (selectedFloor) {
    const list = (DATA.agents || []).filter((a) => a.floor === selectedFloor);
    $("ctx").innerHTML = list.map((a) => `
      <div class="card" data-agent="${esc(a.id)}">
        <div class="who"><span style="color:${COLOURS[a.status]}">●</span> ${esc(a.name)}</div>
        <div class="why">${esc(a.task ? a.task.brief : "no current task")}</div>
      </div>`).join("");
  } else {
    $("ctx").innerHTML = `
      <div class="kv"><span>project</span><b>${esc(DATA.project.name)}</b></div>
      <div class="kv"><span>agents</span><b>${DATA.kpis.agents}</b></div>
      <div class="kv"><span>completed today</span><b>${DATA.kpis.completed_today}</b></div>
      <div class="kv"><span>pending</span><b>${DATA.pending_total}</b></div>
      <div class="kv"><span>stuck</span><b>${DATA.stuck_total}</b></div>`;
  }

  // spend
  $("spend").textContent = "$" + DATA.cost.est_usd.toFixed(2);
  $("spendBar").style.width = Math.min(100, (DATA.cost.est_usd / BUDGET) * 100) + "%";
  $("budgetNote").textContent = `budget $${BUDGET} · ${DATA.cost.runs} runs`;
  $("topBurner").textContent = DATA.cost.top_burner ? `top ${DATA.cost.top_burner.agent}` : "";

  // log
  $("log").innerHTML = (DATA.log || []).slice(0, 24).map((e) => `
    <div>${esc(e.ts)} <b>${esc(e.event)}</b> ${esc(e.task)} ${esc(e.role)}</div>`).join("")
    || `<div>no events</div>`;

  // scene-level controls bound after every render
  $("pending").querySelectorAll("[data-act]").forEach((btn) => {
    btn.onclick = () => act(btn.dataset.act, btn.dataset.task, btn);
  });
  $("ctx").querySelectorAll("[data-agent]").forEach((el) => {
    el.onclick = () => { selectedAgent = el.dataset.agent; renderPanels(); };
  });

  // dim non-matching bots
  const match = (agent) => {
    const okFilter = !filter || agent.status === filter;
    const okQuery = !query || (agent.id + " " + agent.dept + " " + (agent.task ? agent.task.brief : "")).toLowerCase().includes(query);
    return okFilter && okQuery;
  };
  scene.bots.forEach((bot) => {
    const agent = agentById(bot.id);
    bot.hidden = agent ? !match(agent) : false;
  });
}

function fmtCountdown(seconds) {
  const late = seconds < 0;
  let s = Math.abs(seconds);
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  const text = d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
  return late ? `late ${text}` : text;
}

function selectFloor(level) {
  selectedFloor = selectedFloor === level ? null : level;
  selectedAgent = null;
  cam.floor = selectedFloor;
  const tz = selectedFloor ? Math.min(1.8, cam.tz * 1.12) : cam.tz;
  cam.tz = tz;
  renderPanels();
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
}

async function act(action, task, button) {
  if (action === "reply") {
    const note = prompt(`Reply to ${task} — this is sent back to the agent:`);
    if (!note) return;
    return send({ action, task, note });
  }
  return send({ action, task });
}

async function send(payload) {
  const card = document.querySelector(`[data-task="${payload.task}"]`);
  try {
    const response = await fetch("/api/actions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) { toast(result.error || "refused"); return; }
    if (card) {
      card.classList.add("done");
      card.style.maxHeight = "0px";
      card.style.overflow = "hidden";
      card.style.transition = "max-height .35s ease, opacity .35s ease";
      card.style.opacity = "0";
      setTimeout(() => card.remove(), 360);
    }
    toast(`✓ ${payload.action} · ${result.outcome || "done"}`);
    refresh();
  } catch (err) {
    toast("panel disconnected");
  }
}

// answers are computed from the payload — no invented numbers
function answer(question) {
  if (!DATA) return "loading…";
  const q = question.toLowerCase();
  const s = DATA;
  if (q.includes("summary") || q.includes("汇总")) {
    return `${s.project.name}: ${s.kpis.agents} agents · ${s.kpis.working} working · ${s.kpis.waiting} waiting · ${s.kpis.stuck} stuck. ${s.pending_total} item(s) need you, $${s.cost.est_usd.toFixed(2)} est. spend today.`;
  }
  if (q.includes("wait") || q.includes("等待")) {
    const list = s.pending.map((p) => `${p.task} (${p.agent})`).join(", ");
    return list ? `Waiting on you: ${list}.` : "Nothing is waiting on you.";
  }
  if (q.includes("stuck") || q.includes("error") || q.includes("错")) {
    const stuck = s.pending.filter((p) => p.kind === "stuck");
    return stuck.length ? `Stuck: ${stuck.map((p) => `${p.task} — ${p.reason}`).join(" | ")}` : "Nothing is stuck right now.";
  }
  if (q.includes("spend") || q.includes("cost") || q.includes("花")) {
    const top = s.cost.top_burner ? ` Top burner: ${s.cost.top_burner.agent}.` : "";
    return `Estimated spend today: $${s.cost.est_usd.toFixed(2)} over ${s.cost.runs} runs (rate $${s.rate_usd}/run).${top}`;
  }
  const hit = s.agents.find((a) => q.includes(a.id.toLowerCase()));
  if (hit) {
    return `${hit.name} is ${hit.status} on floor ${hit.floor} (${hit.dept})${hit.task ? `, task ${hit.task.id}: ${hit.task.brief}` : ""}.`;
  }
  return "I can answer: summary, who is waiting, what is stuck, spend — or name an agent.";
}

document.querySelectorAll("[data-q]").forEach((btn) => {
  btn.onclick = () => { $("answer").textContent = answer(btn.dataset.q); };
});

// keyboard
addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") return;
  const key = e.key;
  if (key >= "1" && key <= "6") { selectFloor(Number(key)); return; }
  if (key === "0" || key === "Escape") { selectedFloor = null; selectedAgent = null; cam.floor = null; renderPanels(); return; }
  if (key === "w" || key === "W") {
    const next = (DATA.pending || []).find((p) => p.kind !== "stuck");
    if (next) { selectedAgent = next.agent; selectedFloor = (agentById(next.agent) || {}).floor || selectedFloor; cam.floor = selectedFloor; renderPanels(); }
    return;
  }
  if (key === "s" || key === "S") {
    const next = (DATA.pending || []).find((p) => p.kind === "stuck");
    if (next) { selectedAgent = next.agent; renderPanels(); }
    return;
  }
  const target = (DATA.pending || [])[0];
  if ((key === "a" || key === "A") && target) send({ action: "approve", task: target.task });
  if (key === "r" || key === "R") { if (target) act("reply", target.task); }
  if ((key === "x" || key === "X") && target) send({ action: "skip", task: target.task });
  if ((key === "f" || key === "F") && target) send({ action: "repair", task: target.task });
  if (key === "t" || key === "T") $("themeBtn").click();
  if (key === "+" || key === "=") cam.tz = Math.min(2.2, cam.tz * 1.1);
  if (key === "-") cam.tz = Math.max(0.55, cam.tz * 0.9);
});

// mobile sheets
document.querySelectorAll("#mobileTabs [data-sheet]").forEach((btn) => {
  btn.onclick = () => {
    document.body.classList.remove("sheet-left", "sheet-right");
    if (btn.dataset.sheet) document.body.classList.add("sheet-" + btn.dataset.sheet);
  };
});

// ---------- loop ----------
function frame() {
  // exponential smoothing for every camera move
  cam.zoom += (cam.tz - cam.zoom) * 0.14;
  cam.x += (cam.tx - cam.x) * 0.16;
  cam.y += (cam.ty - cam.y) * 0.16;
  drawRoom();
  requestAnimationFrame(frame);
}

async function refresh() {
  try {
    const response = await fetch("/api/scene", { cache: "no-store" });
    DATA = await response.json();
    $("clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const firstPaint = !scene.bots.length;
    if (firstPaint || scene.bots.length !== DATA.agents.length) buildScene();
    if (firstPaint) fit(false);
    DATA.agents.forEach((agent) => {
      const bot = scene.bots.find((b) => b.id === agent.id);
      if (bot) { bot.status = agent.status; bot.floor = agent.floor; bot.accent = agent.accent; }
    });
    renderPanels();
  } catch (err) {
    $("clock").textContent = "offline";
  }
}

refresh();
setInterval(refresh, 3000);
frame();
</script>
</body></html>
"""


def render(cfg, lang: str = "en") -> str:
    """The page is English by design (docs/scene-brief.md); `lang` is accepted for
    symmetry with the board and only affects the `<html lang>` attribute."""
    import json as _json

    page = PAGE.replace("__NAME__", cfg.name).replace("__WS__", str(cfg.workspace))
    page = page.replace("__LANG__", "en")
    return page
