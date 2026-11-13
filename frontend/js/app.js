/* ============================================================
   MosBox 控制台 SPA — 原生 JS,无构建步骤
   ============================================================ */
"use strict";

/* ---------------- 工具 ---------------- */
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const escAttr = esc;

const fmtBytes = (n) => {
  if (n == null || isNaN(n)) return "-";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
};
const fmtUptime = (s) => {
  if (s == null) return "-";
  s = Math.floor(s);
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  if (d) return `${d}天${h}小时`;
  if (h) return `${h}小时${m}分`;
  if (m) return `${m}分${s % 60}秒`;
  return `${s}秒`;
};
const fmtTime = (ts) => {
  if (!ts) return "-";
  const d = new Date(ts * (ts < 1e12 ? 1000 : 1));
  const p = (x) => String(x).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
};

function toast(msg, type = "info", ms = 3200) {
  let box = $(".toasts");
  if (!box) { box = document.createElement("div"); box.className = "toasts"; document.body.appendChild(box); }
  const t = document.createElement("div");
  t.className = `toast ${type}`;
  t.textContent = msg;
  box.appendChild(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; setTimeout(() => t.remove(), 320); }, ms);
}

function confirmBox(title, text, danger = true) {
  return new Promise((resolve) => {
    const mask = document.createElement("div");
    mask.className = "modal-mask";
    mask.innerHTML = `
      <div class="modal" style="width:400px">
        <h3>${esc(title)}</h3>
        <p style="color:var(--text-dim);font-size:13px">${esc(text)}</p>
        <div class="modal-foot">
          <button class="btn" data-act="no">取消</button>
          <button class="btn ${danger ? "btn-danger" : "btn-primary"}" data-act="yes">确认</button>
        </div>
      </div>`;
    mask.addEventListener("click", (e) => {
      const act = e.target.dataset?.act;
      if (act === "yes" || e.target === mask) { mask.remove(); resolve(act === "yes"); }
      if (act === "no") { mask.remove(); resolve(false); }
    });
    document.body.appendChild(mask);
  });
}

function modal(title, bodyHTML) {
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `<div class="modal">
    <h3>${title}</h3>
    <div class="modal-body">${bodyHTML}</div>
  </div>`;
  const close = () => mask.remove();
  mask.addEventListener("click", (e) => { if (e.target === mask) close(); });
  document.body.appendChild(mask);
  return { mask, close };
}

function copyText(text) {
  navigator.clipboard?.writeText(text).then(() => toast("已复制到剪贴板", "ok", 1800))
    .catch(() => {
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      document.execCommand("copy"); ta.remove();
      toast("已复制到剪贴板", "ok", 1800);
    });
}

/* ---------------- API ---------------- */
const api = {
  async req(method, url, body, raw = false) {
    const opt = { method, headers: {}, credentials: "same-origin" };
    if (body instanceof FormData) { opt.body = body; }
    else if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    const resp = await fetch(url, opt);
    if (raw) return resp;
    let data = {};
    try { data = await resp.json(); } catch {}
    if (!resp.ok) {
      const err = new Error(data.detail || `HTTP ${resp.status}`);
      err.status = resp.status;
      throw err;
    }
    return data;
  },
  get: (u) => api.req("GET", u),
  post: (u, b) => api.req("POST", u, b),
  put: (u, b) => api.req("PUT", u, b),
  del: (u) => api.req("DELETE", u),
  upload: (u, form) => api.req("POST", u, form),
};

