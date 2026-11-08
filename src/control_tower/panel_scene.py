"""The isometric control room: one HTML page, Canvas 2D, no build step.

Everything drawn comes from `/api/scene` — the tower's own task files. Motion
(walking, blinking, LED pulses, idle activities) is presentation, never data.

Bilingual zh/en (`?lang=`, stored in localStorage, default zh). Free-text chat is
forwarded to a real agent as a real task (`/api/ask`); the panel polls `/api/task`
for the report, so the "model on the other end" is whatever adapter the role runs.
"""

from __future__ import annotations

PAGE = r"""<!doctype html>
<html lang="zh" data-theme="dark">
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
    --bg:#eef2f8; --bg2:#e3e9f3; --panel:rgba(255,255,255,.92); --ink:#101828; --dim:#5a6b82;
    --line:rgba(16,24,40,.12); --line2:rgba(16,24,40,.22);
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;overflow:hidden;background:var(--bg);color:var(--ink);
            font:13px/1.5 var(--sans)}
  #stage{position:absolute;inset:0}
  canvas{display:block;width:100%;height:100%}
  .glass{background:var(--panel);border:1px solid var(--line);border-radius:16px;
         backdrop-filter:blur(12px);box-shadow:0 24px 60px -30px rgba(0,0,0,.8)}
  #top{position:absolute;top:14px;left:50%;transform:translateX(-50%);z-index:5;
       display:flex;align-items:center;gap:10px;padding:8px 12px;white-space:nowrap}
  #top .brand{font-weight:750;letter-spacing:-.01em}
  #top .acct{font:11px var(--mono);color:var(--dim)}
  .chip{display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border-radius:999px;
        border:1px solid var(--line);font:11px var(--mono);color:var(--dim);cursor:pointer}
  .chip b{color:var(--ink)}
  .chip.on{border-color:var(--line2);background:rgba(120,150,200,.1)}
  #err{display:none;border-color:rgba(239,68,68,.5);color:#f87171}
  #search{width:150px;padding:5px 9px;border-radius:9px;border:1px solid var(--line);
          background:transparent;color:var(--ink);font:12px var(--sans)}
  #search:focus{outline:none;border-color:var(--accent)}
  .ibtn{width:28px;height:28px;border-radius:8px;border:1px solid var(--line);
        background:transparent;color:var(--ink);cursor:pointer;font-size:13px}
  #left,#right{position:absolute;top:66px;bottom:14px;width:308px;z-index:4;
               padding:12px;overflow:auto;scrollbar-width:thin}
  #left{left:14px} #right{right:14px}
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
  .btn{border:1px solid var(--line2);background:transparent;color:var(--ink);
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
  #hint{position:absolute;left:50%;transform:translateX(-50%);bottom:16px;z-index:3;
        font:11px var(--mono);color:var(--dim);text-align:center;line-height:1.7}
  #toast{position:absolute;left:50%;top:70px;transform:translateX(-50%) translateY(-8px);z-index:9;
         padding:7px 12px;border-radius:999px;background:rgba(34,197,94,.16);
         border:1px solid rgba(34,197,94,.4);color:#4ade80;font:12px var(--mono);
         opacity:0;transition:.25s;pointer-events:none}
  #toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
  /* chat drawer */
  #chat{position:absolute;right:14px;bottom:14px;width:344px;max-width:92vw;height:430px;
        max-height:70vh;display:none;flex-direction:column;z-index:8;padding:12px}
  #chat.open{display:flex}
  #chatHead{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
  #chatLog{flex:1;overflow:auto;display:flex;flex-direction:column;gap:8px;padding-right:4px}
  .msg{max-width:85%;padding:7px 10px;border-radius:12px;font-size:12.5px;line-height:1.5;
       white-space:pre-wrap;word-break:break-word}
  .msg.you{align-self:flex-end;background:rgba(59,130,246,.18);border:1px solid rgba(59,130,246,.35)}
  .msg.bot{align-self:flex-start;background:rgba(120,150,200,.12);border:1px solid var(--line)}
  #chatQuick{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}
  #chatRow{display:flex;gap:6px}
  #chatInput{flex:1;padding:7px 10px;border-radius:9px;border:1px solid var(--line);
             background:transparent;color:var(--ink);font:12.5px var(--sans)}
  #chatInput:focus{outline:none;border-color:var(--accent)}
  #mobileTabs{position:absolute;bottom:0;left:0;right:0;display:none;z-index:6;
              border-top:1px solid var(--line);background:var(--panel);padding:8px 12px;gap:8px}
  #mobileTabs button{flex:1;padding:8px;border-radius:10px;border:1px solid var(--line);
                     background:transparent;color:var(--ink);font:12px var(--sans)}
  @media (max-width:1180px){
    #top{left:12px;right:12px;transform:none;overflow:auto}
    #left,#right{display:none}
    #mobileTabs{display:flex}
    #hint{bottom:64px}
    body.sheet-left #left,body.sheet-right #right{display:block;top:auto;bottom:56px;height:52vh}
    body.sheet-left #left{left:8px;right:8px;width:auto}
    body.sheet-right #right{left:8px;right:8px;width:auto}
    #chat{right:8px;left:8px;width:auto}
  }
</style>
</head>
<body>
<div id="stage"><canvas id="c"></canvas></div>

<div id="top" class="glass">
  <span class="brand">__NAME__</span>
  <span class="acct" data-i18n="operator">本机操作员</span>
  <span class="chip" id="clock">--:--</span>
  <span class="chip" id="fps">-- fps</span>
  <span class="chip" id="err"></span>
  <span class="chip" data-filter="working"><span data-i18n="working">工作中</span> <b id="k-working">0</b></span>
  <span class="chip" data-filter="waiting"><span data-i18n="waiting">待处理</span> <b id="k-waiting">0</b></span>
  <span class="chip" data-filter="stuck"><span data-i18n="stuck">卡住</span> <b id="k-stuck">0</b></span>
  <span class="chip" data-filter="resting"><span data-i18n="resting">空闲</span> <b id="k-resting">0</b></span>
  <span class="chip" id="k-spend"><span data-i18n="spend">花费</span> <b>$0.00</b></span>
  <input id="search" data-i18n-ph="search_ph" placeholder="搜索 agent…" autocomplete="off">
  <button class="ibtn" id="chatBtn" title="chat (C)">💬</button>
  <button class="ibtn" id="langBtn" title="language">EN</button>
  <button class="ibtn" id="themeBtn" title="theme (T)">◐</button>
  <a class="ibtn" id="boardLink" style="display:grid;place-items:center;text-decoration:none" title="值守台 board">▤</a>
  <button class="ibtn" id="fitBtn" title="fit view (0)">⤢</button>
</div>

<div id="left" class="glass">
  <div class="block"><h3 data-i18n="floors">楼层</h3><div id="floors"></div></div>
  <div class="block"><h3><span data-i18n="needs">需要你</span> <span id="pendingCount" style="color:var(--bad)"></span></h3><div id="pending"></div></div>
  <div class="block"><h3 data-i18n="scheduled">定时任务</h3><div id="scheduled"></div></div>
  <div class="block"><h3><span data-i18n="completed_today">今日已完成</span> <span id="compCount" style="color:var(--dim)"></span></h3><div id="completed"></div></div>
</div>

<div id="right" class="glass">
  <div class="block"><h3 id="ctxTitle" data-i18n="building">整栋建筑</h3><div id="ctx"></div></div>
  <div class="block">
    <h3><span data-i18n="spend">花费</span> <span style="color:var(--dim);text-transform:none;letter-spacing:0" data-i18n="estimated">（估算）</span></h3>
    <div class="kv"><span data-i18n="today">今日</span><b id="spend">$0.00</b></div>
    <div class="bar"><i id="spendBar" style="width:0%"></i></div>
    <div class="kv"><span id="budgetNote"></span><span id="topBurner"></span></div>
  </div>
  <div class="block"><h3 data-i18n="activity">动态日志</h3><div id="log"></div></div>
  <div class="block">
    <h3 data-i18n="ask">提问</h3>
    <div id="assist">
      <button class="btn" data-q="summary" data-i18n="q_summary">汇总信息</button>
      <button class="btn" data-q="waiting" data-i18n="q_waiting">谁在等待</button>
      <button class="btn" data-q="errors" data-i18n="q_stuck">哪里出错</button>
      <button class="btn" data-q="spend" data-i18n="q_spend">今日花费</button>
    </div>
    <div id="answer" data-i18n="answer_hint">可以问：汇总信息、谁在等待、哪里出错、今日花费。</div>
  </div>
</div>

<div id="chat" class="glass">
  <div id="chatHead"><b data-i18n="chat_title">对话 · 转给真实 agent</b><button class="ibtn" id="chatClose">×</button></div>
  <div id="chatLog"></div>
  <div id="chatQuick">
    <button class="btn" data-quick="summary" data-i18n="q_summary">汇总信息</button>
    <button class="btn" data-quick="waiting" data-i18n="q_waiting">谁在等待</button>
    <button class="btn" data-quick="errors" data-i18n="q_stuck">哪里出错</button>
    <button class="btn" data-quick="spend" data-i18n="q_spend">今日花费</button>
  </div>
  <div id="chatRow">
    <input id="chatInput" data-i18n-ph="chat_ph" placeholder="自由提问，会转给一个真实 agent…">
    <button class="btn primary" id="chatSend" data-i18n="send">发送</button>
  </div>
</div>

<div id="hint"></div>
<div id="toast"></div>
<div id="mobileTabs">
  <button data-sheet="left" data-i18n="tab_left">楼层与队列</button>
  <button data-sheet="right" data-i18n="tab_right">详情</button>
  <button data-sheet="" data-i18n="tab_scene">场景</button>
</div>

<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const QS = new URLSearchParams(location.search);
const MONO = '"Cascadia Mono",Consolas,ui-monospace,monospace';

// ---------- language first: everything below may use T()/LANG ----------
const I18N = {
  zh: {
    working:"工作中", waiting:"待处理", stuck:"卡住", resting:"空闲", spend:"花费",
    operator:"本机操作员", search_ph:"搜索 agent…", chat_ph:"自由提问，会转给一个真实 agent…",
    floors:"楼层", needs:"需要你", scheduled:"定时任务", completed_today:"今日已完成",
    building:"整栋建筑", estimated:"（估算）", activity:"动态日志", ask:"提问", today:"今日",
    approve:"批准", reply:"回复", skip:"跳过", repair:"维修", send:"发送",
    q_summary:"汇总信息", q_waiting:"谁在等待", q_stuck:"哪里出错", q_spend:"今日花费",
    answer_hint:"可以问：汇总信息、谁在等待、哪里出错、今日花费。",
    chat_title:"对话 · 转给真实 agent",
    nothing_pending:"没有需要你处理的事", no_deadlines:"没有到期任务",
    nothing_completed:"今天还没有完成的任务", open:"进行中", tasks:"任务",
    project:"项目", agents:"智能体", pending:"待处理", dept:"部门", model:"模型",
    status:"状态", runs_est:"运行 · 估算", current:"当前", budget:"预算", top:"最高消耗",
    no_task:"暂无任务", loading:"加载中…", offline:"离线", no_events:"暂无动态",
    tab_left:"楼层与队列", tab_right:"详情", tab_scene:"场景",
    floor_word:"楼层", agent_word:"智能体",
    forwarded:"已转发给 {role}（任务 {task}），等它回话…",
    ask_fail:"转发失败", replied:"回复",
    hint:"点击楼层聚焦 · 滚轮缩放 · 拖拽平移 · 双击适配 · 1–6 楼层 · 0 整栋 · W 待处理 · S 故障 · A/R/X 批准/回复/跳过 · F 维修 · C 对话 · T 主题",
    prompt_reply:"回复 ",
    st_working:"工作中", st_waiting:"待你处理", st_stuck:"任务卡住", st_resting:"休息中",
  },
  en: {
    working:"Working", waiting:"Waiting", stuck:"Stuck", resting:"Resting", spend:"Spend",
    operator:"local operator", search_ph:"search agents…", chat_ph:"ask anything — forwarded to a real agent…",
    floors:"Floors", needs:"Needs you", scheduled:"Scheduled", completed_today:"Completed today",
    building:"Building", estimated:"(estimated)", activity:"Activity", ask:"Ask", today:"today",
    approve:"Approve", reply:"Reply", skip:"Skip", repair:"Repair", send:"Send",
    q_summary:"Summary", q_waiting:"Who is waiting", q_stuck:"What is stuck", q_spend:"Spend",
    answer_hint:"Ask: summary, who is waiting, what is stuck, spend.",
    chat_title:"Chat · forwarded to a real agent",
    nothing_pending:"nothing pending", no_deadlines:"no deadlines",
    nothing_completed:"nothing completed today", open:"open", tasks:"tasks",
    project:"project", agents:"agents", pending:"pending", dept:"dept", model:"model",
    status:"status", runs_est:"runs · est", current:"current", budget:"budget", top:"top",
    no_task:"no current task", loading:"loading…", offline:"offline", no_events:"no events",
    tab_left:"Floors & queue", tab_right:"Details", tab_scene:"Scene",
    floor_word:"Floor", agent_word:"Agent",
    forwarded:"Forwarded to {role} (task {task}); waiting for its reply…",
    ask_fail:"forward failed", replied:"reply",
    hint:"click a floor to focus · wheel to zoom · drag to pan · double-click to fit · 1–6 floors · 0 building · W next waiting · S next stuck · A/R/X approve, reply, skip · F repair · C chat · T theme",
    prompt_reply:"Reply to ",
    st_working:"working", st_waiting:"needs you", st_stuck:"stuck", st_resting:"resting",
  },
};
const LANG_KEY = "tower-scene-lang";
let LANG = QS.get("lang") || localStorage.getItem(LANG_KEY) || "zh";
const T = (key) => (I18N[LANG] && I18N[LANG][key]) || I18N.en[key] || key;
const DEPT_ZH = { Code:"代码部", Strategy:"策略部", Content:"内容部", Growth:"增长部",
                  Quality:"质量部", Operations:"运维部", Research:"调研部", Creative:"创意部" };
const deptName = (name) => (LANG === "zh" && DEPT_ZH[name]) ? DEPT_ZH[name] : name;

// ---------- state ----------
const BUDGET = 80;
const COLOURS = { working:"#22c55e", waiting:"#f59e0b", stuck:"#ef4444", resting:"#64748b" };
let DATA = null;
let selectedFloor = null, selectedAgent = null, hoverFloor = null, filter = null, query = "";
let toastTimer = null;
const askedTasks = new Map();   // task id -> {done}
const chatLog = [];

document.documentElement.setAttribute("data-theme", QS.get("theme") || localStorage.getItem("tower-scene-theme") || "dark");
$("themeBtn").onclick = () => {
  const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("tower-scene-theme", next);
};
$("langBtn").onclick = () => {
  LANG = LANG === "zh" ? "en" : "zh";
  localStorage.setItem(LANG_KEY, LANG);
  applyI18n();
};
$("boardLink").href = "/?lang=" + LANG;

// surface page errors instead of failing silently
window.addEventListener("error", (e) => {
  const el = $("err");
  el.style.display = "inline-flex";
  el.textContent = "js: " + String(e.message || "error").slice(0, 48);
});

// ---------- camera ----------
const cam = { x: 0, y: 0, zoom: 1, tx: 0, ty: 0, tz: 1, floor: null };
const canvas = $("c");
const ctx = canvas.getContext("2d");
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

// ---------- isometric helpers (scene units; camera scales once) ----------
const TILE = 46, FLOOR_H = 62;
function iso(x, y, z) {
  return { x: (x - y) * TILE * 0.866, y: (x + y) * TILE * 0.5 - z * FLOOR_H };
}

// ---------- bots (decorative motion only) ----------
const scene = { bots: [] };
function buildScene() {
  scene.bots = [];
  if (!DATA) return;
  DATA.agents.forEach((agent, index) => {
    const x = 1 + (index % 3) * 1.05;
    const y = 1 + Math.floor(index / 3) * 1.0;
    scene.bots.push({
      id: agent.id, floor: agent.floor, accent: agent.accent, status: agent.status,
      x, y, tx: x, ty: y, nextMove: 8 + Math.random() * 14,
      phase: Math.random() * Math.PI * 2, blink: 1.5 + Math.random() * 2.5,
      act: null,
    });
  });
}

// idle activities: coffee, watering, chatting in pairs, bean bag — presentation only
const ACTS = ["coffee", "water", "chat", "lounge", "stand"];
function assignRestStates() {
  if (!DATA) return;
  const resting = scene.bots.filter((b) => b.status === "resting");
  scene.bots.forEach((b) => { if (b.status !== "resting") b.act = null; });
  const byFloor = new Map();
  resting.forEach((b) => {
    const list = byFloor.get(b.floor) || [];
    list.push(b);
    byFloor.set(b.floor, list);
  });
  const slot = Math.floor(performance.now() / 16000);
  byFloor.forEach((list) => {
    for (let i = 0; i < list.length; i += 2) {
      if (list[i + 1]) {                     // pair: chat face to face
        list[i].act = "chat"; list[i + 1].act = "chat";
        const mx = (list[i].homeX + list[i + 1].homeX) / 2;
        const my = (list[i].homeY + list[i + 1].homeY) / 2;
        list[i].tx = mx - 0.42; list[i + 1].tx = mx + 0.42;
        list[i].ty = my; list[i + 1].ty = my;
      } else {
        list[i].act = ACTS[(slot + i) % ACTS.length];
      }
    }
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

  const plaza = [iso(-2.4, -2.4, 0), iso(6.4, -2.4, 0), iso(6.4, 6.4, 0), iso(-2.4, 6.4, 0)];
  ctx.beginPath();
  plaza.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
  ctx.closePath();
  ctx.fillStyle = night ? "rgba(30,45,68,.55)" : "rgba(170,188,212,.55)";
  ctx.fill();

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
    const above = cam.floor !== null && floor.level > cam.floor;
    const lift = (hoverFloor === floor.level && cam.floor === null ? 6 : 0)
               + (above ? (floor.level - cam.floor) * 14 : 0);   // upper floors float up
    drawFloor(floor, lift, above, night);
  });
  drawLift(night);

  const sorted = [...scene.bots].sort((a, b) => (a.floor - b.floor) || (a.x + a.y) - (b.x + b.y));
  sorted.forEach((bot) => drawBot(bot, night));
  ctx.restore();
}

function quad(a, b, c, d, fill, stroke) {
  ctx.beginPath();
  [a, b, c, d].forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
  ctx.closePath();
  if (fill) { ctx.fillStyle = fill; ctx.fill(); }
  if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 0.9; ctx.stroke(); }
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
  const base = iso(2.2, 2.2, z);
  ctx.strokeStyle = night ? "rgba(160,200,250,.7)" : "rgba(70,100,140,.8)";
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(base.x, base.y); ctx.lineTo(base.x, base.y - 34); ctx.stroke();
  const on = Math.sin(performance.now() / 700) > 0;
  ctx.fillStyle = on ? "#ef4444" : "rgba(239,68,68,.25)";
  ctx.beginPath(); ctx.arc(base.x, base.y - 36, 3, 0, 7); ctx.fill();
  const nameAt = iso(1.2, 3.4, z + 0.04);
  ctx.fillStyle = night ? "rgba(190,215,250,.55)" : "rgba(50,75,110,.6)";
  ctx.font = `700 9px ${MONO}`; ctx.textAlign = "left";
  ctx.fillText((DATA && DATA.project.name || "").toUpperCase().slice(0, 22), nameAt.x, nameAt.y);
}

function drawFloor(floor, liftPx, dim, night) {
  const z = floor.level - liftPx / FLOOR_H;
  const w = 4.0, d = 4.0, pad = 0.4;
  const st = floor.status;
  const acc = (DATA.agents.find((a) => a.floor === floor.level) || {}).accent || "#3b82f6";

  ctx.save();
  ctx.globalAlpha = dim ? 0.2 : 1;

  const glassFill = night ? "rgba(96,148,214,.13)" : "rgba(168,206,244,.24)";
  const glassLine = night ? "rgba(150,195,245,.30)" : "rgba(110,150,196,.42)";
  const H = 0.95;
  quad(iso(pad, pad, z), iso(pad, d, z), iso(pad, d, z + H), iso(pad, pad, z + H), glassFill, glassLine);
  quad(iso(pad, pad, z), iso(w, pad, z), iso(w, pad, z + H), iso(pad, pad, z + H), glassFill, glassLine);
  quad(iso(pad, d, z), iso(pad, d, z + H), iso(w, d, z + H), iso(w, d, z),
       night ? "rgba(90,140,205,.05)" : "rgba(150,190,230,.10)", glassLine);
  quad(iso(w, pad, z), iso(w, d, z), iso(w, d, z + H), iso(w, pad, z + H),
       night ? "rgba(90,140,205,.05)" : "rgba(150,190,230,.10)", glassLine);
  ctx.strokeStyle = glassLine; ctx.lineWidth = 0.7;
  for (let i = 1; i < 5; i += 1) {
    const u = pad + (w - pad) * (i / 5);
    let a = iso(u, pad, z), b = iso(u, pad, z + H);
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    a = iso(pad, u, z); b = iso(pad, u, z + H);
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    a = iso(u, d, z); b = iso(u, d, z + H);
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
  }

  const slab = [iso(pad, pad, z), iso(w, pad, z), iso(w, d, z), iso(pad, d, z)];
  ctx.beginPath();
  slab.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
  ctx.closePath();
  ctx.fillStyle = night ? "#1a2740" : "#f7fafd";
  ctx.fill();
  ctx.strokeStyle = night ? "rgba(140,180,235,.55)" : "rgba(90,120,170,.5)";
  ctx.lineWidth = 1.4; ctx.stroke();
  const TH = 0.12;
  quad(iso(pad, d, z), iso(w, d, z), iso(w, d, z - TH), iso(pad, d, z - TH),
       night ? "#131e33" : "#e2e9f4", night ? "rgba(140,180,235,.35)" : "rgba(90,120,170,.35)");
  quad(iso(pad, pad, z), iso(pad, d, z), iso(pad, d, z - TH), iso(pad, pad, z - TH),
       night ? "#16233a" : "#e8eef7", night ? "rgba(140,180,235,.3)" : "rgba(90,120,170,.3)");

  const desk = (dx, dy) => {
    const p0 = iso(dx, dy, z);
    ctx.fillStyle = night ? "rgba(205,225,245,.85)" : "rgba(80,100,132,.8)";
    ctx.fillRect(p0.x - 12, p0.y - 5, 24, 7);
    ctx.fillStyle = st === "working" ? "rgba(125,211,252,.9)" : (night ? "rgba(100,130,170,.5)" : "rgba(140,160,190,.6)");
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
  } else if (dept === "Strategy") {
    prop(2.2, 2.6, 30, 10, "rgba(100,116,139,.55)", "ROUND TABLE");
  } else {
    prop(3.1, 1.2, 26, 22, "rgba(148,163,184,.35)", "DESK");
  }

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
  ctx.globalAlpha = dim ? 0.2 : 1;

  const label = iso(w + 0.18, d - 0.5, z + 0.3);
  ctx.fillStyle = night ? "rgba(210,230,252,.9)" : "rgba(30,50,80,.9)";
  ctx.font = `600 10px ${MONO}`; ctx.textAlign = "left";
  ctx.fillText(`L${floor.level} · ${deptName(floor.dept)}`, label.x + 6, label.y);
  ctx.restore();
}

function drawLift(night) {
  const levels = DATA ? DATA.floors.length : 0;
  if (!levels) return;
  const x = 4.16, y = 4.16;
  const bottom = iso(x, y, 0), top = iso(x, y, levels + 0.5);
  ctx.save();
  [-0.16, 0.16].forEach((offset) => {
    const a = iso(x + offset, y + offset, 0), b = iso(x + offset, y + offset, levels + 0.5);
    ctx.strokeStyle = night ? "rgba(150,195,245,.45)" : "rgba(90,130,180,.55)";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
  });
  const shaft = [iso(x - 0.4, y - 0.4, 0), iso(x + 0.4, y - 0.4, 0),
                 iso(x + 0.4, y - 0.4, levels + 0.5), iso(x - 0.4, y - 0.4, levels + 0.5)];
  ctx.beginPath();
  shaft.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
  ctx.closePath();
  ctx.fillStyle = night ? "rgba(110,160,220,.10)" : "rgba(170,206,242,.22)";
  ctx.fill();
  ctx.strokeStyle = night ? "rgba(150,195,245,.35)" : "rgba(90,130,180,.45)";
  ctx.lineWidth = 1; ctx.stroke();
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
  const lounge = bot.act === "lounge";
  const bob = Math.sin(now * 2 + bot.phase) * 1.5;
  const p = iso(bot.x, bot.y, bot.floor);
  ctx.save();
  ctx.globalAlpha = cam.floor !== null && bot.floor > cam.floor ? 0.22 : 1;

  // status ring
  const pulse = bot.status === "waiting" ? 1 + 0.1 * Math.sin(now * 4 + bot.phase) : 1;
  ctx.strokeStyle = COLOURS[bot.status] || COLOURS.resting;
  ctx.lineWidth = 2.4;
  ctx.beginPath(); ctx.ellipse(p.x, p.y, 12 * pulse, 5.5 * pulse, 0, 0, 7); ctx.stroke();

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

  // idle activity props (presentation only)
  if (bot.status === "resting" && bot.act === "coffee") {
    const cup = iso(bot.x + 0.38, bot.y, bot.floor);
    ctx.fillStyle = "#e2e8f0";
    ctx.beginPath(); ctx.roundRect(cup.x - 5, cup.y - 9, 10, 9, 2); ctx.fill();
    ctx.strokeStyle = "rgba(148,163,184,.9)"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(cup.x + 6, cup.y - 5, 3, -1.2, 1.2); ctx.stroke();
    for (let i = 0; i < 2; i += 1) {
      const t = (now * 0.8 + i * 0.5) % 1;
      ctx.globalAlpha = (1 - t) * 0.5;
      ctx.strokeStyle = "#cbd5e1"; ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(cup.x - 2 + i * 4 + Math.sin(t * 6) * 1.5, cup.y - 11 - t * 10);
      ctx.lineTo(cup.x - 2 + i * 4 + Math.sin(t * 6 + 1) * 1.5, cup.y - 15 - t * 10);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }
  if (bot.status === "resting" && bot.act === "water") {
    const spot = iso(bot.x + 0.42, bot.y + 0.2, bot.floor);
    ctx.fillStyle = "#475569";
    ctx.beginPath(); ctx.roundRect(spot.x - 9, spot.y - 10, 12, 8, 2); ctx.fill();
    ctx.strokeStyle = "#475569"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(spot.x + 2, spot.y - 8); ctx.lineTo(spot.x + 10, spot.y - 13); ctx.stroke();
    for (let i = 0; i < 3; i += 1) {
      const t = (now * 1.4 + i * 0.33) % 1;
      ctx.fillStyle = "rgba(125,211,252,.8)";
      ctx.beginPath(); ctx.arc(spot.x + 10 + Math.sin(i) * 2, spot.y - 12 + t * 10, 1.4, 0, 7); ctx.fill();
    }
    ctx.fillStyle = "#2f6b46";
    ctx.beginPath(); ctx.ellipse(spot.x + 10, spot.y + 1, 5, 6, 0, 0, 7); ctx.fill();
    ctx.fillStyle = "#5b4227"; ctx.fillRect(spot.x + 7, spot.y + 1, 6, 4);
  }
  if (lounge) {
    ctx.fillStyle = "rgba(148,163,184,.4)";
    ctx.beginPath(); ctx.ellipse(p.x, p.y - 4, 15, 6, 0, 0, 7); ctx.fill();
  }
  if (bot.act === "chat") {
    const on = Math.floor(now * 2) % 2 === 0;
    if (on) {
      ctx.fillStyle = "rgba(226,232,240,.9)";
      ctx.beginPath(); ctx.roundRect(p.x + 8, p.y - 46, 18, 10, 4); ctx.fill();
      ctx.fillStyle = "#0b1220"; ctx.font = `700 7px ${MONO}`; ctx.textAlign = "center";
      ctx.fillText("···", p.x + 17, p.y - 38.5);
    }
  }

  const y = p.y - 26 + bob + (lounge ? 5 : 0);
  ctx.fillStyle = "#0f172a";
  ctx.fillRect(p.x - 6, y + (lounge ? 11 : 15), 4, lounge ? 4 : 7);
  ctx.fillRect(p.x + 2, y + (lounge ? 11 : 15), 4, lounge ? 4 : 7);
  ctx.fillStyle = bot.accent;
  ctx.beginPath(); ctx.roundRect(p.x - 9, y + 4, 18, 13, 5); ctx.fill();
  ctx.fillStyle = night ? "#151b25" : "#202834";
  ctx.beginPath(); ctx.roundRect(p.x - 11, y - 12, 22, 18, 8.5); ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,.18)";
  ctx.beginPath(); ctx.ellipse(p.x - 4, y - 8.5, 6, 3.6, -0.15, 0, 7); ctx.fill();
  const closed = ((now + bot.blink) % (3.4 + (bot.phase % 1.4))) > 3.25;
  ctx.strokeStyle = "#f8fafc"; ctx.lineWidth = 2.4; ctx.lineCap = "round";
  if (closed) {
    ctx.beginPath(); ctx.moveTo(p.x - 6, y - 2); ctx.lineTo(p.x - 2, y - 2); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(p.x + 2, y - 2); ctx.lineTo(p.x + 6, y - 2); ctx.stroke();
  } else {
    ctx.beginPath(); ctx.moveTo(p.x - 6, y - 5); ctx.lineTo(p.x - 2.5, y + 1); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(p.x + 2.5, y - 5); ctx.lineTo(p.x + 6, y + 1); ctx.stroke();
  }
  ctx.strokeStyle = "#94a3b8"; ctx.lineWidth = 1.2;
  ctx.beginPath(); ctx.moveTo(p.x, y - 12); ctx.lineTo(p.x, y - 17); ctx.stroke();
  ctx.fillStyle = bot.accent;
  ctx.beginPath(); ctx.arc(p.x, y - 18.5, 2.2, 0, 7); ctx.fill();

  if (bot.status === "waiting") {
    ctx.fillStyle = "rgba(245,158,11,.95)";
    ctx.beginPath(); ctx.arc(p.x + 15, y - 22, 6, 0, 7); ctx.fill();
    ctx.fillStyle = "#0b1220"; ctx.font = `700 8px ${MONO}`; ctx.textAlign = "center";
    ctx.fillText("?", p.x + 15, y - 19.2);
  }
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
  const side = innerWidth > 1180 ? 340 : 0;
  const availW = Math.max(320, innerWidth - side * 2 - 32);
  const availH = Math.max(320, innerHeight - 150);
  const top = iso(0.4, 0.4, levels + 0.6), bottom = iso(4.0, 4.0, 0);
  const boxW = Math.abs(bottom.x - top.x) + 150;
  const boxH = Math.abs(bottom.y - top.y) + 160;
  cam.tz = Math.max(0.32, Math.min(1.5, Math.min(availW / boxW, availH / boxH)));
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

// ---------- chat drawer ----------
function openChat(open) { $("chat").classList.toggle("open", open); }
$("chatBtn").onclick = () => openChat(!$("chat").classList.contains("open"));
$("chatClose").onclick = () => openChat(false);

function appendMsg(who, text) {
  chatLog.push({ who, text });
  const el = document.createElement("div");
  el.className = "msg " + who;
  el.textContent = text;
  $("chatLog").appendChild(el);
  $("chatLog").scrollTop = $("chatLog").scrollHeight;
}

async function sendChat(text) {
  text = (text || "").trim();
  if (!text) return;
  appendMsg("you", text);
  const quick = answer(text);
  if (quick) { appendMsg("bot", quick); return; }
  try {
    const response = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const result = await response.json();
    if (!response.ok) { appendMsg("bot", "✗ " + (result.error || T("ask_fail"))); return; }
    askedTasks.set(result.task, { done: false });
    appendMsg("bot", T("forwarded").replace("{role}", result.role).replace("{task}", result.task));
  } catch (err) {
    appendMsg("bot", T("offline"));
  }
}
$("chatSend").onclick = () => { const v = $("chatInput").value; $("chatInput").value = ""; sendChat(v); };
$("chatInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { const v = $("chatInput").value; $("chatInput").value = ""; sendChat(v); }
});
document.querySelectorAll("[data-quick]").forEach((btn) => {
  btn.onclick = () => sendChat(T(btn.dataset.quick === "errors" ? "q_stuck" : "q_" + btn.dataset.quick) || btn.dataset.quick);
});

async function pollAsked() {
  for (const [task, state] of askedTasks) {
    if (state.done) continue;
    try {
      const response = await fetch("/api/task?id=" + encodeURIComponent(task), { cache: "no-store" });
      if (!response.ok) continue;
      const data = await response.json();
      if (data.summary || data.status === "accepted" || data.status === "reported") {
        state.done = true;
        const text = data.summary
          ? `${T("replied")} · ${data.role} · ${task}\n${data.summary}`
          : `${data.role} · ${task} → ${data.status}`;
        appendMsg("bot", text);
      }
    } catch (err) { /* next tick */ }
  }
}

// ---------- panels ----------
function agentById(id) { return (DATA.agents || []).find((a) => a.id === id); }

function renderPanels() {
  if (!DATA) return;
  $("k-working").textContent = DATA.kpis.working;
  $("k-waiting").textContent = DATA.kpis.waiting;
  $("k-stuck").textContent = DATA.kpis.stuck;
  $("k-resting").textContent = DATA.kpis.resting;
  $("k-spend").innerHTML = `<span>${esc(T("spend"))}</span> <b>$${DATA.cost.est_usd.toFixed(2)}</b>`;

  $("floors").innerHTML = DATA.floors.map((f) => `
    <div class="floor ${selectedFloor === f.level ? "sel" : ""}" data-level="${f.level}">
      <span class="lv">L${f.level}</span>
      <span><span class="nm">${esc(deptName(f.dept))}</span><br><span class="sub">${esc(f.id)} · ${f.open} ${esc(T("open"))}</span></span>
      <span class="pill ${f.status === "stuck" ? "bad" : f.status === "waiting" ? "warn" : f.status === "working" ? "ok" : "idle"}">
        ${esc(T("st_" + f.status))}</span>
    </div>`).join("");
  $("floors").querySelectorAll("[data-level]").forEach((el) => {
    el.onclick = () => selectFloor(Number(el.dataset.level));
  });

  const pending = DATA.pending || [];
  $("pendingCount").textContent = DATA.pending_total ? `(${DATA.pending_total})` : "";
  $("pending").innerHTML = pending.length ? pending.map((p) => `
    <div class="card ${p.kind === "stuck" ? "stuck" : ""}" data-task="${esc(p.task)}">
      <div class="who">${esc(p.agent)} · ${esc(p.kind)} · ${esc(p.task)}</div>
      <div class="why">${esc(p.reason)}</div>
      <div class="acts">
        <button class="btn primary" data-act="approve" data-task="${esc(p.task)}">${esc(T("approve"))}</button>
        <button class="btn" data-act="reply" data-task="${esc(p.task)}">${esc(T("reply"))}</button>
        <button class="btn" data-act="skip" data-task="${esc(p.task)}">${esc(T("skip"))}</button>
        <button class="btn danger" data-act="repair" data-task="${esc(p.task)}">${esc(T("repair"))}</button>
      </div>
    </div>`).join("") : `<div class="kv"><span>${esc(T("nothing_pending"))}</span></div>`;

  const sched = DATA.scheduled || [];
  $("scheduled").innerHTML = sched.length ? sched.map((s) => `
    <div class="kv"><span>${esc(s.agent)} · ${esc(s.task)}</span><b>${fmtCountdown(s.in_s)}</b></div>`).join("")
    : `<div class="kv"><span>${esc(T("no_deadlines"))}</span></div>`;

  const comp = DATA.completed_today || [];
  $("compCount").textContent = `(${comp.length})`;
  $("completed").innerHTML = comp.slice(0, 6).map((c) => `
    <div class="kv done"><span>${esc(c.agent)} · ${esc(c.task)}</span>
      <span>${c.cost.runs} · $${c.cost.est_usd.toFixed(2)}</span></div>`).join("")
    || `<div class="kv"><span>${esc(T("nothing_completed"))}</span></div>`;

  const zh = LANG === "zh";
  $("ctxTitle").textContent = selectedAgent ? `${T("agent_word")} · ${selectedAgent}`
    : selectedFloor ? `${T("floor_word")} · L${selectedFloor}` : T("building");
  if (selectedAgent) {
    const a = agentById(selectedAgent);
    const t = a && a.task;
    $("ctx").innerHTML = a ? `
      <div class="kv"><span>${esc(T("dept"))}</span><b>${esc(deptName(a.dept))}</b></div>
      <div class="kv"><span>${esc(T("model"))}</span><b>${esc(a.model)}</b></div>
      <div class="kv"><span>${esc(T("status"))}</span><b style="color:${COLOURS[a.status]}">${esc(T("st_" + a.status))}</b></div>
      <div class="kv"><span>${esc(T("tasks"))}</span><b>${a.tasks}</b></div>
      <div class="kv"><span>${esc(T("runs_est"))}</span><b>${a.cost.runs} · $${a.cost.est_usd.toFixed(2)}</b></div>
      ${t ? `<div class="card"><div class="who">${esc(T("current"))} · ${esc(t.id)}</div>
        <div class="why">${esc(t.brief)}</div>
        ${t.steps.map((s) => `<div class="kv"><span>${s.done ? "✓" : "•"}</span><span>${esc(s.label)}</span></div>`).join("")}
      </div>` : ""}` : "";
  } else if (selectedFloor) {
    const list = (DATA.agents || []).filter((a) => a.floor === selectedFloor);
    $("ctx").innerHTML = list.map((a) => `
      <div class="card" data-agent="${esc(a.id)}">
        <div class="who"><span style="color:${COLOURS[a.status]}">●</span> ${esc(a.name)}</div>
        <div class="why">${esc(a.task ? a.task.brief : T("no_task"))}</div>
      </div>`).join("");
  } else {
    $("ctx").innerHTML = `
      <div class="kv"><span>${esc(T("project"))}</span><b>${esc(DATA.project.name)}</b></div>
      <div class="kv"><span>${esc(T("agents"))}</span><b>${DATA.kpis.agents}</b></div>
      <div class="kv"><span>${esc(T("completed_today"))}</span><b>${DATA.kpis.completed_today}</b></div>
      <div class="kv"><span>${esc(T("pending"))}</span><b>${DATA.pending_total}</b></div>
      <div class="kv"><span>${esc(T("stuck"))}</span><b>${DATA.stuck_total}</b></div>`;
  }

  $("spend").textContent = "$" + DATA.cost.est_usd.toFixed(2);
  $("spendBar").style.width = Math.min(100, (DATA.cost.est_usd / BUDGET) * 100) + "%";
  $("budgetNote").textContent = `${T("budget")} $${BUDGET} · ${DATA.cost.runs} runs`;
  $("topBurner").textContent = DATA.cost.top_burner ? `${T("top")} ${DATA.cost.top_burner.agent}` : "";

  $("log").innerHTML = (DATA.log || []).slice(0, 24).map((e) => `
    <div>${esc(e.ts)} <b>${esc(e.event)}</b> ${esc(e.task)} ${esc(e.role)}</div>`).join("")
    || `<div>${esc(T("no_events"))}</div>`;

  $("pending").querySelectorAll("[data-act]").forEach((btn) => {
    btn.onclick = () => act(btn.dataset.act, btn.dataset.task);
  });
  $("ctx").querySelectorAll("[data-agent]").forEach((el) => {
    el.onclick = () => { selectedAgent = el.dataset.agent; renderPanels(); };
  });

  const match = (agent) => {
    const okFilter = !filter || agent.status === filter;
    const hay = (agent.id + " " + agent.dept + " " + (agent.task ? agent.task.brief : "")).toLowerCase();
    return okFilter && (!query || hay.includes(query));
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
  renderPanels();
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
}

async function act(action, task) {
  if (action === "reply") {
    const note = prompt(T("prompt_reply") + task);
    if (!note) return;
    return send({ action, task, note });
  }
  return send({ action, task });
}

async function send(payload) {
  const card = document.querySelector(`[data-task="${payload.task}"]`);
  try {
    const response = await fetch("/api/actions", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) { toast(result.error || "refused"); return; }
    if (card) {
      card.classList.add("done");
      card.style.maxHeight = "0px"; card.style.overflow = "hidden";
      card.style.transition = "max-height .35s ease, opacity .35s ease";
      card.style.opacity = "0";
      setTimeout(() => card.remove(), 360);
    }
    toast(`✓ ${payload.action} · ${result.outcome || "done"}`);
    refresh();
  } catch (err) {
    toast(T("offline"));
  }
}

// computed answers — every number comes from the payload
function answer(question) {
  if (!DATA) return T("loading");
  const q = String(question || "").toLowerCase();
  const s = DATA;
  const zh = LANG === "zh";
  if (q.includes("summary") || q.includes("汇总")) {
    return zh
      ? `${s.project.name}：${s.kpis.agents} 个智能体 · ${s.kpis.working} 工作中 · ${s.kpis.waiting} 待处理 · ${s.kpis.stuck} 卡住。有 ${s.pending_total} 条需要你处理，今日估算花费 $${s.cost.est_usd.toFixed(2)}。`
      : `${s.project.name}: ${s.kpis.agents} agents · ${s.kpis.working} working · ${s.kpis.waiting} waiting · ${s.kpis.stuck} stuck. ${s.pending_total} item(s) need you, $${s.cost.est_usd.toFixed(2)} est. spend today.`;
  }
  if (q.includes("wait") || q.includes("等待")) {
    if (zh) {
      const list = s.pending.map((p) => `${p.task}（${p.agent}）`).join("、");
      return list ? `等你处理：${list}。` : "现在没有需要你处理的事。";
    }
    const list = s.pending.map((p) => `${p.task} (${p.agent})`).join(", ");
    return list ? `Waiting on you: ${list}.` : "Nothing is waiting on you.";
  }
  if (q.includes("stuck") || q.includes("error") || q.includes("错")) {
    const stuck = s.pending.filter((p) => p.kind === "stuck");
    if (zh) return stuck.length ? `卡住的：${stuck.map((p) => `${p.task} — ${p.reason}`).join("；")}` : "现在没有卡住的任务。";
    return stuck.length ? `Stuck: ${stuck.map((p) => `${p.task} — ${p.reason}`).join(" | ")}` : "Nothing is stuck right now.";
  }
  if (q.includes("spend") || q.includes("cost") || q.includes("花")) {
    const top = s.cost.top_burner ? (zh ? ` 消耗最高：${s.cost.top_burner.agent}。` : ` Top burner: ${s.cost.top_burner.agent}.`) : "";
    return zh
      ? `今日估算花费 $${s.cost.est_usd.toFixed(2)}，共 ${s.cost.runs} 次运行（单价 $${s.rate_usd}/次）。${top}`
      : `Estimated spend today: $${s.cost.est_usd.toFixed(2)} over ${s.cost.runs} runs (rate $${s.rate_usd}/run).${top}`;
  }
  const hit = s.agents.find((a) => q.includes(a.id.toLowerCase()));
  if (hit) {
    return zh
      ? `${hit.name} 正在「${T("st_" + hit.status)}」，位于 ${hit.floor} 层（${deptName(hit.dept)}）${hit.task ? `，任务 ${hit.task.id}：${hit.task.brief}` : ""}。`
      : `${hit.name} is ${hit.status} on floor ${hit.floor} (${hit.dept})${hit.task ? `, task ${hit.task.id}: ${hit.task.brief}` : ""}.`;
  }
  return null;   // no computed answer: the chat drawer forwards it to a real agent
}

document.querySelectorAll("[data-q]").forEach((btn) => {
  btn.onclick = () => {
    const value = answer(btn.dataset.q);
    $("answer").textContent = value || T("answer_hint");
    if (!value) openChat(true);
  };
});

// keyboard
addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") return;
  const key = e.key;
  if (key === "c" || key === "C") { openChat(!$("chat").classList.contains("open")); return; }
  if (key === "Escape") { if ($("chat").classList.contains("open")) { openChat(false); return; } }
  if (key >= "1" && key <= "6") { selectFloor(Number(key)); return; }
  if (key === "0") { selectedFloor = null; selectedAgent = null; cam.floor = null; renderPanels(); return; }
  if (key === "w" || key === "W") {
    const next = (DATA.pending || []).find((p) => p.kind !== "stuck");
    if (next) { selectedAgent = next.agent; const a = agentById(next.agent); selectedFloor = a ? a.floor : selectedFloor; cam.floor = selectedFloor; renderPanels(); }
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

document.querySelectorAll("#mobileTabs [data-sheet]").forEach((btn) => {
  btn.onclick = () => {
    document.body.classList.remove("sheet-left", "sheet-right");
    if (btn.dataset.sheet) document.body.classList.add("sheet-" + btn.dataset.sheet);
  };
});

// ---------- loop ----------
let frames = 0, lastFps = performance.now();
function frame() {
  cam.zoom += (cam.tz - cam.zoom) * 0.14;
  cam.x += (cam.tx - cam.x) * 0.16;
  cam.y += (cam.ty - cam.y) * 0.16;
  // eased re-seating: resting bots wander inside their floor between tasks
  const now = performance.now() / 1000;
  scene.bots.forEach((bot) => {
    bot.x += (bot.tx - bot.x) * 0.05;
    bot.y += (bot.ty - bot.y) * 0.05;
    if (bot.status === "resting" && bot.act !== "chat" && now > bot.nextMove) {
      bot.tx = 1 + Math.random() * 2.2;
      bot.ty = 1 + Math.random() * 2.2;
      bot.nextMove = now + 16 + Math.random() * 18;
    }
  });
  assignRestStates();
  drawRoom();
  frames += 1;
  if (performance.now() - lastFps >= 1000) {
    $("fps").textContent = frames + " fps";
    frames = 0; lastFps = performance.now();
  }
  requestAnimationFrame(frame);
}

async function checkInstance() {
  try {
    const h = await (await fetch("/api/health", { cache: "no-store" })).json();
    if (checkInstance.seen && h.instance && h.instance !== checkInstance.seen) location.reload();
    checkInstance.seen = h.instance || checkInstance.seen;
  } catch (err) { /* mid-restart */ }
}

async function refresh() {
  try {
    const response = await fetch("/api/scene", { cache: "no-store" });
    DATA = await response.json();
    $("clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const firstPaint = !scene.bots.length;
    if (firstPaint || scene.bots.length !== DATA.agents.length) buildScene();
    DATA.agents.forEach((agent) => {
      const bot = scene.bots.find((b) => b.id === agent.id);
      if (bot) { bot.status = agent.status; bot.floor = agent.floor; bot.accent = agent.accent; }
    });
    if (firstPaint) {
      fit(false);
      const want = Number(QS.get("floor"));
      if (want >= 1 && DATA.floors.some((f) => f.level === want)) selectFloor(want);
    }
    renderPanels();
    pollAsked();
    checkInstance();
  } catch (err) {
    $("clock").textContent = T("offline");
  }
}

function applyI18n() {
  document.documentElement.setAttribute("lang", LANG === "zh" ? "zh" : "en");
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = T(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-ph]").forEach((el) => { el.placeholder = T(el.dataset.i18nPh); });
  $("langBtn").textContent = LANG === "zh" ? "EN" : "中";
  $("hint").textContent = T("hint");
  if (DATA) renderPanels();
}

applyI18n();
refresh();
setInterval(refresh, 3000);
frame();
</script>
</body></html>
"""


def render(cfg, lang: str = "zh") -> str:
    """`lang` seeds the initial <html lang>; the client keeps its own choice."""
    page = PAGE.replace("__NAME__", cfg.name).replace("__WS__", str(cfg.workspace))
    return page.replace("__LANG__", "zh" if lang == "zh" else "en")
