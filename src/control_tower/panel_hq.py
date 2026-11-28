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
<meta name="color-scheme" content="light dark">
<link rel="stylesheet" href="/assets/pico.css">
<title>__NAME__ — 控制塔 HQ</title>
<style>
  :root{--page:#FAFAF8;--section:#F4F3F0;--card:#FFFFFF;--line:#D2CDC2;--ink:#1F1F24;--ink2:#6E6A61;--radius:14px;
    --dim:var(--ink2);--dim2:#8C877C;--orange:#B07A1F;--red:#B3402E;--green:#2F7D4F;--blue:#2F5FA8;--slate:#6B4FA8;
    --mono:ui-monospace,SFMono-Regular,Consolas,monospace;
    --pico-background-color:var(--page);--pico-color:var(--ink);--pico-muted-color:var(--ink2);
    --pico-muted-border-color:var(--line);--pico-card-background-color:var(--card);--pico-card-border-color:var(--line);
    --pico-card-box-shadow:none;--pico-border-radius:var(--radius);--pico-form-element-background-color:var(--card);
    --pico-form-element-border-color:var(--line);--pico-primary:#2F5FA8;--pico-primary-background:#2F5FA8;
    --pico-primary-hover-background:#274F8C}
  [data-theme="dark"]{--page:#1F1F26;--section:#26262F;--card:#2C2C36;--line:#3B3B48;--ink:#ECEAE4;--ink2:#A9A5A0;--dim2:#7C7884;color-scheme:dark}
  *{box-shadow:none!important;text-shadow:none!important}
  html body{background:var(--page);color:var(--ink)}
  main.container{padding-block:24px}
  header{gap:12px;align-items:center;flex-wrap:wrap;background:none;border:0;margin-bottom:8px}
  header h1{font-size:24px;margin:0;color:var(--ink)}
  .kpis{display:flex;gap:8px;flex-wrap:wrap;margin:0}
  .kpi{min-width:72px;padding:8px 12px;background:var(--section);border:1px solid var(--line);border-radius:var(--radius);text-align:center}
  .kpi b{display:block;font-variant-numeric:tabular-nums;color:var(--ink)}
  .kpi span{font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--ink2)}
  article{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:16px}
  table{width:100%;font-size:13px;background:var(--card);border:1px solid var(--line);border-radius:var(--radius);border-collapse:separate;border-spacing:0;overflow:hidden}
  th{background:var(--section);color:var(--ink);text-align:left}
  th,td{padding:5px 8px;border-bottom:1px solid var(--line)}
  tr:last-child td{border-bottom:0}
  .tag,.state{display:inline-block;min-height:20px;padding:0 8px;border:0;border-radius:var(--radius);background:var(--section);color:#fff;font:600 11px/20px var(--mono);letter-spacing:.4px;text-transform:uppercase;text-align:center}
  .tag.ready,.tag.accepted,.state.ready{background:#2F7D4F}
  .tag.failed,.tag.blocked,.state.failed,.state.blocked{background:#B3402E}
  .tag.progress,.tag.in_progress,.state.progress,.state.in_progress{background:#2F5FA8}
  .tag.overdue,.tag.late,.tag.restating,.state.overdue,.state.late{background:#B07A1F}
  .tag.reported,.state.reported{background:#6B4FA8}
  .id,.term,.countdown b,.tag,.botrow .tail{font-family:var(--mono)}
  .askbox{display:flex;gap:8px;margin:16px 0 4px;padding:12px;background:var(--card);border:1px solid var(--line);border-radius:var(--radius)}
  .askbox input{flex:1;margin:0;background:var(--page);border:1px solid var(--line);border-radius:var(--radius)}
  .askbox button{width:auto;margin:0;padding-inline:16px;background:#2F5FA8;border:0;border-radius:10px}
  .askbox button:hover{background:#28508D}
  .askbox button:disabled{background:#4A4A55}
  .logochip{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;background:var(--section);border:1px solid var(--line);border-radius:8px;color:var(--ink);font:600 13px/1 var(--mono)}
  .logochip.big{width:32px;height:32px;font-size:14px}
  .seg{display:flex;gap:4px;align-items:center}
  .seg a,.seg button{width:auto;margin:0;padding:2px 8px;background:transparent;border:1px solid var(--line);border-radius:8px;color:var(--ink2);font:600 11px/18px var(--mono);text-decoration:none}
  .seg .on,html[lang^="zh"] .seg a[data-lang="zh"],html[lang="en"] .seg a[data-lang="en"]{background:#2F5FA8;border-color:#2F5FA8;color:#fff}
  .sub,.live,.label,.empty,.how{color:var(--ink2)}
  .label{font-size:11px;letter-spacing:1px;text-transform:uppercase;margin:16px 0 8px;color:var(--ink2)}
  .dot{display:inline-block;width:8px;height:8px;border-radius:var(--radius)}
  .botrow{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:12px;margin-bottom:8px}
  .botrow .body{display:none}
  .botrow[open] .body{display:block}
  .dock{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:24px;padding:16px;background:var(--section);border:1px solid var(--line);border-radius:var(--radius)}
  .line{display:flex;gap:8px;align-items:baseline;font-size:13px;padding:4px 0;border-bottom:1px solid var(--line)}
  .line .what2{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .bar{height:4px;background:var(--line);margin-top:8px}
  .bar i{display:block;height:100%;width:0;background:var(--ink2)}
  [data-theme="dark"] body::before{content:"";position:fixed;inset:0;z-index:1;pointer-events:none;opacity:.3;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")}
  main,footer{position:relative;z-index:2}
</style>
</head>
<body>
<main class="container">
  <header>
    <span class="logochip" id="mark" aria-hidden="true">__MARK__</span>
    <span>
      <h1 id="title">__NAME__</h1>
      <div class="sub">值守台 · 一眼看清：谁在干、谁要你决定、哪些塔自己会处理</div>
    </span>
    <span class="grow"></span>
    <span class="kpis" id="kpis"></span>
    <span class="seg">
      <a href="/swarm">蜂群</a>
      <a href="?lang=zh" data-lang="zh">中文</a>
      <a href="?lang=en" data-lang="en">EN</a>
      <button type="button" data-theme-choice="dark">暗</button>
      <button type="button" data-theme-choice="light">亮</button>
    </span>
  </header>

  <form class="askbox" id="askform">
    <input id="asktext" name="text" placeholder="问塔台：谁卡住了？" autocomplete="off" required>
    <button type="submit">发送</button>
  </form>
  <p id="askmsg" role="status"></p>

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
      <article class="coord" id="coord"></article>
      <div class="label">项目 PROJECTS · 一个塔 = 一个工作区，点一个只看它</div>
      <table>
        <thead><tr><th>项目</th><th>Bot</th><th>未完成</th><th>需要你</th><th>逾期</th></tr></thead>
        <tbody id="teams"></tbody>
      </table>
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
      <section id="bots"></section>
      <details class="how" style="margin-top:14px">
        <summary style="font:600 10.5px var(--mono);letter-spacing:.12em;color:var(--dim2)">统计 STATS · 验收率 / 周期 / 每日吞吐（复盘时再看）</summary>
        <div class="business" id="business" style="margin-top:8px"></div>
      </details>
      <div class="label">Ledger · 操作日志</div>
      <article style="max-height:220px;overflow:auto">
        <div id="log" style="font-size:.8rem;color:var(--pico-muted-color)"></div>
      </article>
    </div>
  </div>
</main>

<footer class="dock">
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
</footer>

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
const paintTheme = () => document.querySelectorAll("[data-theme-choice]").forEach((b) =>
  b.classList.toggle("on", b.dataset.themeChoice === document.documentElement.getAttribute("data-theme")));
document.documentElement.setAttribute("data-theme", QS.get("theme") || localStorage.getItem(THEME) || "dark");
paintTheme();
document.querySelectorAll("[data-theme-choice]").forEach((b) => {
  b.onclick = () => {
    document.documentElement.setAttribute("data-theme", b.dataset.themeChoice);
    localStorage.setItem(THEME, b.dataset.themeChoice);
    paintTheme();
  };
});

$("askform").onsubmit = async (event) => {
  event.preventDefault();
  const text = $("asktext").value.trim();
  if (!text) return;
  $("askmsg").textContent = "转发中…";
  try {
    const r = await fetch("/api/ask", {method: "POST", headers: {"Content-Type": "application/json"},
                                       body: JSON.stringify({text})});
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
    $("askmsg").textContent = `→ ${data.role} · ${data.task}`;
    $("asktext").value = "";
    load();
  } catch (err) {
    $("askmsg").textContent = "转发失败：" + (err.message || err);
  }
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
  const row = (key, colour, label, bots, open, needs, late, pressed) =>
    `<tr data-p="${esc(key)}"${pressed ? ' aria-selected="true"' : ""}>
      <td><i class="dot" style="background:${esc(colour)}"></i> ${label}</td>
      <td>${bots}</td><td>${open}</td><td>${needs}</td><td>${late}</td></tr>`;
  const rows = state.projects.map((p) => row(p.name, p.color || "#64748b",
    p.ok ? esc(p.name) : `${esc(p.name)} <small>${esc(L.broken)}</small>`,
    p.bots, p.open, p.needs_you || 0, p.overdue || 0, p.name === active));
  const allRow = `<tr data-p="__all__"${active === "__all__" ? ' aria-selected="true"' : ""}>
    <td><i class="dot" style="background:#64748b"></i> ${esc(L.all)} ${totals.projects ?? 0}</td>
    <td>${totals.bots ?? 0}</td><td>${totals.open ?? 0}</td>
    <td>${totals.needs_you ?? 0}</td><td>${totals.overdue ?? 0}</td></tr>`;
  $("teams").innerHTML = allRow + rows.join("");
  $("teams").querySelectorAll("[data-p]").forEach((el) => {
    el.onclick = () => { state.project = el.dataset.p; load(); };
  });
}

function nextLine(next) {
  if (!next || !next.due) return "";
  const due = String(next.due);
  return `<div class="countdown"><span>下一次到期</span><b>${esc(parseDue(due) ? countdownTo(due) : due)}</b></div>`;
}

function drawCoord() {
  if (state.project === "__all__") {
    const t = state.overview?.totals || {};
    $("coord").innerHTML = `
      <span class="logochip big">${esc((L.all || "A").slice(0, 1).toUpperCase())}</span>
      <div class="nm">${esc(L.all)} · ${t.projects ?? 0} ${esc(L.projects)}</div>
      <div class="desc">${esc(L.bots)} ${t.bots ?? 0} · ${esc(L.open)} ${t.open ?? 0} · ${esc(L.accepted)} ${t.accepted ?? 0}</div>
      <div class="countdown"><span>${esc(L.overdue)}</span><b>${t.overdue ?? 0}</b></div>`;
    return;
  }
  const proj = state.projects.find((p) => p.name === state.project);
  const next = nextDue();
  $("coord").innerHTML = `
    <span class="logochip big">${esc((state.project || "—").trim().slice(0, 1).toUpperCase())}</span>
    <div class="nm">${esc(state.project || "—")}</div>
    <div class="desc">coordinator · 管 ${proj?.bots ?? 0} 个 bot · ${proj?.path ?? ""}</div>
    ${nextLine(next)}`;
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
  const claimed = s.counts?.attention_claimed ?? 0;
  const staleCount = s.counts?.stale ?? 0;
  const resolved = s.counts?.attention_resolved ?? 0;
  const issuesBit = (claimed || resolved)
    ? ' · <span style="color:var(--dim)">处理中 <b>' + claimed + '</b> · 已处理 <b>'
      + resolved + '</b></span>'
    : '';
  el.innerHTML =
    '<span style="color:var(--orange)">◐</span> 待总控 <b>' + lead +
    '</b> 项（塔自己验收入库 / 打回重试） · <span style="color:var(--red)">●</span> 需要你 <b>' +
    person + '</b> 项（blocked/failed 且重试用尽）' + issuesBit
    + (staleCount ? ' · <span style="color:var(--dim)">陈旧 <b>' + staleCount
                     + '</b>（不在待总控内）</span>' : '');
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
    page = page.replace("__MARK__", (display_name(cfg)[:1] or "T").upper())
    page = page.replace("__LANG__", "zh-CN" if lang == "zh" else "en")
    page = page.replace("__LABELS__", _json.dumps(LABELS.get(lang, LABELS["en"]), ensure_ascii=False))
    if lang == "zh":
        for english, chinese in ZH.items():
            page = page.replace(english, chinese)
    return page