/* ---------------- 图标 ---------------- */
const I = (name, size = 17) => {
  const paths = {
    dash: '<rect x="3" y="3" width="7.5" height="9" rx="1.6"/><rect x="13.5" y="3" width="7.5" height="5.5" rx="1.6"/><rect x="13.5" y="11.5" width="7.5" height="9.5" rx="1.6"/><rect x="3" y="15" width="7.5" height="6" rx="1.6"/>',
    apps: '<rect x="3" y="3" width="8" height="8" rx="2"/><rect x="13" y="3" width="8" height="8" rx="2"/><rect x="3" y="13" width="8" height="8" rx="2"/><rect x="13" y="13" width="8" height="8" rx="2"/>',
    files: '<path d="M4 4h6l2 2.5h8V19a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4z"/><path d="M4 10h16"/>',
    term: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M7 9l3.5 3L7 15M13 15h4"/>',
    ssh: '<rect x="4" y="10.5" width="16" height="9.5" rx="2.5"/><path d="M8 10.5V7a4 4 0 0 1 8 0v3.5M8.5 15h.01M12 15h.01M15.5 15h.01"/>',
    cloud: '<path d="M7 18a4 4 0 0 1-.6-7.95A5.5 5.5 0 0 1 17 8.6 4.2 4.2 0 0 1 17.5 18H7z"/>',
    key: '<circle cx="8" cy="8" r="4.5"/><path d="M11.2 11.2L20 20M16.5 16.5l2.5-2.5M14 14l2.5-2.5"/>',
    gear: '<circle cx="12" cy="12" r="3.2"/><path d="M12 2.8l1 2.6a6.9 6.9 0 0 1 2.4 1l2.6-.9 2 3.4-2 1.8a7 7 0 0 1 0 2.6l2 1.8-2 3.4-2.6-.9a6.9 6.9 0 0 1-2.4 1l-1 2.6h-2.4l-1-2.6a6.9 6.9 0 0 1-2.4-1l-2.6.9-2-3.4 2-1.8a7 7 0 0 1 0-2.6L2 8.9l2-3.4 2.6.9a6.9 6.9 0 0 1 2.4-1l1-2.6z" fill="none"/>',
    play: '<path d="M7 5l11 7-11 7V5z"/>',
    stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="1.8"/>',
    restart: '<path d="M20 12a8 8 0 1 1-2.34-5.66M20 4v4h-4"/>',
    trash: '<path d="M4 7h16M10 7V5a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v2m3 0v12a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2V7"/>',
    upload: '<path d="M12 16V5M7.5 9.5L12 5l4.5 4.5M4 20h16"/>',
    download: '<path d="M12 5v11M7.5 11.5L12 16l4.5-4.5M4 20h16"/>',
    edit: '<path d="M4 20h4L20 8l-4-4L4 16v4z"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M6 3.5v2M18 3.5v2M6 18.5v2M18 18.5v2M3.5 6h2M3.5 18h2M18.5 6h2M18.5 18h2" stroke-width="1.2"/>',
    mem: '<rect x="3" y="7" width="18" height="10" rx="2"/><path d="M7 17v3M11 17v3M15 17v3M19 17v3M7 10.5h10M7 13.5h6"/>',
    disk: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="2.5"/><path d="M14.5 9.5L18 6M6 18l3.5-3.5"/>',
    net: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4"/><circle cx="12" cy="12" r="3.4"/><circle cx="12" cy="5" r="1.4"/><circle cx="12" cy="19" r="1.4"/><circle cx="5" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    logout: '<path d="M9 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h3M15 8l4 4-4 4M19 12H9"/>',
    refresh: '<path d="M20 12a8 8 0 1 1-2.34-5.66M20 4v4h-4"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 10.5V16M12 7.8h.01"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    back: '<path d="M14.5 5.5L8 12l6.5 6.5"/>',
    folder: '<path d="M3.5 6.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-11.5z"/>',
    file: '<path d="M6 3.5h7l5 5V20a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 5 20V5a1.5 1.5 0 0 1 1-1.5z"/><path d="M13 3.5v5h5"/>',
    save: '<path d="M5 4h11l4 4v12H5V4z"/><path d="M8 4v5h7V4M8 20v-6h8v6"/>',
    globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c-5.5 5-5.5 12 0 17M12 3.5c5.5 5 5.5 12 0 17"/>',
    shell: '<path d="M4 17l6-5-6-5M12 19h8"/>',
    bell: '<path d="M6 9a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6M10 20a2.2 2.2 0 0 0 4 0"/>',
  };
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${paths[name] || ""}</svg>`;
};

/* ---------------- 全局状态 ---------------- */
const S = {
  me: null,
  route: "",
  stats: { system: {}, workloads: {} },
  dash: null,
  statHistory: { cpu: [], mem: [], rx: [], tx: [] },
  ws: null,
  term: null, termWs: null, termFit: null,
  logWs: null,
  host: location.host,
};

/* ---------------- 路由 ---------------- */
const routes = {
  "/dashboard": { title: "仪表盘", view: viewDashboard, icon: "dash" },
  "/workloads": { title: "程序管理", view: viewWorkloads, icon: "apps" },
  "/files": { title: "文件管理", view: viewFiles, icon: "files" },
  "/terminal": { title: "终端", view: viewTerminal, icon: "term" },
  "/ssh": { title: "SSH 服务", view: viewSSH, icon: "ssh" },
  "/storage": { title: "云端存储", view: viewStorage, icon: "cloud" },
  "/keys": { title: "API 密钥", view: viewKeys, icon: "key" },
  "/settings": { title: "设置", view: viewSettings, icon: "gear" },
};

async function navigate() {
  let hash = location.hash.replace(/^#/, "") || "/dashboard";
  if (hash.startsWith("/wl/")) { renderShell("/workloads"); viewWorkloadDetail(hash.slice(4)); return; }
  const route = routes[hash] ? hash : "/dashboard";
  S.route = route;
  renderShell(route);
  await routes[route].view($("#page"));
}

function renderShell(active) {
  const app = $("#app");
  if (!S.me) return;
  if (!$("#layout")) {
    app.innerHTML = `
      <div class="layout" id="layout">
        <aside class="sidebar" id="sidebar">
          <div class="logo">
            <div class="logo-badge">M</div>
            <div><div class="logo-name">MosBox</div><div class="logo-sub">控制台</div></div>
          </div>
          <nav id="nav"></nav>
          <div class="sidebar-foot"><span class="dot"></span><span>服务运行中 · v<span id="ver">2.0</span></span>
            <a class="btn btn-icon btn-sm" style="margin-left:auto" id="logout-btn" title="退出登录">${I("logout", 14)}</a></div>
        </aside>
        <div class="main">
          <div class="topbar">
            <button class="btn btn-icon menu-btn" id="menu-btn">${I("menu")}</button>
            <h1 id="page-title"></h1>
            <div class="spacer"></div>
            <div class="host" id="host-tag">${esc(S.host)}</div>
          </div>
          <div class="content" id="page"></div>
        </div>
      </div>`;
    $("#menu-btn").onclick = () => $("#sidebar").classList.toggle("open");
    $("#logout-btn").onclick = doLogout;
    connectEvents();
  }
  const nav = $("#nav");
  nav.innerHTML = Object.entries(routes).map(([path, r]) =>
    `<div class="nav-item ${path === active ? "active" : ""}" data-route="${path}">${I(r.icon)}<span>${r.title}</span></div>`).join("");
  $$(".nav-item", nav).forEach(el => el.onclick = () => {
    location.hash = el.dataset.route;
    $("#sidebar").classList.remove("open");
  });
  $("#page-title").textContent = routes[active]?.title || "";
}

/* ---------------- 登录 ---------------- */
async function viewLogin() {
  document.title = "登录 · MosBox 控制台";
  const app = $("#app");
  app.innerHTML = `
    <div class="login-wrap">
      <div class="login-card">
        <div class="logo-badge">M</div>
        <h2>MosBox 控制台</h2>
        <div class="sub">单端口复用 · 程序守护 · SSH · 云存储</div>
        <form id="login-form">
          <div class="form-row">
            <label>管理密码</label>
            <input class="input" type="password" id="pw" placeholder="请输入管理密码" autocomplete="current-password" autofocus>
          </div>
          <button class="btn btn-primary" type="submit" id="login-btn">进入控制台</button>
        </form>
        <div class="login-foot">raddy L4 分流 + puressh sshd · v2.0</div>
      </div>
    </div>`;
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    const btn = $("#login-btn");
    btn.disabled = true; btn.textContent = "验证中…";
    try {
      await api.post("/api/auth/login", { password: $("#pw").value });
      await boot();
    } catch (err) {
      toast(err.message, "err");
      btn.disabled = false; btn.textContent = "进入控制台";
    }
  };
}

async function doLogout() {
  try { await api.post("/api/auth/logout", {}); } catch {}
  location.reload();
}

/* ---------------- 事件流(WebSocket) ---------------- */
function connectEvents() {
  if (S.ws) { try { S.ws.close(); } catch {} }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/ws/events`);
  S.ws = ws;
  ws.onmessage = (e) => {
    let msg; try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.type === "event") {
      const feed = $("#event-feed");
      if (feed) {
        feed.prepend(eventRow(msg.event));
        while (feed.children.length > 60) feed.lastChild.remove();
      }
      const kind = msg.event.kind || "";
      if (kind.startsWith("workload.")) refreshDashQuiet();
      if (kind.startsWith("service.")) toast(msg.event.message, kind.includes("error") || kind.includes("died") ? "err" : "info");
    }
  };
  ws.onclose = () => setTimeout(() => { if (S.me) connectEvents(); }, 4000);
}

function eventRow(ev) {
  return `<div class="event-item"><span class="t">${fmtTime(ev.ts)}</span><span class="k">${esc((ev.kind || "").split(".")[0])}</span><span class="m" title="${escAttr(ev.message)}">${esc(ev.message)}</span></div>`;
}

