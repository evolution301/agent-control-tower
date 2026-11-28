"""蜂群实时网页看板：`/swarm`。

沿用控制塔的离线哑光设计体系（零 CDN、复用内置 `/assets/pico.css`、暗/亮双主题、
同源回环写保护），提供：
- 左侧：工蜂花名册（实时 4 色状态灯、当前模型、独立工位模式与未合并文件数、
  一键换模型 / 控制塔质检 / 合并工位 / 解散 + 召唤新工蜂表单）；
- 右侧：公开共享消息板（自动高亮 @提及、区分派活/回复/换脑/质检/巡检标签、
  底部输入框直接发 @名字 或 @all 唤醒工蜂）。
"""

from __future__ import annotations

from .config import TowerConfig

PAGE = r"""<!doctype html>
<html lang="__LANG__" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<link rel="stylesheet" href="/assets/pico.css">
<title>__NAME__ — 蜂群实时看板</title>
<style>
  :root{--page:#FAFAF8;--section:#F4F3F0;--card:#FFFFFF;--line:#D2CDC2;--ink:#1F1F24;--ink2:#6E6A61;--radius:14px;
    --green:#2F7D4F;--red:#B3402E;--blue:#2F5FA8;--orange:#B07A1F;--slate:#6B4FA8;
    --mono:ui-monospace,SFMono-Regular,Consolas,monospace;
    --pico-background-color:var(--page);--pico-color:var(--ink);--pico-muted-color:var(--ink2);
    --pico-muted-border-color:var(--line);--pico-card-background-color:var(--card);--pico-card-border-color:var(--line);
    --pico-card-box-shadow:none;--pico-border-radius:var(--radius);--pico-form-element-background-color:var(--card);
    --pico-form-element-border-color:var(--line);--pico-primary:#2F5FA8;--pico-primary-background:#2F5FA8}
  [data-theme="dark"]{--page:#1F1F26;--section:#26262F;--card:#2C2C36;--line:#3B3B48;--ink:#ECEAE4;--ink2:#A9A5A0;color-scheme:dark}
  *{box-shadow:none!important;text-shadow:none!important}
  html body{background:var(--page);color:var(--ink)}
  main.container{max-width:1280px;padding-block:20px}
  header{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
  header h1{font-size:22px;margin:0}
  .sub{font-size:12px;color:var(--ink2)}
  .grow{flex:1}
  .kpis{display:flex;gap:8px;flex-wrap:wrap}
  .kpi{min-width:68px;padding:6px 10px;background:var(--section);border:1px solid var(--line);border-radius:10px;text-align:center}
  .kpi b{display:block;font:700 16px/1.2 var(--mono)}
  .kpi span{font-size:10px;color:var(--ink2);text-transform:uppercase}
  .seg{display:flex;gap:6px;align-items:center}
  .seg a,.seg button{width:auto;margin:0;padding:4px 10px;background:transparent;border:1px solid var(--line);border-radius:8px;color:var(--ink2);font:600 12px/18px var(--mono);text-decoration:none}
  .seg .on{background:var(--blue);border-color:var(--blue);color:#fff}
  .layout{display:grid;grid-template-columns:390px 1fr;gap:16px;align-items:start}
  @media(max-width:920px){.layout{grid-template-columns:1fr}}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:14px}
  .sec-hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}
  .sec-hd h2{font-size:13px;letter-spacing:.6px;text-transform:uppercase;color:var(--ink2);margin:0}
  .worker{background:var(--section);border:1px solid var(--line);border-radius:12px;padding:11px 12px;margin-bottom:10px}
  .worker.killed{opacity:.58}
  .w-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .lamp{width:9px;height:9px;border-radius:99px;display:inline-block}
  .lamp.working{background:var(--blue)}
  .lamp.waiting{background:var(--green)}
  .lamp.stopped{background:var(--orange)}
  .lamp.killed{background:#7C7884}
  .w-name{font:700 14px/1.2 var(--mono)}
  .chip{display:inline-block;padding:1px 7px;border-radius:99px;font:600 11px/18px var(--mono);background:var(--card);border:1px solid var(--line);color:var(--ink2)}
  .chip.ok{background:var(--green);border-color:var(--green);color:#fff}
  .chip.bad{background:var(--red);border-color:var(--red);color:#fff}
  .chip.ws{color:var(--ink)}
  .w-brief{font-size:12px;color:var(--ink2);margin:6px 0 8px;line-height:1.45}
  .w-acts{display:flex;gap:6px;flex-wrap:wrap}
  .w-acts button{width:auto;margin:0;padding:3px 9px;font:600 11px/16px var(--mono);border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
  .w-acts button.danger{color:var(--red);border-color:var(--red)}
  .w-acts button.primary{background:var(--blue);border-color:var(--blue);color:#fff}
  .board-stream{max-height:560px;overflow-y:auto;padding-right:4px;display:flex;flex-direction:column;gap:8px}
  .msg{background:var(--section);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
  .msg.brief{border-left:3px solid var(--blue)}
  .msg.verify{border-left:3px solid var(--green)}
  .msg.model_switch{border-left:3px solid var(--slate)}
  .msg.monitor{border-left:3px solid var(--orange)}
  .m-hd{display:flex;gap:8px;align-items:center;font-size:11px;color:var(--ink2);margin-bottom:4px;font-family:var(--mono)}
  .m-hd b{color:var(--ink);font-size:12px}
  .m-body{font-size:13px;line-height:1.5;white-space:pre-wrap;word-break:break-word}
  .mention{display:inline-block;padding:0 5px;border-radius:6px;background:rgba(47,95,168,.22);color:var(--ink);font-family:var(--mono);font-size:12px}
  .composer{display:flex;gap:8px;margin-top:12px}
  .composer input{flex:1;margin:0;background:var(--page);border:1px solid var(--line);border-radius:10px}
  .composer button{width:auto;margin:0;padding:8px 16px;border-radius:10px}
  details.add-box{margin-bottom:12px;background:var(--section);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
  details.add-box summary{font:600 13px/1.4 var(--mono);cursor:pointer}
  .form-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}
  .form-grid input,.form-grid select,.form-grid textarea{margin:0;font-size:12px;padding:6px 8px;background:var(--card)}
  .form-grid .full{grid-column:1/-1}
  #toast{font-size:12px;color:var(--ink2);min-height:18px;margin:6px 0}
</style>
</head>
<body>
<main class="container">
  <header>
    <div>
      <h1 id="swarm-title">__NAME__ 蜂群</h1>
      <div class="sub">公开共享消息板 · @唤醒休眠工蜂 · 独立工位隔离 · 控制塔质检验收</div>
    </div>
    <span class="grow"></span>
    <span class="kpis" id="kpis"></span>
    <span class="seg">
      <a href="/">值守台</a>
      <a href="/scene">场景</a>
      <a href="/swarm" class="on">蜂群</a>
      <button type="button" id="btn-monitor">巡检</button>
      <button type="button" id="btn-theme">明/暗</button>
    </span>
  </header>

  <div id="toast" role="status"></div>

  <div class="layout">
    <section class="panel">
      <div class="sec-hd">
        <h2>工蜂花名册</h2>
        <span class="sub" id="clock">—</span>
      </div>

      <details class="add-box" id="add-details">
        <summary>+ 召唤新工蜂 / 分片并行</summary>
        <form id="add-form" class="form-grid">
          <input name="name" placeholder="工蜂名（如 frontend）" required>
          <select name="workspace">
            <option value="share">share（独立 worktree 工位）</option>
            <option value="inherit">inherit（共享主目录/只读）</option>
            <option value="branch">branch（完全独立分支）</option>
          </select>
          <input name="model" placeholder="模型（留空用默认，如 gpt-4.1-mini）">
          <select name="adapter">
            <option value="">默认后端</option>
            <option value="mock">mock（离线演示）</option>
            <option value="cli">cli（本地代理 CLI）</option>
            <option value="openai">openai（兼容 API）</option>
            <option value="dsh">dsh</option>
          </select>
          <input name="fork" placeholder="克隆记忆自（可选，如 coder-1）">
          <input name="n" type="number" min="1" max="8" value="1" placeholder="分身数 n">
          <textarea class="full" name="brief" rows="2" placeholder="派活说明：负责什么、什么算完成" required></textarea>
          <button class="full" type="submit">召唤并发布到公开大群</button>
        </form>
      </details>

      <div id="workers">加载中…</div>
    </section>

    <section class="panel">
      <div class="sec-hd">
        <h2>公开共享消息板（@名字 自动唤醒）</h2>
        <span class="sub" id="health-pill">健康检测中…</span>
      </div>
      <div class="board-stream" id="board">加载中…</div>
      <form class="composer" id="post-form">
        <input id="post-input" placeholder="在群里发言，例如：@coder-1 请补一条边界测试（或 @all 全体汇报）" autocomplete="off" required>
        <button type="submit">发送</button>
      </form>
    </section>
  </div>
</main>

<script>
const qs = location.search || "";
const toastEl = document.getElementById("toast");
function toast(text) { toastEl.textContent = text; }
function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}
function highlightMentions(text) {
  return esc(text).replace(/(^|[^A-Za-z0-9_.-])(@[a-z0-9][a-z0-9_-]{0,23})/g, '$1<span class="mention">$2</span>');
}
async function postJSON(url, body) {
  const res = await fetch(url + qs, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok || data.ok === false) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}
function render(data) {
  document.getElementById("swarm-title").textContent = "🐝 " + (data.title || "蜂群");
  document.getElementById("clock").textContent = "刷新 " + new Date().toLocaleTimeString();
  const c = data.counts || {};
  document.getElementById("kpis").innerHTML = [
    ["干活中", c.working || 0],
    ["休眠中", c.waiting || 0],
    ["暂停", c.stopped || 0],
    ["已解散", c.killed || 0],
    ["消息", c.messages || 0],
  ].map(([k, v]) => `<span class="kpi"><b>${v}</b><span>${k}</span></span>`).join("");

  const h = data.health || {};
  document.getElementById("health-pill").textContent = h.healthy
    ? "● 巡检正常"
    : `▲ 告警：卡死 ${(h.stalled||[]).length} · 未回 ${(h.unanswered||[]).length}`;

  const workersEl = document.getElementById("workers");
  if (!data.workers || !data.workers.length) {
    workersEl.innerHTML = '<div class="sub">还没有工蜂。点上方「+ 召唤新工蜂」即可开始！</div>';
  } else {
    workersEl.innerHTML = data.workers.map(w => {
      const alive = w.state !== "killed";
      const vchip = w.verified === true ? '<span class="chip ok">✓ 已质检</span>'
                  : w.verified === false ? '<span class="chip bad">✗ 质检失败</span>' : '';
      const unm = (w.unmerged_files || []).length;
      const uchip = unm ? `<span class="chip">${unm} 待合并</span>` : (w.merged ? '<span class="chip ok">已合并</span>' : '');
      const bchip = w.branch_name ? `<span class="chip ws">${esc(w.workspace_mode)} · ${esc(w.branch_name)}</span>`
                                  : `<span class="chip ws">${esc(w.workspace_mode)}</span>`;
      const acts = alive ? `
        <div class="w-acts">
          <button type="button" onclick="mentionWorker('${esc(w.name)}')">@它</button>
          <button type="button" onclick="doSwitchModel('${esc(w.name)}')">换大脑</button>
          <button type="button" class="primary" onclick="doVerify('${esc(w.name)}')">质检验收</button>
          ${w.workspace_mode !== "inherit" ? `<button type="button" onclick="doMerge('${esc(w.name)}')">合并工位</button>` : ""}
          <button type="button" class="danger" onclick="doKill('${esc(w.name)}')">解散</button>
        </div>` : "";
      return `<article class="worker ${esc(w.state)}">
        <div class="w-top">
          <span class="lamp ${esc(w.state)}"></span>
          <span class="w-name">${esc(w.name)}</span>
          <span class="chip">${esc(w.state)}</span>
          <span class="chip">${esc(w.adapter)}:${esc(w.model)}</span>
          ${bchip}${vchip}${uchip}
        </div>
        <div class="w-brief">${esc(w.brief || "").slice(0, 160)}</div>
        ${acts}
      </article>`;
    }).join("");
  }

  const boardEl = document.getElementById("board");
  if (!data.board || !data.board.length) {
    boardEl.innerHTML = '<div class="sub">公开消息板暂无消息。召唤一只工蜂或直接发消息吧。</div>';
  } else {
    const atBottom = boardEl.scrollHeight - boardEl.scrollTop - boardEl.clientHeight < 60;
    boardEl.innerHTML = data.board.map(m => `
      <div class="msg ${esc(m.kind)}">
        <div class="m-hd">
          <b>${esc(m.author)}</b>
          <span>${esc(m.id)}</span>
          <span>${esc(m.kind)}</span>
          <span>${esc(m.ts)}</span>
        </div>
        <div class="m-body">${highlightMentions(m.body)}</div>
      </div>`).join("");
    if (atBottom) boardEl.scrollTop = boardEl.scrollHeight;
  }
}

window.mentionWorker = (name) => {
  const inp = document.getElementById("post-input");
  inp.value = `@${name} ` + inp.value.replace(new RegExp(`^@${name}\\s*`), "");
  inp.focus();
};
window.doSwitchModel = async (name) => {
  const next = prompt(`给工蜂 ${name} 切换新模型（保留全部历史记忆与工位）：`, "mock-pro");
  if (!next) return;
  const reason = prompt("切换原因（可选）：", "按任务难度切换模型") || "";
  toast(`正在为 ${name} 切换模型到 ${next}…`);
  try {
    await postJSON("/api/swarm/action", {action: "model", name, model: next, reason});
    toast(`已为 ${name} 无损切换到 ${next}`);
    refresh();
  } catch (e) { toast("失败：" + e.message); }
};
window.doVerify = async (name) => {
  toast(`正在运行控制塔质检门核查 ${name}…`);
  try {
    const r = await postJSON("/api/swarm/action", {action: "verify", name, land: true});
    toast(r.ok ? `质检通过：${name}` : `质检未通过：${(r.problems||[]).join("; ")}`);
    refresh();
  } catch (e) { toast("质检失败：" + e.message); }
};
window.doMerge = async (name) => {
  toast(`正在合并 ${name} 的独立工位…`);
  try {
    const r = await postJSON("/api/swarm/action", {action: "merge", name});
    toast(r.detail || "已合并");
    refresh();
  } catch (e) { toast("合并失败：" + e.message); }
};
window.doKill = async (name) => {
  if (!confirm(`确定永久解散工蜂 ${name} 并清理其临时工位吗？`)) return;
  try {
    await postJSON("/api/swarm/action", {action: "kill", name, force: true});
    toast(`已解散 ${name}`);
    refresh();
  } catch (e) { toast("解散失败：" + e.message); }
};

document.getElementById("add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const payload = Object.fromEntries(fd.entries());
  payload.n = parseInt(payload.n || "1", 10) || 1;
  toast(`正在召唤 ${payload.name}…`);
  try {
    await postJSON("/api/swarm/add", payload);
    e.target.reset();
    document.getElementById("add-details").open = false;
    toast(`已召唤 ${payload.name} 并唤醒干活`);
    refresh();
  } catch (err) { toast("召唤失败：" + err.message); }
});

document.getElementById("post-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const inp = document.getElementById("post-input");
  const body = inp.value.trim();
  if (!body) return;
  inp.value = "";
  toast("已发送消息，正在唤醒被 @提及 的工蜂…");
  try {
    const r = await postJSON("/api/swarm/post", {body});
    toast(r.woken && r.woken.length ? `已唤醒：${r.woken.join(", ")}` : "消息已发布到共享黑板");
    refresh();
  } catch (err) { toast("发送失败：" + err.message); }
});

document.getElementById("btn-monitor").addEventListener("click", async () => {
  toast("正在执行蜂群巡检…");
  try {
    const r = await postJSON("/api/swarm/action", {action: "monitor", nudge: true});
    toast(r.healthy ? "巡检通过：全员健康" : `巡检完成，已处理 ${(r.nudged||[]).length} 项`);
    refresh();
  } catch (err) { toast("巡检失败：" + err.message); }
});

document.getElementById("btn-theme").addEventListener("click", () => {
  const html = document.documentElement;
  html.dataset.theme = html.dataset.theme === "dark" ? "light" : "dark";
});

async function refresh() {
  try {
    const res = await fetch("/api/swarm" + qs);
    if (res.ok) render(await res.json());
  } catch (_) {}
}
refresh();
setInterval(refresh, 2500);
</script>
</body>
</html>
"""


def render(cfg: TowerConfig, lang: str = "zh") -> str:
    """渲染蜂群实时看板 HTML 页面。"""
    return (
        PAGE.replace("__NAME__", cfg.name)
        .replace("__LANG__", lang or "zh")
    )
