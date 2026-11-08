"""The HQ page — dark, dense, and readable at a glance.

Design borrowed from the 值守台HQ reference the user pointed at ("抄袭，要科技感"):

* near-black background with a faint grid, one big coordinating robot on the left,
  small robots for every team on the right;
* left column = project (team) list with a health dot-string and `ok/total`;
* right column = one row per bot: coloured team dot, bot name, **what it is doing
  right now** in bold, task id + age, running badge, chevron to expand its tasks;
* top right = small KPI cards; under the header a black terminal strip;
* bottom fixed = NOW (needs you) and NEXT (deadlines) panels, rotating;
* colour is identity, red is only ever "needs you".
"""

from __future__ import annotations

from pathlib import Path

PAGE = r"""<!doctype html>
<html lang="__LANG__" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__NAME__ — 控制塔 HQ</title>
<style>
  :root{
    --bg:#05080d; --bg2:#070b12; --card:#0b1119; --card2:#0e1520; --ink:#e9eef6;
    --dim:#7d8aa0; --dim2:#55627a; --line:rgba(120,150,200,.14); --line2:rgba(120,150,200,.22);
    --blue:#3b82f6; --pink:#ec4899; --orange:#f59e0b; --green:#22c55e; --teal:#14b8a6;
    --slate:#64748b; --red:#ef4444;
    --mono:"Cascadia Mono",Consolas,ui-monospace,monospace;
    --sans:"Segoe UI","PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  }
  html[data-theme="light"]{
    --bg:#eef1f8; --bg2:#f6f3fa; --card:#fff; --card2:#fff; --ink:#14161f; --dim:#5c6478;
    --dim2:#8b93a7; --line:rgba(24,30,56,.10); --line2:rgba(24,30,56,.18);
  }
  *{box-sizing:border-box}
  html,body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 var(--sans)}
  body{
    background-image:
      radial-gradient(1100px 520px at 18% -8%, rgba(59,130,246,.16), transparent 60%),
      radial-gradient(900px 460px at 92% 0%, rgba(236,72,153,.10), transparent 55%),
      linear-gradient(var(--line) 1px, transparent 1px),
      linear-gradient(90deg, var(--line) 1px, transparent 1px);
    background-size: auto, auto, 44px 44px, 44px 44px;
    background-color: var(--bg);
    padding-bottom:170px;
  }
  .wrap{max-width:1520px;margin:0 auto;padding:18px 22px 26px}
  /* ---------- header ---------- */
  header{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
  .mark{width:46px;height:46px;display:grid;place-items:center;flex:none}
  h1{font-size:23px;margin:0;letter-spacing:-.02em;font-weight:750}
  .sub{color:var(--dim);font-size:12px;margin-top:1px}
  .live{display:flex;align-items:center;gap:8px;margin-top:10px;color:var(--dim);font:12px var(--mono)}
  .live .pulse{width:9px;height:9px;border-radius:50%;background:var(--green);
                box-shadow:0 0 10px var(--green);animation:pulse 2s infinite}
  .grow{flex:1}
  .kpis{display:flex;gap:8px;flex-wrap:wrap}
  .kpi{min-width:74px;padding:7px 11px;border:1px solid var(--line);border-radius:10px;
       background:linear-gradient(180deg,var(--card2),var(--card));text-align:center}
  .kpi b{display:block;font:17px/1.2 var(--mono);letter-spacing:-.02em}
  .kpi span{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim)}
  .kpi.alarm b{color:var(--red)}
  .iconbtn{height:32px;width:32px;border-radius:9px;border:1px solid var(--line);
           background:var(--card);color:var(--ink);cursor:pointer;font-size:14px}
  /* ---------- terminal strip ---------- */
  .term{margin:14px 0 0;background:#02050a;border:1px solid var(--line2);border-radius:12px;
        padding:10px 14px;font:12.5px/1.5 var(--mono);color:#cfd8e6;
        display:flex;align-items:center;gap:10px;overflow:hidden}
  .term .prompt{color:var(--green)}
  .term .cmd{color:#8ab4ff}
  .term .hash{color:var(--dim2)}
  .term .body{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .term .lights{display:flex;gap:6px;flex:none}
  .term .lights i{width:9px;height:9px;border-radius:50%}
  .cur{display:inline-block;width:7px;height:14px;background:#cfd8e6;vertical-align:-2px;
       animation:blink 1s steps(2) infinite}
  /* ---------- layout ---------- */
  .grid{display:grid;grid-template-columns:322px 1fr;gap:18px;margin-top:18px;align-items:start}
  @media (max-width:1040px){.grid{grid-template-columns:1fr}}
  .panel{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line);
         border-radius:16px;padding:14px}
  /* ---------- left: coordinator ---------- */
  .coord{display:flex;flex-direction:column;align-items:center;gap:6px;padding:16px 12px 14px}
  .coord .big{margin:2px 0 6px;filter:drop-shadow(0 0 18px rgba(59,130,246,.35))}
  .coord .nm{font:700 13px var(--mono);letter-spacing:.14em}
  .coord .desc{color:var(--dim);font-size:12px}
  .countdown{margin-top:10px;width:100%;border:1px solid var(--line2);border-radius:10px;
             padding:8px;text-align:center;background:#04070d}
  .countdown span{display:block;color:var(--dim);font-size:11px;letter-spacing:.06em}
  .countdown b{display:block;font:600 20px var(--mono);color:#ffd479;margin-top:2px}
  .label{font:600 10.5px var(--mono);letter-spacing:.16em;text-transform:uppercase;
         color:var(--dim2);margin:16px 4px 8px}
  .team{display:grid;grid-template-columns:10px 1fr auto;gap:9px;align-items:center;
        padding:8px 10px;border-radius:10px;border:1px solid transparent;cursor:pointer}
  .team:hover{background:rgba(120,150,200,.07)}
  .team[aria-pressed="true"]{background:rgba(59,130,246,.12);border-color:rgba(59,130,246,.35)}
  .team .dot{width:10px;height:10px;border-radius:50%;box-shadow:0 0 8px currentColor}
  .team .nm{font-weight:650;font-size:13px}
  .team .meta{color:var(--dim);font:11px var(--mono)}
  .dots{display:flex;gap:3px;margin-top:3px}
  .dots i{width:5px;height:5px;border-radius:50%;background:var(--line2)}
  .dots i.ok{background:var(--green)} .dots i.run{background:var(--orange)}
  .dots i.bad{background:var(--red)} .dots i.warn{background:#eab308}
  .tag{font:600 10px var(--mono);padding:2px 7px;border-radius:5px;letter-spacing:.05em}
  .tag.ready{background:rgba(34,197,94,.16);color:#4ade80}
  .tag.blocked,.tag.failed{background:rgba(239,68,68,.16);color:#f87171}
  .tag.progress{background:rgba(59,130,246,.16);color:#7dd3fc}
  .tag.restating{background:rgba(245,158,11,.16);color:#fbbf24}
  .tag.overdue{background:rgba(239,68,68,.16);color:#f87171}
  /* ---------- right: bot rows ---------- */
  .auto{padding:10px 14px;border:1px solid var(--line);border-radius:12px;background:#04070d;
        color:#c7d2e3;font-size:13px;margin-bottom:12px}
  .auto .ok{color:var(--green)}
  .botrow{border:1px solid var(--line);border-radius:14px;background:linear-gradient(180deg,var(--card2),var(--card));
          margin-bottom:9px;overflow:hidden}
  .botrow .head{display:grid;grid-template-columns:52px 1fr auto;gap:12px;align-items:center;
                padding:10px 14px;cursor:pointer}
  .botrow .head:hover{background:rgba(120,150,200,.05)}
  .botrow .info{min-width:0}
  .botrow .top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .botrow .who{font-weight:700;font-size:13.5px}
  .botrow .what{margin-top:3px;font-weight:650;font-size:13.5px;color:#dbe6f5;
               overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  html[data-theme="light"] .botrow .what{color:#1f2937}
  .botrow .what.empty{font-weight:500;color:var(--dim)}
  .botrow .tail{display:flex;align-items:center;gap:12px;color:var(--dim);font:11px var(--mono)}
  .running{display:flex;align-items:center;gap:6px;color:#cfd8e6}
  .running b{font:600 13px var(--mono)}
  .chev{color:var(--dim2);transition:transform .2s}
  .botrow[open] .chev{transform:rotate(180deg)}
  .botrow .body{display:none;padding:0 14px 12px 78px;border-top:1px solid var(--line)}
  .botrow[open] .body{display:block}
  .taskline{padding:8px 0;border-bottom:1px dashed var(--line)}
  .taskline:last-child{border-bottom:0}
  .taskline .t1{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
  .taskline .id{font:11px var(--mono);color:var(--dim)}
  .taskline .brief{font-size:12.5px;margin-top:3px;color:#c7d2e3}
  html[data-theme="light"] .taskline .brief{color:#374151}
  .empty{color:var(--dim);font-size:13px;padding:10px 2px}
  /* ---------- business strip ---------- */
  .business{display:flex;gap:22px;flex-wrap:wrap;margin-top:6px;padding:12px 14px;
            border:1px solid var(--line);border-radius:12px;background:#04070d}
  .business .k b{display:block;font:16px var(--mono)}
  .business .k span{color:var(--dim);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase}
  /* ---------- bottom dock ---------- */
  .dock{position:fixed;left:0;right:0;bottom:0;display:grid;grid-template-columns:1fr 1fr;gap:14px;
        padding:10px 22px 12px;background:linear-gradient(180deg,rgba(5,8,13,.9),#05080d);
        border-top:1px solid var(--line2);backdrop-filter:blur(6px)}
  html[data-theme="light"] .dock{background:linear-gradient(180deg,rgba(238,241,248,.94),#eef1f8)}
  .dock .col{min-width:0}
  .dock .hdr{display:flex;align-items:baseline;gap:10px;margin-bottom:5px}
  .dock .hdr b{font:700 11px var(--mono);letter-spacing:.18em;color:var(--dim)}
  .dock .hdr span{color:var(--dim2);font-size:11px}
  .dock .line{display:flex;align-items:center;gap:10px;padding:5px 0;font:12.5px var(--mono);
              border-bottom:1px solid var(--line)}
  .dock .line .t{color:var(--dim2);width:58px;flex:none}
  .dock .line .what2{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#cfd8e6}
  html[data-theme="light"] .dock .line .what2{color:#374151}
  .dock .line .when{color:var(--dim);flex:none}
  .dock .bar{height:2px;background:var(--line);margin-top:6px;border-radius:2px;overflow:hidden}
  .dock .bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--blue),var(--green))}
  @keyframes fill{from{width:0}to{width:100%}}
  @keyframes pulse{50%{opacity:.35}}
  @keyframes blink{50%{opacity:0}}
  .bot .face{fill:var(--card);stroke:rgba(150,180,220,.35);stroke-width:1.2}
  .bot .e{fill:#cfe3ff}
  .bot .ant{fill:var(--c)} .bot .cheek{fill:var(--c);opacity:.35}
  .bot .ant-l{stroke:rgba(150,180,220,.35);stroke-width:1.4;stroke-linecap:round}
  .bot .xeye{stroke:#ef4444;stroke-width:2.4;stroke-linecap:round}
  .bot.st-working .ant{animation:antblink .5s steps(2) infinite}
  .bot.st-working{filter:drop-shadow(0 0 9px var(--c))}
  @keyframes antblink{to{opacity:.25}}
  @media (prefers-reduced-motion:reduce){*{animation:none !important}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <span class="mark" id="mark"></span>
    <span>
      <h1 id="title">__NAME__</h1>
      <div class="sub">值守台 · 一眼看清：谁在干、谁要你决定、哪些塔自己会处理</div>
    </span>
    <span class="grow"></span>
    <span class="kpis" id="kpis"></span>
    <a href="?lang=zh" style="color:var(--dim);font:11px var(--mono);text-decoration:none">中文</a>
    <span style="color:var(--dim2)">/</span>
    <a href="?lang=en" style="color:var(--dim);font:11px var(--mono);text-decoration:none">EN</a>
    <button class="iconbtn" id="theme" title="theme">◐</button>
  </header>

  <div class="live">
    <span class="pulse"></span>
    <span id="live">LIVE</span>
    <span>· 快照 <span id="clock">—</span>（每 5 秒刷新）</span>
  </div>

  <div class="term">
    <span class="prompt">~$</span><span class="cmd">hq</span><span class="hash">next</span>
    <span class="body" id="term">loading…</span>
    <span class="lights"><i style="background:#ef4444"></i><i style="background:#f59e0b"></i><i style="background:#22c55e"></i></span>
  </div>

  <div class="grid">
    <div>
      <div class="panel coord" id="coord"></div>
      <div class="label">项目 PROJECTS · 一个塔 = 一个工作区，点一个只看它</div>
      <div id="teams"></div>
    </div>
    <div>
      <div class="auto" id="auto">规则：待总控 = 已交付待验收 / 打回重试中（塔自己收口）· 需要你 = blocked/failed 且重试用尽</div>
      <div class="label"><span style="color:var(--dim2)">BOTS</span> · 角色 — 谁在干什么
        <details class="how" style="display:inline-block;margin-left:10px;text-transform:none;letter-spacing:0">
          <summary>这是什么？点开看规则</summary>
          <div style="font:12px/1.7 var(--sans);color:var(--dim);max-width:620px;margin-top:6px">
            <b>进行中</b>：bot 正在干活，不用管。<br>
            <b>待总控</b>：已交付 / 打回重试中 —— 塔会自己验收、自己看着办，<b>不用你动手</b>。<br>
            <b>需要你</b>：只有 <code>blocked</code>（卡住要你拍板）或 <code>failed</code>（重试用尽）才会出现，点开有具体问题。<br>
            <b>已完成</b>：验收通过，只在「业务」里计数，不再占队列。<br>
            <span style="color:var(--dim2)">每个数字都来自 .tower/ 里的任务文件；这套看板只读，不改任何东西。</span>
          </div>
        </details>
      </div>
      <div id="bots"></div>
      <details class="how" style="margin-top:14px">
        <summary style="font:600 10.5px var(--mono);letter-spacing:.12em;color:var(--dim2)">统计 STATS · 验收率 / 周期 / 每日吞吐（复盘时再看）</summary>
        <div class="business" id="business" style="margin-top:8px"></div>
      </details>
      <div class="label">Ledger · 操作日志</div>
      <div class="panel" style="max-height:220px;overflow:auto">
        <div id="log" style="font:11.5px/1.7 var(--mono);color:var(--dim)"></div>
      </div>
    </div>
  </div>
</div>

<div class="dock">
  <div class="col">
    <div class="hdr"><b>NOW · 需要你处理</b><span id="nowcount"></span>
      <span style="color:var(--dim2);font-size:11px">只有你能做的决定</span></div>
    <div id="now"></div>
    <div class="bar"><i id="nowbar"></i></div>
  </div>
  <div class="col">
    <div class="hdr"><b>NEXT · 进行中 / 到期</b><span id="nextcount"></span>
      <span style="color:var(--dim2);font-size:11px">塔自己会推进，不需要你</span></div>
    <div id="next"></div>
    <div class="bar"><i id="nextbar"></i></div>
  </div>
</div>

<script>
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const $ = (id) => document.getElementById(id);
const L = __LABELS__;
// Never fail silently: a JS error is shown in the terminal strip, which is the
// first thing a person reads, instead of leaving an empty page behind.
window.addEventListener("error", (event) => {
  const el = document.getElementById("term");
  if (el) el.textContent = "panel error: " + (event.message || event.error || "unknown");
});
const QS = new URLSearchParams(location.search);
const THEME = "tower-hq-theme";
document.documentElement.setAttribute("data-theme", QS.get("theme") || localStorage.getItem(THEME) || "dark");
$("theme").onclick = () => {
  const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem(THEME, next);
};

const TEAM_COLOURS = ["#3b82f6", "#ec4899", "#f59e0b", "#22c55e", "#14b8a6", "#64748b", "#a855f7", "#06b6d4"];

function bot(state, colour, size) {
  const cls = state === "working" ? "st-working" : state === "failed" ? "st-failed" : "st-ok";
  const eye = state === "failed"
    ? `<path class="xeye" d="M15 19l5 5M20 19l-5 5M29 19l5 5M34 19l-5 5"/>`
    : `<g><rect class="e" x="15" y="17" width="5" height="9" rx="2.5"/>
       <rect class="e" x="28" y="17" width="5" height="9" rx="2.5"/></g>`;
  return `<svg class="bot ${cls}" width="${size}" height="${size}" viewBox="0 0 48 48" style="--c:${colour}">
    <g><line class="ant-l" x1="24" y1="10" x2="24" y2="5"/><circle class="ant" cx="24" cy="4" r="2.8"/>
    <rect class="face" x="7" y="10" width="34" height="31" rx="10"/>
    ${eye}
    <circle class="cheek" cx="13" cy="32" r="2.6"/><circle class="cheek" cx="35" cy="32" r="2.6"/></g></svg>`;
}

const DEFAULT_PROJECT = "__PROJECT__";
let state = { project: QS.get("project") || DEFAULT_PROJECT, projects: [], overview: null, snapshot: null, stats: null, instance: "" };

// The panel restarts itself when the installed code changes; the page follows it
// instead of showing numbers from the previous build.
async function checkInstance() {
  try {
    const h = await (await fetch("/api/health", { cache: "no-store" })).json();
    if (state.instance && h.instance && h.instance !== state.instance) location.reload();
    state.instance = h.instance || state.instance;
  } catch (err) {
    // mid-restart: the next tick picks the new instance up
  }
}
let nowTimer = null, nowIndex = 0;

function dotString(project) {
  const total = Math.max(project.bots || 0, 1);
  const bad = project.overdue || 0;
  const need = project.needs_you || 0;
  const run = project.working || 0;
  const cells = [];
  for (let i = 0; i < Math.min(total, 12); i += 1) {
    let cls = "ok";
    if (i < bad) cls = "bad"; else if (i < bad + need) cls = "warn"; else if (i < bad + need + run) cls = "run";
    cells.push(`<i class="${cls}"></i>`);
  }
  return cells.join("");
}

function drawHeader() {
  $("mark").innerHTML = bot("ok", "#3b82f6", 42);
  const selected = state.projects.find((p) => p.name === state.project);
  const title = state.project === "__all__" ? L.all : (selected?.name || state.project || "");
  if (title) { $("title").textContent = title; document.title = title + " — 控制塔 HQ"; }
  // Five numbers, each with the formula written down in docs/board-model.md §3.
  const t = state.overview?.totals || {};
  const snap = state.snapshot?.counts || {};
  const proj = state.projects.find((p) => p.name === state.project);
  const all = state.project === "__all__";
  const lead = all ? (t.lead ?? 0) : (snap.triage ?? 0);
  const person = snap.attention ?? 0;
  const late = all ? (t.overdue ?? 0) : (proj?.overdue ?? 0);
  const items = [
    [t.projects ?? 0, "teams", false, "登记在这台机器上的项目数（一个工作区一个塔）"],
    [all ? (t.bots ?? 0) : (proj?.bots ?? 0), "bots", false, "角色数，一个角色 = 一个 bot"],
    [all ? (t.working ?? 0) : (snap.in_flight ?? 0), "running", false, "此刻正在干活的任务数"],
    [lead, "lead_queue", lead > 0, "塔自己会收口：验收 / 打回重试 / 催办"],
    [person, "needs_you", person > 0, "只有 blocked/failed 才计数：要你给一个决定"],
  ];
  if (late > 0) items.push([late, "overdue", true, "过了期限还没交的任务"]);
  $("kpis").innerHTML = items.map(([n, label, alarm, hint]) =>
    `<div class="kpi ${alarm ? "alarm" : ""}" title="${esc(hint)}"><b>${n}</b><span>${esc(L[label] || label)}</span></div>`).join("");
}

function drawTeams() {
  const active = state.project;
  const totals = state.overview?.totals || {};
  const allChip = `
    <div class="team" data-p="__all__" aria-pressed="${active === "__all__"}">
      <i class="dot" style="background:var(--slate);color:var(--slate)"></i>
      <div>
        <div class="nm">${esc(L.all)} ${totals.projects ?? 0}</div>
        <div class="meta">${esc(L.bots)} ${totals.bots ?? 0} · ${esc(L.open)} ${totals.open ?? 0}</div>
        <div class="dots">${dotString({ bots: totals.bots, needs_you: totals.needs_you, overdue: totals.overdue, working: totals.working })}</div>
      </div>
      <span class="meta">${totals.needs_you ? totals.needs_you + " " + L.needs : ""}</span>
    </div>`;
  const rows = state.projects.map((p) => `
    <div class="team" data-p="${esc(p.name)}" aria-pressed="${p.name === active}">
      <i class="dot" style="background:${esc(p.color)};color:${esc(p.color)}"></i>
      <div>
        <div class="nm">${esc(p.name)}</div>
        <div class="meta">${p.ok ? `${p.bots} ${esc(L.bots)} · ${p.open} ${esc(L.open)}` : `<span style="color:var(--red)">${esc(L.broken)}</span>`}</div>
        <div class="dots">${dotString(p)}</div>
      </div>
      <span class="meta">${p.ok
        ? (p.needs_you ? `<span style="color:var(--red)">${esc(L.needs_you)} ${p.needs_you}</span>`
           : ((p.lead ?? 0) > 0 ? `<span style="color:var(--orange)">${esc(L.lead_queue)} ${p.lead}</span>` : "—"))
        : ""}</span>
    </div>`).join("");
  $("teams").innerHTML = allChip + rows;
  $("teams").querySelectorAll("[data-p]").forEach((el) => {
    el.onclick = () => { state.project = el.dataset.p; load(); };
  });
}

function drawCoord() {
  if (state.project === "__all__") {
    const t = state.overview?.totals || {};
    $("coord").innerHTML = `
      ${bot("ok", "#3b82f6", 96)}
      <div class="nm">${esc(L.all)} · ${t.projects ?? 0} ${esc(L.projects)}</div>
      <div class="desc">${esc(L.bots)} ${t.bots ?? 0} · ${esc(L.open)} ${t.open ?? 0} · ${esc(L.accepted)} ${t.accepted ?? 0}</div>
      <div class="countdown"><span>${esc(L.overdue)}</span><b>${t.overdue ?? 0}</b></div>`;
    return;
  }
  const proj = state.projects.find((p) => p.name === state.project);
  const colour = proj?.color || "#3b82f6";
  const next = nextDue();
  const countdown = next ? countdownTo(next.due) : "—";
  $("coord").innerHTML = `
    ${bot(next ? "ok" : "ok", colour, 96)}
    <div class="nm">${esc(state.project || "—")}</div>
    <div class="desc">coordinator · 管 ${proj?.bots ?? 0} 个 bot · ${proj?.path ?? ""}</div>
    <div class="countdown"><span>下一次到期</span><b>${esc(countdown)}</b></div>`;
}

function parseDue(text) {
  if (!text) return null;
  const m = String(text).match(/(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/);
  if (!m) return null;
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
}

function countdownTo(due) {
  const d = parseDue(due);
  if (!d) return "—";
  let s = Math.round((d - Date.now()) / 1000);
  const late = s < 0;
  s = Math.abs(s);
  const h = Math.floor(s / 3600), mi = Math.floor((s % 3600) / 60), se = s % 60;
  const text = `${String(h).padStart(2, "0")}:${String(mi).padStart(2, "0")}:${String(se).padStart(2, "0")}`;
  return late ? `逾期 ${text}` : text;
}

function allTasks() {
  const s = state.snapshot || {};
  return [...(s.attention || []), ...(s.in_flight || [])];
}

function nextDue() {
  return allTasks()
    .filter((t) => t.due && !String(t.due).startsWith("overdue"))
    .sort((a, b) => String(a.due).localeCompare(String(b.due)))[0];
}

function tasksFor(role) {
  return allTasks().filter((t) => t.role === role);
}

function drawAllProjects() {
  const projects = state.projects || [];
  $("bots").innerHTML = projects.map((p, i) => {
    const colour = p.color || TEAM_COLOURS[i % TEAM_COLOURS.length];
    return `<div class="botrow">
      <div class="head">
        ${bot(p.working ? "working" : "ok", colour, 40)}
        <div class="info">
          <div class="top"><i class="dot" style="width:8px;height:8px;border-radius:50%;background:${colour}"></i>
            <span class="who">${esc(p.name)}</span></div>
          <div class="what ${p.ok ? "" : "empty"}">${p.ok ? esc(p.path) : esc(p.error)}</div>
        </div>
        <div class="tail">
          ${p.needs_you ? `<span class="tag overdue">${esc(L.needs)} ${p.needs_you}</span>` : ""}
          ${p.overdue ? `<span class="tag overdue">${esc(L.overdue)} ${p.overdue}</span>` : ""}
          <span>${esc(L.bots)} ${p.bots}</span>
          <span>${esc(L.tasks)} ${p.tasks}</span>
          <span>${p.ok ? p.accepted + " " + L.accepted : esc(L.broken)}</span>
        </div>
      </div>
    </div>`;
  }).join("") || `<div class="empty">${esc(L.no_projects)}</div>`;
}

function drawBots() {
  if (state.project === "__all__") { drawAllProjects(); return; }
  const s = state.snapshot || {};
  const roles = s.roles || [];
  $("bots").innerHTML = roles.map((r, i) => {
    const colour = TEAM_COLOURS[i % TEAM_COLOURS.length];
    const mine = tasksFor(r.name);
    const doing = mine[0];
    const face = r.doing === "working" ? "working" : "ok";
    const tasks = mine.length
      ? mine.map((t) => `<div class="taskline">
            <div class="t1"><span class="id">${esc(t.id)}</span>
              <span class="tag ${esc(t.state || t.status)}">${esc(t.state || t.status)}</span>
              ${t.overdue ? '<span class="tag overdue">overdue</span>' : ""}
              <span class="id">${esc(t.age)} ago</span></div>
            <div class="brief">${esc(t.brief)}</div></div>`).join("")
      : `<div class="empty">${esc(L.nothing_in_flight)}</div>`;
    return `<div class="botrow" data-role="${esc(r.name)}">
      <div class="head">
        ${bot(face, colour, 40)}
        <div class="info">
          <div class="top"><i class="dot" style="width:8px;height:8px;border-radius:50%;background:${colour}"></i>
            <span class="who">${esc(r.name)}</span>
            <span class="id">${esc(r.adapter || "")}</span></div>
          <div class="what ${doing ? "" : "empty"}">${doing ? esc(doing.brief) : esc(L.all_quiet)}</div>
        </div>
        <div class="tail">
          <span class="tag ${r.doing === "working" ? "progress" : "ready"}">${r.doing === "working" ? "在干活" : "空闲"}</span>
          <span>${mine.length} ${esc(L.tasks)}</span>
          <span>${mine.filter((t) => t.status === "in_progress" || t.state === "progress").length ? "" : ""}</span>
          <span class="chev">⌄</span>
        </div>
      </div>
      <div class="body">${tasks}</div>
    </div>`;
  }).join("") || `<div class="empty">${esc(L.no_roles)}</div>`;

  $("bots").querySelectorAll(".botrow").forEach((row) => {
    row.querySelector(".head").onclick = () => row.toggleAttribute("open");
  });
}

function drawAuto() {
  const s = state.snapshot || {};
  const all = state.project === "__all__";
  const lead = all ? (state.overview?.totals?.lead ?? 0) : (s.triage || []).length;
  const person = s.counts?.attention ?? 0;
  const el = $("auto");
  if (!el) return;
  el.innerHTML =
    '<span style="color:var(--orange)">◐</span> 待总控 <b>' + lead +
    '</b> 项（塔自己验收入库 / 打回重试） · <span style="color:var(--red)">●</span> 需要你 <b>' +
    person + '</b> 项（blocked/failed 且重试用尽）';
}

function drawBusiness() {
  if (!state.stats) {
    const t = state.overview?.totals || {};
    const k0 = (value, label) => `<div class="k"><b>${esc(value)}</b><span>${esc(label)}</span></div>`;
    $("business").innerHTML = [k0(t.projects ?? 0, L.teams), k0(t.bots ?? 0, L.bots),
                               k0(t.tasks ?? 0, L.tasks), k0(t.accepted ?? 0, L.accepted),
                               k0(t.open ?? 0, L.open), k0(t.needs_you ?? 0, L.needs_you)].join("");
    return;
  }
  const b = state.stats;
  if (!b) return;
  const k = (value, label) => `<div class="k"><b>${esc(value)}</b><span>${esc(label)}</span></div>`;
  const verified = b.rates?.verified == null ? "—" : Math.round(b.rates.verified * 100) + "%";
  $("business").innerHTML = [
    k(verified, L.verified),
    k(dur(b.lead_time?.p50_s), L.lead_p50),
    k(dur(b.lead_time?.p90_s), L.lead_p90),
    k(b.totals?.accepted ?? 0, L.accepted),
    k(b.collaboration?.messages ?? 0, L.messages),
    k(b.collaboration?.huddles ?? 0, L.huddles),
  ].join("");
}

function dur(seconds) {
  if (seconds == null) return "—";
  if (seconds < 60) return Math.round(seconds) + "s";
  if (seconds < 3600) return Math.floor(seconds / 60) + "m";
  if (seconds < 86400) return Math.floor(seconds / 3600) + "h";
  return Math.floor(seconds / 86400) + "d";
}

function drawLog() {
  const recent = state.snapshot?.recent || [];
  $("log").innerHTML = recent.map((e) =>
    `<div><span style="color:var(--dim2)">${esc((e.ts || "").replace("T", " ").slice(11, 19))}</span>
      <span style="color:var(--orange)">${esc(e.event)}</span> ${esc(e.task)} ${esc(e.role)}
      <span style="color:var(--dim2)">${esc((e.note || "").slice(0, 70))}</span></div>`).join("")
    || `<div class="empty">${esc(L.no_events)}</div>`;
}

function dockLine(t, timeText) {
  return `<div class="line"><span class="t">${esc(t.id)}</span>
    <span class="tag ${esc(t.state || t.status)}">${esc(t.role)}</span>
    <span class="what2">${esc(t.brief)}</span>
    <span class="when">${esc(timeText)}</span></div>`;
}

function drawDock() {
  if (state.project === "__all__") {
    const t = state.overview?.totals || {};
    const rows = (state.projects || []).filter((p) => p.needs_you || p.overdue);
    $("nowcount").textContent = `${t.needs_you ?? 0} 项`;
    $("nextcount").textContent = `${t.overdue ?? 0} 逾期`;
    $("now").innerHTML = rows.slice(0, 2).map((p) =>
      `<div class="line"><span class="t" style="color:${esc(p.color)}">●</span>
        <span class="what2">${esc(p.name)} — ${esc(L.needs)} ${p.needs_you}</span>
        <span class="when">${esc(L.overdue)} ${p.overdue}</span></div>`).join("")
      || `<div class="line"><span class="what2">${esc(L.nothing_needs_you)}</span></div>`;
    $("next").innerHTML = (state.projects || []).slice(0, 2).map((p) =>
      `<div class="line"><span class="t" style="color:${esc(p.color)}">●</span>
        <span class="what2">${esc(p.name)}</span>
        <span class="when">${p.ok ? `${esc(L.open)} ${p.open}` : esc(L.broken)}</span></div>`).join("");
    return;
  }
  const s = state.snapshot || {};
  const attention = s.attention || [];
  const flight = [...(s.in_flight || []), ...(s.triage || [])];
  $("nowcount").textContent = attention.length ? `${attention.length} 项` : "0";
  $("nextcount").textContent = flight.length ? `${flight.length} 项` : "0";

  if (!attention.length) {
    $("now").innerHTML = `<div class="line"><span class="what2">${esc(L.nothing_needs_you)}</span></div>`;
  } else {
    const visible = attention.slice(0, 2);
    $("now").innerHTML = visible.map((t) => dockLine(t, t.age + " ago")).join("");
  }
  $("next").innerHTML = flight.length
    ? flight.slice(0, 2).map((t) => dockLine(t, t.due || t.age + " ago")).join("")
    : `<div class="line"><span class="what2">${esc(L.nothing_in_flight)}</span></div>`;

  // rotate the NOW lines every 3s when there is more than one page of them
  if (nowTimer) { clearInterval(nowTimer); nowTimer = null; }
  if (attention.length > 2) {
    nowTimer = setInterval(() => {
      nowIndex = (nowIndex + 2) % attention.length;
      const page = attention.slice(nowIndex).concat(attention).slice(0, 2);
      $("now").innerHTML = page.map((t) => dockLine(t, t.age + " ago")).join("");
    }, 3000);
  }
}

function drawTerm() {
  const s = state.snapshot || {};
  const next = nextDue();
  const t = state.overview?.totals || {};
  const all = state.project === "__all__";
  const attention = all ? (t.needs_you ?? 0) : (s.attention || []).length;
  const flying = all ? (t.working ?? 0) : (s.in_flight || []).length;
  const parts = [
    `${L.dock_now} ${countdownTo(next?.due) || "—"}`,
    `${attention} ${L.needs_you}`,
    `${flying} ${L.in_flight}`,
  ];
  $("term").innerHTML = esc(parts.join("  #  ")) + '<span class="cur"></span>';
}

async function load() {
  checkInstance();
  try {
    const overview = await (await fetch("/api/projects", { cache: "no-store" })).json();
    state.overview = overview;
    state.projects = overview.projects || [];
    const wantAll = state.project === "__all__";
    if (!wantAll && (!state.project || !state.projects.some((p) => p.name === state.project))) {
      state.project = overview.active || state.projects[0]?.name || "__all__";
    }
    if (state.project === "__all__") {
      const t = overview.totals || {};
      state.snapshot = { counts: { attention: t.needs_you || 0, in_flight: t.working || 0,
                                   total: t.tasks || 0, progress: t.working || 0 },
                        attention: [], in_flight: [], roles: [], recent: [] };
      state.stats = null;
      state.project = "__all__";
      $("clock").textContent = (overview.generated_at || "").slice(11);
      drawHeader(); drawTeams(); drawCoord(); drawBots(); drawBusiness(); drawLog(); drawDock(); drawTerm(); drawAuto();
  const sceneLink = $("sceneLink");
  if (sceneLink) sceneLink.href = "/scene?lang=" + (QS.get("lang") || "zh");
      return;
    }
    const q = state.project ? "?project=" + encodeURIComponent(state.project) : "";
    const [snap, stats] = await Promise.all([
      fetch("/api/snapshot" + q, { cache: "no-store" }).then((r) => r.json()),
      fetch("/api/stats" + q, { cache: "no-store" }).then((r) => r.json()),
    ]);
    state.snapshot = snap;
    state.stats = stats;
    $("clock").textContent = (snap.generated_at || "").slice(11);
    drawHeader(); drawTeams(); drawCoord(); drawBots(); drawBusiness(); drawLog(); drawDock(); drawTerm(); drawAuto();
  const sceneLink = $("sceneLink");
  if (sceneLink) sceneLink.href = "/scene?lang=" + (QS.get("lang") || "zh");
  } catch (err) {
    $("term").textContent = L.disconnected;
  }
}

load();
setInterval(load, 5000);
setInterval(drawCoord, 1000);
</script>
</body></html>
"""