/* ---------------- 仪表盘 ---------------- */
async function viewDashboard(el) {
  document.title = "仪表盘 · MosBox 控制台";
  el.innerHTML = loadingHTML();
  const [dash, stats] = await Promise.all([api.get("/api/dashboard"), api.get("/api/system/stats")]);
  S.dash = dash; S.stats = stats;
  const sys = stats.system;
  const tiles = [
    { label: "CPU", icon: "cpu", value: `${sys.cpu_percent}%`, sub: `${sys.cpu_cores} 核 · 负载 ${sys.load1}`, hist: "cpu", max: 100 },
    { label: "内存", icon: "mem", value: fmtBytes(sys.mem_used), sub: `共 ${fmtBytes(sys.mem_total)} · ${sys.mem_percent}%`, hist: "mem", max: sys.mem_total },
    { label: "下行", icon: "net", value: fmtSpeed(sys.net_rx), sub: `↑ ${fmtSpeed(sys.net_tx)}`, hist: "rx" },
    { label: "磁盘", icon: "disk", value: fmtBytes(sys.disk_used), sub: `共 ${fmtBytes(sys.disk_total)} · ${sys.disk_percent}%`, meter: sys.disk_percent },
  ];
  const wls = dash.workloads || [];
  const running = wls.filter(w => w.status === "running").length;
  el.innerHTML = `
    <div class="grid grid-4">
      ${tiles.map(t => `
        <div class="stat-tile">
          <div class="label">${I(t.icon)}${t.label}</div>
          <div class="value">${t.value}${t.meter != null ? "" : `<small>${esc(t.sub || "")}</small>`}</div>
          ${t.meter != null ? `<div style="margin-top:8px" class="meter"><i style="width:${Math.min(t.meter, 100)}%" class="${t.meter > 85 ? "hot" : ""}"></i></div><div style="font-size:11px;color:var(--text-faint);margin-top:5px">${esc(t.sub || "")}</div>` : ""}
          ${t.hist ? `<canvas width="88" height="30" data-hist="${t.hist}" data-max="${t.max || ""}"></canvas>` : ""}
        </div>`).join("")}
    </div>

    <div class="grid" style="grid-template-columns: 1.5fr 1fr; margin-top:14px" id="dash-main">
      <div style="display:flex;flex-direction:column;gap:14px">
        <div class="card">
          <div class="card-title">${I("apps")}程序 <span class="hint">${running}/${wls.length} 运行中</span>
            <button class="btn btn-sm btn-primary" onclick="location.hash='/workloads'" style="margin-left:auto">管理</button></div>
          ${wls.length ? `<div class="table-wrap"><table class="table">
            <thead><tr><th>名称</th><th>状态</th><th>运行时长</th><th>内存</th><th>自动拉活</th></tr></thead>
            <tbody>${wls.map(w => `
              <tr style="cursor:pointer" onclick="location.hash='/wl/${escAttr(w.id)}'">
                <td><b>${esc(w.name)}</b></td>
                <td>${statusPill(w.status)}</td>
                <td class="mono">${w.status === "running" ? fmtUptime(w.uptime) : "-"}</td>
                <td class="mono">${fmtBytes((w.stats?.memory_mb || 0) * 1048576)}</td>
                <td>${w.auto_restart ? "✓" : "—"}</td>
              </tr>`).join("")}</tbody></table></div>`
      : `<div class="empty"><div class="big">📦</div><p>还没有程序 —— 到「程序管理」上传你的 Python 程序即可开始</p></div>`}
        </div>

        <div class="card">
          <div class="card-title">${I("bell")}最近事件</div>
          <div class="event-feed" id="event-feed">${(dash.events || []).map(eventRow).join("")}</div>
        </div>
      </div>

      <div style="display:flex;flex-direction:column;gap:14px">
        <div class="card">
          <div class="card-title">${I("info")}服务状态</div>
          <div class="kv">
            <div class="row"><span class="k">面板 API</span><span class="v" style="color:var(--ok)">● 运行中</span></div>
            <div class="row"><span class="k">Web 服务</span><span class="v">raddy · 端口 ${dash.version ? esc(String(dash.ssh?.port || "")) : "-"}</span></div>
            <div class="row"><span class="k">SSH</span><span class="v">${dash.ssh?.enabled ? `<span style="color:var(--ok)">●</span> puressh sshd` : "已禁用"} · ${dash.ssh?.key_count ?? 0} 把公钥</span></div>
            <div class="row"><span class="k">主机指纹</span><span class="v">${esc(dash.ssh?.host_key_fp || "-")}</span></div>
            <div class="row"><span class="k">R2 存储</span><span class="v">${dash.r2?.enabled ? "已配置" : "未配置"}</span></div>
            <div class="row"><span class="k">版本</span><span class="v">v${esc(dash.version)}</span></div>
          </div>
        </div>
        <div class="card">
          <div class="card-title">${I("ssh")}快速连接</div>
          <div style="display:flex;flex-direction:column;gap:10px;font-size:12.5px">
            <div><div style="color:var(--text-faint);margin-bottom:5px">SSH(与面板同端口)</div>
              <div class="codebox"><span>ssh -p ${esc(dash.ssh?.port || "")} ${esc(dash.ssh?.user || "root")}@${esc(S.host.split(":")[0])}</span><span class="copy" onclick="copyText('ssh -p ${escAttr(dash.ssh?.port || "")} ${escAttr(dash.ssh?.user || "root")}@${escAttr(S.host.split(":")[0])}')">${I("copy", 14)}</span></div></div>
            <div><div style="color:var(--text-faint);margin-bottom:5px">面板地址</div>
              <div class="codebox"><span>http://${esc(S.host)}</span><span class="copy" onclick="copyText('http://${escAttr(S.host)}')">${I("copy", 14)}</span></div></div>
          </div>
        </div>
      </div>
    </div>`;
  drawSparks();
  $("#ver") && ($("#ver").textContent = dash.version);
  S._dashTimer && clearInterval(S._dashTimer);
  S._dashTimer = setInterval(refreshDashQuiet, 5000);
}

function fmtSpeed(bps) {
  if (bps == null) return "-";
  return fmtBytes(bps) + "/s";
}

function statusPill(st) {
  const map = { running: ["pill-running", "运行中"], stopped: ["pill-stopped", "已停止"], crashed: ["pill-crashed", "已崩溃"], starting: ["pill-starting", "启动中"] };
  const [cls, label] = map[st] || ["pill-stopped", st];
  return `<span class="pill ${cls}"><span class="dot"></span>${label}</span>`;
}

async function refreshDashQuiet() {
  if (S.route !== "/dashboard" || !$("#dash-main")) return;
  try {
    const [dash, stats] = await Promise.all([api.get("/api/dashboard"), api.get("/api/system/stats")]);
    S.dash = dash; S.stats = stats;
    const sys = stats.system;
    pushHist("cpu", sys.cpu_percent, 100);
    pushHist("mem", sys.mem_percent, 100);
    pushHist("rx", sys.net_rx, Math.max(sys.net_rx * 3, 10240));
    pushHist("tx", sys.net_tx, Math.max(sys.net_tx * 3, 10240));
    drawSparks();
    const wls = dash.workloads || [];
    const rows = $$("#dash-main tbody tr");
    wls.forEach((w, i) => {
      const tr = rows[i];
      if (!tr) return;
      tr.children[2].textContent = w.status === "running" ? fmtUptime(w.uptime) : "-";
      tr.children[3].textContent = fmtBytes((w.stats?.memory_mb || 0) * 1048576);
    });
  } catch {}
}

function pushHist(key, val, max) {
  const h = S.statHistory[key];
  h.push({ v: val, max });
  if (h.length > 40) h.shift();
}

function drawSparks() {
  $$("canvas[data-hist]").forEach(cv => {
    const key = cv.dataset.hist;
    const fixedMax = cv.dataset.max ? parseFloat(cv.dataset.max) : null;
    const h = S.statHistory[key] || [];
    if (h.length < 2) return;
    const ctx = cv.getContext("2d");
    const W = cv.width, H = cv.height;
    ctx.clearRect(0, 0, W, H);
    const max = fixedMax || Math.max(...h.map(p => p.max || p.v), 1);
    const grad = ctx.createLinearGradient(0, 0, W, 0);
    grad.addColorStop(0, "rgba(99,102,241,.9)");
    grad.addColorStop(1, "rgba(168,85,247,.9)");
    ctx.strokeStyle = grad; ctx.lineWidth = 1.6; ctx.beginPath();
    h.forEach((p, i) => {
      const x = i / (h.length - 1) * (W - 2) + 1;
      const y = H - 2 - Math.min(p.v / max, 1) * (H - 5);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
    const last = h[h.length - 1];
    ctx.fillStyle = "rgba(168,85,247,.7)";
    const ly = H - 2 - Math.min(last.v / max, 1) * (H - 5);
    ctx.beginPath(); ctx.arc(W - 2, ly, 2, 0, 7); ctx.fill();
  });
}

const loadingHTML = () => `<div class="empty"><div class="big">⏳</div><p>加载中…</p></div>`;

/* ---------------- 程序管理 ---------------- */
async function viewWorkloads(el) {
  document.title = "程序管理 · MosBox 控制台";
  el.innerHTML = loadingHTML();
  const data = await api.get("/api/workloads");
  const wls = data.workloads;
  el.innerHTML = `
    <div class="card">
      <div class="card-title">${I("apps")}全部程序 <span class="hint">${wls.length} 个</span>
        <div style="margin-left:auto;display:flex;gap:8px">
          <button class="btn btn-sm" onclick="wlCreateModal()">${I("plus", 13)}新建</button>
          <button class="btn btn-sm btn-primary" onclick="wlUploadModal()">${I("upload", 13)}上传程序</button>
        </div>
      </div>
      ${wls.length ? `<div class="table-wrap"><table class="table">
        <thead><tr><th>名称</th><th>入口 / 命令</th><th>状态</th><th>PID</th><th>重启</th><th>内存</th><th style="width:190px">操作</th></tr></thead>
        <tbody>
        ${wls.map(w => `
          <tr>
            <td><a href="#/wl/${escAttr(w.id)}" style="color:var(--text)"><b>${esc(w.name)}</b></a>
              <div style="font-size:11px;color:var(--text-faint)" class="mono">${escAttr(w.id)}</div></td>
            <td class="mono">${esc(w.command || w.entrypoint || "-")}</td>
            <td>${statusPill(w.status)}</td>
            <td class="mono">${w.pid || "-"}</td>
            <td class="mono">${w.restarts || 0}</td>
            <td class="mono">${fmtBytes((w.stats?.memory_mb || 0) * 1048576)}</td>
            <td>
              <div style="display:flex;gap:5px">
                ${w.status === "running"
                  ? `<button class="btn btn-sm btn-icon" title="停止" onclick="wlAction('${escAttr(w.id)}','stop')">${I("stop", 13)}</button>
                     <button class="btn btn-sm btn-icon" title="重启" onclick="wlAction('${escAttr(w.id)}','restart')">${I("restart", 13)}</button>`
                  : `<button class="btn btn-sm btn-icon" title="启动" onclick="wlAction('${escAttr(w.id)}','start')">${I("play", 13)}</button>`}
                <button class="btn btn-sm btn-icon" title="导出" onclick="location='/api/workloads/${escAttr(w.id)}/export'">${I("download", 13)}</button>
                <button class="btn btn-sm btn-icon btn-danger" title="删除" onclick="wlDelete('${escAttr(w.id)}')">${I("trash", 13)}</button>
              </div>
            </td>
          </tr>`).join("")}
        </tbody></table></div>`
      : `<div class="empty"><div class="big">🚀</div><p>上传一个 <b>.py</b> 或 <b>.zip</b> 程序,面板会自动安装依赖并守护运行</p></div>`}
    </div>`;
}

async function wlAction(wid, action) {
  try {
    await api.post(`/api/workloads/${wid}/actions`, { action });
    toast({ start: "已启动", stop: "已停止", restart: "已重启" }[action] || action, "ok", 1800);
    setTimeout(() => navigate(), 500);
  } catch (e) { toast(e.message, "err"); }
}

async function wlDelete(wid) {
  if (!await confirmBox("删除程序", "将停止并彻底删除该程序及其全部文件,此操作不可恢复。")) return;
  try { await api.del(`/api/workloads/${wid}`); toast("已删除", "ok"); navigate(); }
  catch (e) { toast(e.message, "err"); }
}

const TEMPLATES = {
  blank: "空白程序",
  "http-server": "HTTP 服务示例",
  scheduler: "定时任务示例",
};

function wlCreateModal() {
  const { mask, close } = modal(`${I("plus")} 新建程序`, `
    <div class="form-row"><label>程序名称</label><input class="input" id="nc-name" placeholder="my-bot"></div>
    <div class="form-row"><label>模板(可选)</label><select class="select" id="nc-tpl">
      <option value="">不使用模板</option>
      ${Object.entries(TEMPLATES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}
    </select></div>
    <div class="form-row"><label>入口文件</label><input class="input" id="nc-entry" value="main.py"></div>
    <div class="modal-foot"><button class="btn" id="nc-cancel">取消</button><button class="btn btn-primary" id="nc-ok">创建</button></div>`);
  $("#nc-cancel", mask).onclick = close;
  $("#nc-ok", mask).onclick = async () => {
    try {
      const r = await api.post("/api/workloads", {
        name: $("#nc-name", mask).value, template: $("#nc-tpl", mask).value || null,
        entrypoint: $("#nc-entry", mask).value,
      });
      close();
      toast("已创建", "ok");
      location.hash = `/wl/${r.workload.id}`;
    } catch (e) { toast(e.message, "err"); }
  };
}

function wlUploadModal() {
  const { mask, close } = modal(`${I("upload")} 上传程序`, `
    <div class="dropzone" id="up-zone">点击选择或拖拽文件到此处<br><small style="color:var(--text-faint)">支持 .py 单文件 / .zip 完整项目(≤64MB)</small></div>
    <input type="file" id="up-file" accept=".py,.zip" style="display:none">
    <div class="form-row" style="margin-top:14px"><label>程序名称(可选,默认取文件名)</label><input class="input" id="up-name" placeholder="my-bot"></div>
    <div class="modal-foot"><button class="btn" id="up-cancel">取消</button><button class="btn btn-primary" id="up-ok" disabled>上传</button></div>`);
  let file = null;
  const zone = $("#up-zone", mask), input = $("#up-file", mask);
  const setFile = (f) => {
    if (!f) return;
    if (!/\.(py|zip)$/i.test(f.name)) { toast("仅支持 .py 或 .zip", "err"); return; }
    file = f;
    zone.innerHTML = `📄 <b>${esc(f.name)}</b> (${fmtBytes(f.size)})`;
    $("#up-ok", mask).disabled = false;
  };
  zone.onclick = () => input.click();
  input.onchange = () => setFile(input.files[0]);
  zone.ondragover = (e) => { e.preventDefault(); zone.classList.add("drag"); };
  zone.ondragleave = () => zone.classList.remove("drag");
  zone.ondrop = (e) => { e.preventDefault(); zone.classList.remove("drag"); setFile(e.dataTransfer.files[0]); };
  $("#up-cancel", mask).onclick = close;
  $("#up-ok", mask).onclick = async () => {
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", $("#up-name", mask).value);
    $("#up-ok", mask).disabled = true;
    $("#up-ok", mask).textContent = "上传中…";
    try {
      const r = await api.upload("/api/workloads/upload", fd);
      close();
      toast("上传成功,正在打开…", "ok");
      location.hash = `/wl/${r.workload.id}`;
    } catch (e) { toast(e.message, "err"); $("#up-ok", mask).disabled = false; $("#up-ok", mask).textContent = "上传"; }
  };
}

/* ---------------- 程序详情 ---------------- */
async function viewWorkloadDetail(wid) {
  const page = $("#page");
  document.title = "程序详情 · MosBox 控制台";
  page.innerHTML = loadingHTML();
  let w;
  try {
    const r = await api.get(`/api/workloads/${wid}`);
    w = r.workload;
  } catch (e) {
    page.innerHTML = `<div class="empty"><div class="big">🫥</div><p>${esc(e.message)}</p><p><a href="#/workloads">← 返回列表</a></p></div>`;
    return;
  }
  $("#page-title").textContent = w.name;
  let tab = "logs";
  page.innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;flex-wrap:wrap">
      <button class="btn btn-icon btn-sm" onclick="location.hash='/workloads'">${I("back", 15)}</button>
      <h2 style="font-size:17px">${esc(w.name)} <span class="mono" style="font-size:12px;color:var(--text-faint)">${escAttr(w.id)}</span></h2>
      ${statusPill(w.status)}
      <div class="spacer" style="flex:1"></div>
      ${w.status === "running"
        ? `<button class="btn btn-sm" onclick="wlAction('${escAttr(w.id)}','stop')">${I("stop", 13)}停止</button>
           <button class="btn btn-sm btn-primary" onclick="wlAction('${escAttr(w.id)}','restart')">${I("restart", 13)}重启</button>`
        : `<button class="btn btn-sm btn-primary" onclick="wlAction('${escAttr(w.id)}','start')">${I("play", 13)}启动</button>`}
    </div>
    <div class="tabs" id="wl-tabs">
      <div class="tab" data-t="logs">日志控制台</div>
      <div class="tab" data-t="files">文件</div>
      <div class="tab" data-t="cfg">配置</div>
    </div>
    <div id="wl-body"></div>`;
  $$("#wl-tabs .tab").forEach(t => t.onclick = () => { tab = t.dataset.t; $$("#wl-tabs .tab").forEach(x => x.classList.toggle("active", x === t)); renderTab(); });
  $(`#wl-tabs .tab[data-t="${tab}"]`).classList.add("active");

  const renderTab = () => {
    const body = $("#wl-body");
    if (tab === "logs") renderWlLogs(body, w);
    else if (tab === "files") renderFileManager(body, `/api/workloads/${wid}/files`, `/api/workloads/${wid}/file`,
      `/api/workloads/${wid}/file-action`, `/api/workloads/${wid}/files/upload`, `/api/workloads/${wid}/download`);
    else renderWlCfg(body, w);
  };
  renderTab();
}

function renderWlLogs(body, w) {
  body.innerHTML = `
    <div class="card">
      <div class="card-title">${I("term")}实时输出 <span class="hint">PID ${w.pid || "-"} · 运行 ${fmtUptime(w.uptime)} · 内存 ${fmtBytes((w.stats?.memory_mb || 0) * 1048576)} · CPU ${w.stats?.cpu_percent || 0}%</span>
        <div style="margin-left:auto;display:flex;gap:6px;align-items:center">
          <label style="font-size:12px;color:var(--text-dim);display:flex;gap:5px;align-items:center"><input type="checkbox" id="log-follow" checked> 自动滚动</label>
          <button class="btn btn-sm" id="log-clear">${I("trash", 12)}清屏</button>
        </div></div>
      <div class="log-view" id="log-view" style="min-height:44vh"></div>
      <div class="stdin-bar">
        <input class="input" id="stdin-input" placeholder="向程序 stdin 发送一行输入,回车发送…">
        <button class="btn" id="stdin-send">发送</button>
      </div>
    </div>`;
  const view = $("#log-view");
  connectLogWs(w.id, view, () => $("#log-follow")?.checked);
  $("#stdin-send").onclick = () => wlStdin(w.id);
  $("#stdin-input").onkeydown = (e) => { if (e.key === "Enter") wlStdin(w.id); };
  $("#log-clear").onclick = () => { view.textContent = ""; };
}

function connectLogWs(wid, view, followFn) {
  if (S.logWs) { try { S.logWs.close(); } catch {} S.logWs = null; }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/ws/logs/${wid}`);
  S.logWs = ws;
  let started = false;
  ws.onopen = () => {
    // 先拉历史
    api.get(`/api/workloads/${wid}/logs?lines=300`).then(r => {
      if (ws.readyState !== 1 || started) return;
      view.textContent = (r.lines || []).join("\n") + "\n";
      if (followFn()) view.scrollTop = view.scrollHeight;
      started = true;
    }).catch(() => {});
  };
  ws.onmessage = (e) => {
    let msg; try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.type === "log") {
      const atEnd = view.scrollTop + view.clientHeight >= view.scrollHeight - 30;
      view.textContent += msg.data;
      if (view.textContent.length > 300000) view.textContent = view.textContent.slice(-200000);
      if ((followFn() || atEnd)) view.scrollTop = view.scrollHeight;
    }
  };
  ws.onclose = () => { if (S.logWs === ws) S.logWs = null; };
}

async function wlStdin(wid) {
  const input = $("#stdin-input");
  const val = input.value + "\n";
  input.value = "";
  try { await api.post(`/api/workloads/${wid}/stdin`, { input: val }); }
  catch (e) { toast(e.message, "err"); }
}

function renderWlCfg(body, w) {
  body.innerHTML = `
    <div class="grid grid-2">
      <div class="card">
        <div class="card-title">${I("gear")}程序配置</div>
        <div class="form-row"><label>名称</label><input class="input" id="cf-name" value="${escAttr(w.name)}"></div>
        <div class="form-row"><label>入口文件(仅自定义命令为空时生效)</label><input class="input" id="cf-entry" value="${escAttr(w.entrypoint)}"></div>
        <div class="form-row"><label>自定义启动命令(如 node index.js / ./server)</label><input class="input" id="cf-cmd" value="${escAttr(w.command || "")}" placeholder="留空则运行入口文件"></div>
        <div class="form-row"><label style="display:flex;align-items:center;gap:10px">崩溃自动拉活
          <span class="switch"><input type="checkbox" id="cf-auto" ${w.auto_restart ? "checked" : ""}><span class="track"></span></span></label></div>
        <div class="form-row"><label style="display:flex;align-items:center;gap:10px">面板启动时自动运行
          <span class="switch"><input type="checkbox" id="cf-boot" ${w.autostart ? "checked" : ""}><span class="track"></span></span></label></div>
        <button class="btn btn-primary" id="cf-save">${I("save", 14)}保存配置</button>
      </div>
      <div class="card">
        <div class="card-title">${I("info")}运行信息</div>
        <div class="kv">
          <div class="row"><span class="k">程序 ID</span><span class="v">${escAttr(w.id)}</span></div>
          <div class="row"><span class="k">状态</span><span class="v">${esc(w.status)}${w.pid ? ` · PID ${w.pid}` : ""}</span></div>
          <div class="row"><span class="k">累计重启</span><span class="v">${w.restarts || 0} 次</span></div>
          <div class="row"><span class="k">上次退出码</span><span class="v">${w.last_exit ?? "-"}</span></div>
          <div class="row"><span class="k">创建时间</span><span class="v">${fmtTime(w.created_at)}</span></div>
          <div class="row"><span class="k">依赖安装</span><span class="v">检测到 requirements.txt 自动 pip install</span></div>
        </div>
        <div style="margin-top:16px">
          <div class="card-title">${I("key")}环境变量(每行 KEY=VALUE)</div>
          <textarea class="input" id="cf-env" rows="6" placeholder="TOKEN=xxx&#10;DEBUG=1">${esc(Object.entries(w.env || {}).map(([k, v]) => `${k}=${v}`).join("\n"))}</textarea>
        </div>
      </div>
    </div>`;
  $("#cf-save").onclick = async () => {
    const env = {};
    $("#cf-env").value.split("\n").forEach(line => {
      line = line.trim();
      if (!line || line.startsWith("#")) return;
      const i = line.indexOf("=");
      if (i > 0) env[line.slice(0, i).trim()] = line.slice(i + 1).trim();
    });
    try {
      await api.put(`/api/workloads/${w.id}`, {
        name: $("#cf-name").value, entrypoint: $("#cf-entry").value,
        command: $("#cf-cmd").value, auto_restart: $("#cf-auto").checked,
        autostart: $("#cf-boot").checked, env,
      });
      toast("配置已保存(重启后生效)", "ok");
    } catch (e) { toast(e.message, "err"); }
  };
}

/* ---------------- 文件管理器(通用) ---------------- */
function renderFileManager(body, listApi, fileApi, actionApi, uploadApi, downloadApi, base = "") {
  let cwd = base;
  body.innerHTML = `
    <div class="card fm">
      <div class="fm-toolbar">
        <div class="fm-crumbs" id="fm-crumbs"></div>
        <button class="btn btn-sm" id="fm-mkdir">${I("plus", 12)}新建目录</button>
        <button class="btn btn-sm btn-primary" id="fm-upload-btn">${I("upload", 12)}上传文件</button>
        <input type="file" id="fm-file" style="display:none" ${uploadApi ? "" : "disabled"}>
      </div>
      <div class="fm-list" id="fm-list"></div>
    </div>`;
  const listEl = $("#fm-list", body);

  const crumbs = () => {
    const el = $("#fm-crumbs", body);
    const parts = cwd ? cwd.split("/") : [];
    let html = `<a data-p="">🏠 根目录</a>`;
    let acc = "";
    parts.forEach(p => { acc += (acc ? "/" : "") + p; html += ` <span style="color:var(--text-faint)">/</span> <a data-p="${escAttr(acc)}">${esc(p)}</a>`; });
    el.innerHTML = html;
    $$("a", el).forEach(a => a.onclick = () => load(a.dataset.p));
  };

  const ops = (rel, isDir) => `
    <div class="ops">
      ${!isDir ? `<button class="btn btn-sm btn-icon" title="下载" onclick="location='${downloadApi}?path=${encodeURIComponent(rel)}'">${I("download", 12)}</button>` : ""}
      <button class="btn btn-sm btn-icon" title="重命名" data-op="rename" data-p="${escAttr(rel)}">${I("edit", 12)}</button>
      <button class="btn btn-sm btn-icon btn-danger" title="删除" data-op="delete" data-p="${escAttr(rel)}">${I("trash", 12)}</button>
    </div>`;

  const load = async (path) => {
    cwd = path || "";
    crumbs();
    listEl.innerHTML = loadingHTML();
    try {
      const r = await api.get(`${listApi}?path=${encodeURIComponent(cwd)}`);
      if (!r.items.length) { listEl.innerHTML = `<div class="fm-empty">空目录</div>`; return; }
      listEl.innerHTML = r.items.map(it => `
        <div class="fm-row" data-name="${escAttr(it.name)}" data-type="${it.type}" data-p="${escAttr(cwd ? cwd + "/" + it.name : it.name)}">
          <span class="ic" style="color:${it.type === "dir" ? "var(--warn)" : "var(--accent-3)"}">${I(it.type === "dir" ? "folder" : "file", 15)}</span>
          <span class="name">${esc(it.name)}</span>
          <span class="size">${it.type === "file" ? fmtBytes(it.size) : ""}</span>
          <span class="mtime">${fmtTime(it.mtime)}</span>
          ${ops(cwd ? cwd + "/" + it.name : it.name, it.type === "dir")}
        </div>`).join("");
      $$(".fm-row", listEl).forEach(row => {
        row.onclick = async (e) => {
          if (e.target.closest(".ops") || e.target.closest("button")) return;
          const p = row.dataset.p;
          if (row.dataset.type === "dir") { load(p); return; }
          openEditor(p, row.dataset.name);
        };
      });
      $$("[data-op]", listEl).forEach(btn => btn.onclick = async (e) => {
        e.stopPropagation();
        const op = btn.dataset.op, p = btn.dataset.p;
        if (op === "delete") {
          if (!await confirmBox("删除", `确认删除 ${p}?`)) return;
          await api.post(actionApi, { action: "delete", path: p });
        } else if (op === "rename") {
          const name = prompt("新名称", p.split("/").pop());
          if (!name) return;
          await api.post(actionApi, { action: "rename", path: p, name });
        }
        load(cwd);
      });
    } catch (e) { listEl.innerHTML = `<div class="fm-empty">${esc(e.message)}</div>`; }
  };

  const openEditor = async (p, name) => {
    try {
      const r = await api.get(`${fileApi}?path=${encodeURIComponent(p)}`);
      const { mask, close } = modal(`${I("edit")} 编辑 ${esc(name)}`, `
        <textarea class="input" id="ed-content" rows="16">${esc(r.content)}</textarea>
        <div class="modal-foot"><button class="btn" id="ed-cancel">取消</button><button class="btn btn-primary" id="ed-save">${I("save", 13)}保存</button></div>`);
      $("#ed-cancel", mask).onclick = close;
      $("#ed-save", mask).onclick = async () => {
        try {
          await api.put(fileApi, { path: p, content: $("#ed-content", mask).value });
          toast("已保存", "ok"); close();
        } catch (e) { toast(e.message, "err"); }
      };
    } catch (e) {
      toast(e.message.includes("过大") ? "文件过大,请下载后编辑" : e.message, "err");
    }
  };

  $("#fm-mkdir", body).onclick = async () => {
    const name = prompt("目录名称");
    if (!name) return;
    try { await api.post(actionApi, { action: "mkdir", path: cwd ? `${cwd}/${name}` : name }); load(cwd); }
    catch (e) { toast(e.message, "err"); }
  };
  const input = $("#fm-file", body);
  $("#fm-upload-btn", body).onclick = () => input.click();
  input.onchange = async () => {
    const f = input.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    fd.append("path", cwd);
    try { await api.upload(uploadApi, fd); toast("已上传", "ok"); load(cwd); }
    catch (e) { toast(e.message, "err"); }
    input.value = "";
  };
  load(cwd);
}

async function viewFiles(el) {
  document.title = "文件管理 · MosBox 控制台";
  $("#page-title").textContent = "文件管理";
  el.innerHTML = loadingHTML();
  el.innerHTML = `<div id="fm-host"></div>`;
  renderFileManager($("#fm-host", el), "/api/files", "/api/files/file", "/api/files/file-action", "/api/files/upload", "/api/files/download");
}

/* ---------------- 终端 ---------------- */
async function viewTerminal(el) {
  document.title = "终端 · MosBox 控制台";
  el.innerHTML = `
    <div class="term-wrap">
      <div class="card">
        <div class="card-title">${I("term")}Web 终端 <span class="hint">真实 PTY · 与 SSH 同一环境 · botkeepctl 可用</span>
          <button class="btn btn-sm" id="term-reconnect" style="margin-left:auto">${I("refresh", 12)}重连</button></div>
        <div class="term-box" id="term-box"></div>
      </div>
    </div>`;
  startTerminal($("#term-box", el));
  $("#term-reconnect", el).onclick = () => { stopTerminal(); startTerminal($("#term-box", el)); };
}

function startTerminal(box) {
  if (!window.Terminal) { box.innerHTML = `<div class="empty"><p>终端组件加载失败</p></div>`; return; }
  stopTerminal();
  const term = new Terminal({
    cursorBlink: true, fontSize: 13.5, fontFamily: '"JetBrains Mono", Menlo, Consolas, monospace',
    theme: { background: "rgba(0,0,0,.32)", foreground: "#c9d2ee", cursor: "#a855f7",
             selectionBackground: "rgba(99,102,241,.4)", black: "#0a0e1a",
             brightBlack: "#6b7494", green: "#34d399", red: "#f87171", yellow: "#fbbf24", blue: "#60a5fa" },
    scrollback: 5000,
  });
  const fit = new window.FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(box);
  S.term = term; S.termFit = fit;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/ws/terminal`);
  S.termWs = ws;
  let opened = false;
  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "start", rows: term.rows, cols: term.cols }));
    term.onData(d => { if (ws.readyState === 1) ws.send(JSON.stringify({ type: "input", data: btoa(unescape(encodeURIComponent(d))) })); });
    term.onResize(({ rows, cols }) => { if (ws.readyState === 1) ws.send(JSON.stringify({ type: "resize", rows, cols })); });
    fit.fit();
  };
  ws.onmessage = (e) => {
    let msg; try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.type === "data") term.write(decodeURIComponent(escape(atob(msg.data))));
    else if (msg.type === "exit") { term.write("\r\n\x1b[38;5;245m[会话已结束,点击「重连」重新打开]\x1b[0m\r\n"); }
  };
  ws.onclose = () => { if (opened) term.write("\r\n\x1b[38;5;245m[连接断开]\x1b[0m\r\n"); };
  opened = true;
  const onResize = () => { try { fit.fit(); } catch {} };
  window.addEventListener("resize", onResize);
  S._termResize = onResize;
  setTimeout(onResize, 60);
}