ZH = {
    ">NOW · 需要你处理<": ">NOW · 需要你处理<",
    ">NEXT · 进行中 / 到期<": ">NEXT · 进行中 / 到期<",
    "TEAMS · 点哪队看哪队": "TEAMS · 点哪队看哪队",
    "BOTS": "BOTS",
    "Ledger · 操作日志": "Ledger · 操作日志",
    "All systems autonomous · 只有修不好的大问题才会亮红": "All systems autonomous · 只有修不好的大问题才会亮红",
    "值守台 · 每个 bot 在干嘛，一眼看全": "值守台 · 每个 bot 在干嘛，一眼看全",
    "下一次到期": "下一次到期",
    "loading…": "加载中…",
}

LABELS = {
    "en": {
        "teams": "teams", "bots": "bots", "running": "running", "tasks": "tasks",
        "needs_you": "needs you", "overdue": "overdue", "open": "open", "broken": "broken",
        "accepted": "accepted", "verified": "verified", "lead_p50": "lead p50",
        "lead_p90": "lead p90", "messages": "messages", "huddles": "huddles",
        "needs": "needs", "nothing_needs_you": "Nothing needs you.",
        "nothing_in_flight": "Nothing in flight.", "no_roles": "no roles yet",
        "no_projects": "no projects registered", "all_quiet": "all quiet — nothing in flight",
        "no_events": "no events yet", "dock_now": "now", "all": "all", "projects": "projects", "in_flight": "in flight", "lead_queue": "tower queue",
        "disconnected": "panel disconnected — is `tower panel` still running?",
    },
    "zh": {
        "teams": "项目队", "bots": "机器人", "running": "正在跑", "tasks": "任务",
        "needs_you": "需要你", "overdue": "逾期", "open": "未完成", "broken": "已损坏",
        "accepted": "已验收", "verified": "验收率", "lead_p50": "周期 P50",
        "lead_p90": "周期 P90", "messages": "消息", "huddles": "群组",
        "needs": "需要", "nothing_needs_you": "没有需要你处理的事。",
        "nothing_in_flight": "没有进行中的任务。", "no_roles": "还没有角色",
        "no_projects": "还没有注册项目", "all_quiet": "一切平静 — 没有进行中的任务",
        "no_events": "暂无事件", "dock_now": "现在", "all": "全部", "projects": "项目", "in_flight": "进行中", "lead_queue": "待总控",
        "disconnected": "面板断开 —— `tower panel` 还在运行吗？",
    },
}


def display_name(cfg) -> str:
    """What this board calls itself: the registered project name, else the config name.

    The tower.toml 
ame: is often a scaffold placeholder (my-team); a person
    reading the board wants the project they are actually working in.
    """
    try:
        from . import registry

        target = Path(cfg.workspace).resolve() if hasattr(cfg.workspace, "resolve") else Path(str(cfg.workspace)).resolve()
        for item in registry.load_projects():
            if Path(item["path"]).resolve() == target:
                return item.get("name") or cfg.name
    except Exception:
        pass
    return cfg.name


def render(cfg, lang: str = "en") -> str:
    import json as _json

    page = PAGE.replace("__NAME__", display_name(cfg)).replace("__WS__", str(cfg.workspace))
    page = page.replace("__PROJECT__", display_name(cfg))
    page = page.replace("__LANG__", "zh-CN" if lang == "zh" else "en")
    page = page.replace("__LABELS__", _json.dumps(LABELS.get(lang, LABELS["en"]), ensure_ascii=False))
    if lang == "zh":
        for english, chinese in ZH.items():
            page = page.replace(english, chinese)
    return page