function stopTerminal() {
  if (S.termWs) { try { S.termWs.close(); } catch {} S.termWs = null; }
  if (S.term) { try { S.term.dispose(); } catch {} S.term = null; }
  if (S._termResize) { window.removeEventListener("resize", S._termResize); S._termResize = null; }
}

/* ---------------- SSH ---------------- */
async function viewSSH(el) {
  document.title = "SSH 服务 · MosBox 控制台";
  el.innerHTML = loadingHTML();
  const data = await api.get("/api/ssh");
  const host = S.host.split(":")[0];
  el.innerHTML = `
    <div class="grid grid-2">
      <div class="card">
        <div class="card-title">${I("ssh")}连接信息 <span class="hint">puressh sshd · 公钥认证</span></div>
        <div class="kv">
          <div class="row"><span class="k">连接命令</span><span class="v"></span></div>
        </div>
        <div class="codebox" style="margin:6px 0 16px"><span>ssh -p ${escAttr(data.port)} ${escAttr(data.user || "root")}@${esc(host)}</span>
          <span class="copy" onclick="copyText('ssh -p ${escAttr(data.port)} ${escAttr(data.user || "root")}@${escAttr(host)}')">${I("copy", 14)}</span></div>
        <div class="kv">
          <div class="row"><span class="k">服务状态</span><span class="v" style="color:${data.self_test?.ok ? "var(--ok)" : "var(--err)"}">${data.self_test?.ok ? "● 正常" : "● 异常"} · ${esc(data.self_test?.detail || "")}</span></div>
          <div class="row"><span class="k">主机密钥指纹</span><span class="v">${esc(data.host_key_fp)}</span></div>
          <div class="row"><span class="k">端口</span><span class="v">${data.port}(与面板共用,raddy 自动分流)</span></div>
          <div class="row"><span class="k">SFTP</span><span class="v">${data.allow_sftp ? "已启用(连接后直接 sftp)" : "已禁用"}</span></div>
        </div>
        <div style="margin-top:14px;display:flex;flex-wrap:wrap;gap:6px">
          ${data.features.map(f => `<span class="pill pill-stopped">${esc(f)}</span>`).join("")}
        </div>
      </div>
      <div class="card">
        <div class="card-title">${I("key")}添加公钥</div>
        <div class="form-row"><label>公钥内容(OpenSSH one-line 格式)</label>
          <textarea class="input" id="ssh-key" rows="3" placeholder="ssh-ed25519 AAAA... 备注"></textarea></div>
        <div class="form-row"><label>备注名称</label><input class="input" id="ssh-key-name" placeholder="我的笔记本"></div>
        <button class="btn btn-primary" id="ssh-add">${I("plus", 14)}添加并生效</button>
        <div style="margin-top:18px">
          <div class="card-title">${I("key")}已授权公钥 <span class="hint">${data.keys.length} 把 · 保存后自动重载 sshd</span></div>
          ${data.keys.map(k => `
            <div class="fm-row" style="padding:7px 6px">
              <span class="ic" style="color:var(--accent-3)">${I("key", 14)}</span>
              <span class="name"><b>${esc(k.name)}</b>
                <div class="mono" style="font-size:11px;color:var(--text-faint)">${esc(k.fingerprint)}</div>
                <div class="mono" style="font-size:10.5px;color:var(--text-faint);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:340px">${esc(k.key_line.slice(0, 42))}…</div></span>
              <span class="ops" style="display:flex"><button class="btn btn-sm btn-icon btn-danger" data-del="${escAttr(k.id)}">${I("trash", 12)}</button></span>
            </div>`).join("")}
        </div>
      </div>
    </div>`;
  $("#ssh-add").onclick = async () => {
    try {
      await api.post("/api/ssh/keys", { key: $("#ssh-key").value, name: $("#ssh-key-name").value });
      toast("公钥已添加并生效", "ok");
      navigate();
    } catch (e) { toast(e.message, "err"); }
  };
  $$("[data-del]", el).forEach(b => b.onclick = async () => {
    if (!await confirmBox("移除公钥", "移除后使用该密钥的设备将无法登录。")) return;
    try { await api.del(`/api/ssh/keys/${b.dataset.del}`); toast("已移除", "ok"); navigate(); }
    catch (e) { toast(e.message, "err"); }
  });
}

/* ---------------- 云存储 ---------------- */
async function viewStorage(el) {
  document.title = "云端存储 · MosBox 控制台";
  el.innerHTML = loadingHTML();
  const [conf, backups, pub] = await Promise.all([
    api.get("/api/storage/config"), api.get("/api/storage/backups"), api.get("/api/storage/public")]);
  const r2 = conf.r2;
  el.innerHTML = `
    <div class="grid grid-2">
      <div class="card">
        <div class="card-title">${I("cloud")}Cloudflare R2 <span class="hint">${r2.enabled ? "已配置" : "未配置(备份自动落到本地)"}</span></div>
        <div class="form-row"><label>Account ID</label><input class="input" id="r2-acct" value="${escAttr(r2.account_id)}" placeholder=" Cloudflare 账户 ID"></div>
        <div class="form-row"><label>Bucket</label><input class="input" id="r2-bucket" value="${escAttr(r2.bucket)}" placeholder="桶名称"></div>
        <div class="form-row"><label>Access Key ID</label><input class="input" id="r2-ak" value="${escAttr(r2.account_id ? "" : "")}" placeholder="${r2.has_credentials ? "已保存(留空保持不变)" : "R2 API Access Key"}"></div>
        <div class="form-row"><label>Secret Access Key</label><input class="input" id="r2-sk" type="password" placeholder="${r2.has_credentials ? "已保存(留空保持不变)" : "R2 API Secret Key"}"></div>
        <div class="form-row"><label>公共访问域名(可选,R2 自定义域名 / r2.dev)</label><input class="input" id="r2-base" value="${escAttr(r2.public_base)}" placeholder="https://files.example.com"></div>
        <div style="display:flex;gap:8px">
          <button class="btn btn-primary" id="r2-save">${I("save", 13)}保存</button>
          <button class="btn" id="r2-test">${I("refresh", 13)}测试连通</button>
        </div>
      </div>
      <div class="card">
        <div class="card-title">${I("save")}备份快照 <span class="hint">${backups.backups.length} 份</span>
          <button class="btn btn-sm btn-primary" id="bk-new" style="margin-left:auto">${I("plus", 12)}立即备份</button></div>
        ${backups.backups.length ? `<div class="table-wrap"><table class="table">
          <thead><tr><th>名称</th><th>位置</th><th>大小</th><th>时间</th><th style="width:110px">操作</th></tr></thead>
          <tbody>${backups.backups.map(b => `
            <tr><td><b>${esc(b.name)}</b></td>
            <td><span class="pill ${b.storage === "r2" ? "pill-running" : "pill-stopped"}">${b.storage === "r2" ? "R2 云端" : "本地"}</span></td>
            <td class="mono">${fmtBytes(b.size)}</td><td class="mono">${fmtTime(b.created_at)}</td>
            <td><div style="display:flex;gap:4px">
              <button class="btn btn-sm" data-bk-restore="${escAttr(b.id)}">恢复</button>
              <button class="btn btn-sm btn-icon" title="下载" onclick="location='/api/storage/backups/${escAttr(b.id)}/download'">${I("download", 12)}</button>
              <button class="btn btn-sm btn-icon btn-danger" data-bk-del="${escAttr(b.id)}">${I("trash", 12)}</button></div></td></tr>`).join("")}
          </tbody></table></div>`
        : `<div class="empty"><div class="big">🗄️</div><p>还没有备份 —— 备份包含全部程序文件与配置</p></div>`}
      </div>
    </div>
    <div class="card" style="margin-top:14px">
      <div class="card-title">${I("globe")}对外发布文件 <span class="hint">${pub.backend === "r2" ? "R2 自定义域名直链" : "面板直链 /f/"} · ${pub.items.length} 个</span></div>
      <div style="display:flex;gap:8px;margin-bottom:12px">
        <button class="btn btn-sm btn-primary" id="pub-up">${I("upload", 12)}上传并发布</button>
        <input type="file" id="pub-file" style="display:none">
      </div>
      ${pub.items.length ? `<div class="table-wrap"><table class="table">
        <thead><tr><th>文件</th><th>大小</th><th>链接</th><th style="width:60px"></th></tr></thead>
        <tbody>${pub.items.map(p => `
          <tr><td><b>${esc(p.key)}</b></td><td class="mono">${fmtBytes(p.size)}</td>
          <td class="mono" style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><a href="${escAttr(p.url)}" target="_blank" rel="noopener">${esc(p.url)}</a></td>
          <td><button class="btn btn-sm btn-icon btn-danger" data-pub-del="${escAttr(p.key)}">${I("trash", 12)}</button></td></tr>`).join("")}
        </tbody></table></div>`
      : `<div class="empty" style="padding:24px"><p>上传的文件会获得对外可访问的直链</p></div>`}
    </div>`;

  $("#r2-save").onclick = async () => {
    try {
      await api.put("/api/storage/config", {
        account_id: $("#r2-acct").value, bucket: $("#r2-bucket").value,
        access_key: $("#r2-ak").value || undefined, secret_key: $("#r2-sk").value || undefined,
        public_base: $("#r2-base").value,
      });
      toast("R2 配置已保存", "ok"); navigate();
    } catch (e) { toast(e.message, "err"); }
  };
  $("#r2-test").onclick = async () => {
    toast("测试中…", "info", 1500);
    const r = await api.post("/api/storage/test");
    r.ok ? toast(`✓ 连通正常(桶内 ${r.objects} 个对象)`, "ok") : toast(`✗ ${r.error}`, "err");
  };
  $("#bk-new").onclick = async () => {
    toast("正在打包备份…", "info", 4000);
    try { await api.post("/api/storage/backups", {}); toast("备份完成", "ok"); navigate(); }
    catch (e) { toast(e.message, "err"); }
  };
  $$("[data-bk-del]", el).forEach(b => b.onclick = async () => {
    if (!await confirmBox("删除备份", "确认删除该备份?")) return;
    await api.del(`/api/storage/backups/${b.dataset.bkDel}`); navigate();
  });
  $$("[data-bk-restore]", el).forEach(b => b.onclick = async () => {
    if (!await confirmBox("恢复备份", "将用备份覆盖当前全部程序文件(先停止相关程序)。", false)) return;
    toast("恢复中…", "info", 5000);
    try { await api.post(`/api/storage/backups/${b.dataset.bkRestore}/restore`); toast("已恢复", "ok"); }
    catch (e) { toast(e.message, "err"); }
  });
  $("#pub-up").onclick = () => $("#pub-file").click();
  $("#pub-file").onchange = async () => {
    const f = $("#pub-file").files[0];
    if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    try { const r = await api.upload("/api/storage/public/upload", fd); toast(`已发布:${r.url}`, "ok", 5000); navigate(); }
    catch (e) { toast(e.message, "err"); }
  };
  $$("[data-pub-del]", el).forEach(b => b.onclick = async () => {
    if (!await confirmBox("删除", "确认删除该发布文件?")) return;
    await api.del(`/api/storage/public/${b.dataset.pubDel}`); navigate();
  });
}

/* ---------------- API 密钥 ---------------- */
async function viewKeys(el) {
  document.title = "API 密钥 · MosBox 控制台";
  el.innerHTML = loadingHTML();
  const data = await api.get("/api/keys");
  el.innerHTML = `
    <div class="card">
      <div class="card-title">${I("key")}API 密钥 <span class="hint">bk_live_* · Bearer 方式调用 /api/v1/developer/*</span>
        <button class="btn btn-sm btn-primary" id="ak-new" style="margin-left:auto">${I("plus", 12)}生成密钥</button></div>
      ${data.keys.length ? `<table class="table">
        <thead><tr><th>名称</th><th>令牌</th><th>创建</th><th>最近使用</th><th style="width:60px"></th></tr></thead>
        <tbody>${data.keys.map(k => `
          <tr><td><b>${esc(k.name)}</b></td>
          <td class="mono">${esc(k.token.slice(0, 12))}…${k.token.slice(-4)}</td>
          <td class="mono">${fmtTime(k.created_at)}</td><td class="mono">${k.last_used ? fmtTime(k.last_used) : "未使用"}</td>
          <td><button class="btn btn-sm btn-icon btn-danger" data-ak-del="${escAttr(k.id)}">${I("trash", 12)}</button></td></tr>`).join("")}
        </tbody></table>`
      : `<div class="empty" style="padding:30px"><p>还没有 API 密钥</p></div>`}
      <div style="margin-top:16px;font-size:12.5px;color:var(--text-dim)">
        <div class="card-title" style="font-size:12.5px">${I("info", 14)}调用示例</div>
        <div class="codebox"><span>curl -H "Authorization: Bearer bk_live_xxx" http://${escAttr(S.host)}/api/v1/developer/workloads</span>
        <span class="copy" onclick="copyText('curl -H \\"Authorization: Bearer bk_live_xxx\\" http://${escAttr(S.host)}/api/v1/developer/workloads')">${I("copy", 14)}</span></div>
      </div>
    </div>`;
  $("#ak-new").onclick = async () => {
    const name = prompt("密钥名称", "默认密钥");
    if (!name) return;
    const r = await api.post("/api/keys", { name });
    const { mask, close } = modal(`${I("check")} 密钥已创建`, `
      <p style="color:var(--text-dim);font-size:13px;margin-bottom:10px">请立即保存(仅显示一次):</p>
      <div class="codebox"><span>${esc(r.token)}</span><span class="copy" onclick="copyText('${escAttr(r.token)}')">${I("copy", 14)}</span></div>
      <div class="modal-foot"><button class="btn btn-primary" id="ak-done">完成</button></div>`);
    $("#ak-done", mask).onclick = () => { close(); navigate(); };
  };
  $$("[data-ak-del]", el).forEach(b => b.onclick = async () => {
    if (!await confirmBox("删除密钥", "使用该密钥的调用将立即失效。")) return;
    await api.del(`/api/keys/${b.dataset.akDel}`); navigate();
  });
}

/* ---------------- 设置 ---------------- */
async function viewSettings(el) {
  document.title = "设置 · MosBox 控制台";
  el.innerHTML = loadingHTML();
  const st = await api.get("/api/settings");
  el.innerHTML = `
    <div class="grid grid-2">
      <div class="card">
        <div class="card-title">${I("gear")}基础设置</div>
        <div class="form-row"><label>面板名称</label><input class="input" id="st-name" value="${escAttr(st.panel_name)}"></div>
        <div class="form-row"><label>对外访问地址(可选,展示用)</label><input class="input" id="st-url" value="${escAttr(st.public_url || "")}" placeholder="http://1.2.3.4:8800"></div>
        <div class="form-row"><label>公网端口(重启后生效)</label><input class="input" value="${st.port}" disabled style="opacity:.6"></div>
        <button class="btn btn-primary" id="st-save">${I("save", 13)}保存</button>
      </div>
      <div class="card">
        <div class="card-title">${I("key")}修改管理密码</div>
        <div class="form-row"><label>当前密码</label><input class="input" type="password" id="st-old"></div>
        <div class="form-row"><label>新密码(至少 6 位)</label><input class="input" type="password" id="st-new"></div>
        <button class="btn" id="st-pw">${I("check", 13)}修改密码</button>
        <div style="margin-top:22px">
          <div class="card-title">${I("gear")}守护参数</div>
          <div class="form-row"><label>自动重启最大退避(秒)</label><input class="input" id="st-backoff" type="number" value="${st.supervisor?.backoff_max ?? 30}"></div>
          <div class="form-row"><label>日志单文件大小(MB)</label><input class="input" id="st-logmb" type="number" value="${st.supervisor?.log_rotate_mb ?? 2}"></div>
        </div>
      </div>
    </div>`;
  $("#st-save").onclick = async () => {
    try {
      await api.put("/api/settings", {
        panel_name: $("#st-name").value, public_url: $("#st-url").value,
        supervisor: { backoff_max: +$("#st-backoff").value, log_rotate_mb: +$("#st-logmb").value },
      });
      toast("已保存", "ok");
    } catch (e) { toast(e.message, "err"); }
  };
  $("#st-pw").onclick = async () => {
    try {
      await api.put("/api/settings", { current_password: $("#st-old").value, new_password: $("#st-new").value });
      toast("密码已修改,请重新登录", "ok"); setTimeout(doLogout, 1200);
    } catch (e) { toast(e.message, "err"); }
  };
}

/* ---------------- 启动 ---------------- */
async function boot() {
  try {
    const me = await api.get("/api/auth/me");
    if (!me.authenticated) { viewLogin(); return; }
    S.me = me;
    document.title = `${me.panel_name || "MosBox 控制台"}`;
    $("#app").innerHTML = "";
    await navigate();
  } catch {
    viewLogin();
  }
}

window.addEventListener("hashchange", () => { if (S.me) navigate(); });
boot();

/* 暴露给 onclick 使用 */
Object.assign(window, { wlAction, wlDelete, wlCreateModal, wlUploadModal, copyText });
