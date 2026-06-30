const _cfg = JSON.parse(document.getElementById('page-config').textContent);
const $ = s => document.querySelector(s);
const APPROVAL   = _cfg.approval;
const IS_ADMIN   = _cfg.isAdmin;
const TUNNEL_MODE = _cfg.tunnelMode;
const PIN_SUFFIX  = _cfg.pinSuffix;
let lanUrlShare   = _cfg.lanUrl + PIN_SUFFIX;

const ICON_FILE = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`;
const ICON_DL   = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>`;
const ICON_TRASH= `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 011-1h4a1 1 0 011 1v2"/></svg>`;
const ICON_X    = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;

// ── Gate ──
const gate = $("#gate");
let pollTimer = null;
let _lastPerms = null;
let _permsApplied = false;

function showGate(which) {
  gate.classList.add("show");
  $("#gateLoading").style.display  = which === "loading"  ? "block" : "none";
  $("#gatePending").style.display  = which === "pending"  ? "block" : "none";
  $("#gateWaiting").style.display  = which === "waiting"  ? "block" : "none";
  $("#gateDenied").style.display   = which === "denied"   ? "block" : "none";
  $("#gateError").style.display    = which === "error"    ? "block" : "none";
}
let canSend = IS_ADMIN;
function applyAccess(s) {
  canSend = s.can_send;
  const sendTab = document.querySelector('.tab[data-panel="send"]');
  const recvTab = document.querySelector('.tab[data-panel="receive"]');
  sendTab.classList.toggle("hidden", !s.can_send);
  $("#send").classList.toggle("hidden", !s.can_send);
  recvTab.classList.toggle("hidden", !s.can_receive);
  $("#receive").classList.toggle("hidden", !s.can_receive);
  if (!_permsApplied) {
    _permsApplied = true;
    const first = s.can_send ? sendTab : (s.can_receive ? recvTab : null);
    if (first) first.click();
    else {
      const noperm = document.getElementById("noPermMsg");
      if (noperm) noperm.style.display = "block";
    }
  }
  if (s.can_receive) loadFiles();
  else if (_filePollTimer) { clearInterval(_filePollTimer); _filePollTimer = null; }
}
async function checkAccess() {
  if (!APPROVAL || IS_ADMIN) { gate.classList.remove("show"); loadFiles(); return; }
  try {
    const r = await fetch("/api/access-status");
    if (r.status === 429) { startPolling(); return; }
    const s = await r.json();
    if (s.status === "approved") {
      gate.classList.remove("show");
      const changed = !_lastPerms || _lastPerms.can_send !== s.can_send || _lastPerms.can_receive !== s.can_receive;
      if (changed) {
        _lastPerms = { can_send: s.can_send, can_receive: s.can_receive };
        applyAccess(s);
      }
      startPolling(10000);
    } else if (s.status === "denied") {
      showGate("denied");
    } else if (s.status === "pending") {
      showGate("waiting"); startPolling(3000);
    } else {
      showGate("pending"); startPolling(3000);
    }
  } catch { showGate("error"); if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }
}
function startPolling(interval) {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  pollTimer = setInterval(checkAccess, interval !== undefined ? interval : 3000);
}
document.addEventListener("DOMContentLoaded", () => {
  if (APPROVAL && !IS_ADMIN) showGate("loading");
  checkAccess();
});
async function sendRequest() {
  const name = $("#deviceName").value.trim();
  showGate("waiting"); startPolling();
  try {
    await fetch("/api/request-access", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({name}) });
  } catch { showGate("error"); if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }
}
$("#requestBtn").addEventListener("click", sendRequest);
$("#retryBtn").addEventListener("click", () => {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  showGate("pending");
});
$("#retryErrorBtn").addEventListener("click", () => { showGate("loading"); checkAccess(); });

// ── Modal ──
const overlay = $("#overlay");
let tunnelQrLoaded = false;

function showView(which) {
  $("#viewLan").style.display     = which === "lan"    ? "" : "none";
  $("#viewTunnel").style.display  = which === "tunnel" ? "" : "none";
  $("#viewNoConn").style.display  = which === "none"   ? "" : "none";
}

function loadTunnelQr(tunnelUrl) {
  if (tunnelQrLoaded) return;
  if (tunnelUrl) {
    const img = $("#tunnelQrImg");
    img.src = "/qr.svg?tunnel=1&t=" + Date.now();
    img.onload = () => {
      tunnelQrLoaded = true;
      $("#tunnelLoading").style.display = "none";
      img.style.display = "block";
      $("#tunnelUrlDisplay").textContent = tunnelUrl;
    };
  } else {
    setTimeout(async () => {
      try {
        const d = await fetch("/api/network-info").then(r => r.json());
        loadTunnelQr(d.tunnel_url);
      } catch { setTimeout(() => loadTunnelQr(null), 2000); }
    }, 2000);
  }
}

async function initModal() {
  let info = { same_network: true, tunnel_mode: false, tunnel_url: null, is_admin: IS_ADMIN };
  try { info = await fetch("/api/network-info").then(r => r.json()); } catch {}
  if (info.lan_url) updateLanUrl(info.lan_url);

  const { same_network, tunnel_mode, tunnel_url } = info;
  const is_admin = info.is_admin || IS_ADMIN;

  if (_cfg.auth) {
    const pinBox = $("#pinBox");
    if (pinBox) pinBox.style.display = (same_network || is_admin || tunnel_mode) ? "" : "none";
  }

  if (tunnel_mode) {
    showView("tunnel");
    loadTunnelQr(tunnel_url);
  } else if (same_network || is_admin) {
    showView("lan");
  } else {
    showView("none");
    if (_cfg.auth) { const pb = $("#pinBox"); if (pb) pb.style.display = "none"; }
  }
}

function updateLanUrl(newBase) {
  if (!newBase || TUNNEL_MODE) return;
  lanUrlShare = newBase + PIN_SUFFIX;
  const el = $("#lanUrlDisplay");
  if (el) el.textContent = newBase;
  const qrImg = document.querySelector("#viewLan .qr-wrap img");
  if (qrImg) qrImg.src = "/qr.svg?t=" + Date.now();
}

if (!TUNNEL_MODE) {
  setInterval(async () => {
    try {
      const info = await fetch("/api/network-info").then(r => r.json());
      if (info.lan_url) updateLanUrl(info.lan_url);
    } catch {}
  }, 30000);
}

function copyToClipboard(text) {
  if (!text || text === "—") return;
  navigator.clipboard.writeText(text).then(() => toast("Lien copié !", "ok")).catch(() => toast("Impossible de copier", "error"));
}

$("#copyLanBtn").addEventListener("click", () => copyToClipboard(lanUrlShare));
$("#copyTunnelBtn").addEventListener("click", () => {
  const base = $("#tunnelUrlDisplay").textContent.trim();
  copyToClipboard(base !== "—" ? base + PIN_SUFFIX : null);
});

let modalInitialized = false;
function openModal() {
  overlay.classList.add("show");
  if (!modalInitialized) { modalInitialized = true; initModal(); }
  setTimeout(() => $("#closeModal").focus(), 50);
}
function closeModal() { overlay.classList.remove("show"); }
const _connectBtn = $("#connectBtn"); if (_connectBtn) _connectBtn.addEventListener("click", openModal);
$("#closeModal").addEventListener("click", closeModal);
overlay.addEventListener("click", e => { if (e.target === overlay) closeModal(); });
document.addEventListener("keydown", e => { if (e.key === "Escape" && overlay.classList.contains("show")) closeModal(); });

// ── Tabs ──
document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
    tab.classList.add("active");
    $("#" + tab.dataset.panel).classList.add("active");
    if (tab.dataset.panel === "receive") { loadFiles(); updateBadge(-_unreadCount); }
  });
});

// ── Toast ──
let toastTimer;
function toast(msg, type = "ok", duration = 3000) {
  const el = $("#toast");
  el.textContent = msg; el.className = "toast " + type; el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), duration);
}

function humanSize(n) {
  const u = ["o","Ko","Mo","Go"]; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(1)) + " " + u[i];
}

// ── Upload ──
const dropzone = $("#dropzone"), fileInput = $("#fileInput"), sendBtn = $("#sendBtn"), queue = $("#queue");
let selected = [];

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
fileInput.addEventListener("change", () => addFiles(fileInput.files));
["dragenter","dragover"].forEach(ev => dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.add("drag"); }));
["dragleave","drop"].forEach(ev => dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.remove("drag"); }));
dropzone.addEventListener("drop", e => addFiles(e.dataTransfer.files));

function addFiles(list) { for (const f of list) selected.push(f); renderQueue(); }

function renderQueue() {
  queue.innerHTML = "";
  selected.forEach((f, idx) => {
    const item = document.createElement("div");
    item.className = "item";
    item.innerHTML = `
      <div class="item-icon">${ICON_FILE}</div>
      <div class="meta">
        <div class="name">${escapeHtml(f.name)}</div>
        <div class="sub">${humanSize(f.size)}</div>
        <div class="progress"><div></div></div>
      </div>
      <div class="actions">
        <button class="icon-btn danger" title="Retirer">${ICON_X}</button>
      </div>`;
    item.querySelector(".icon-btn.danger").addEventListener("click", () => { selected.splice(idx, 1); renderQueue(); });
    queue.appendChild(item);
  });
  sendBtn.disabled = selected.length === 0;
  sendBtn.textContent = selected.length ? `Envoyer (${selected.length})` : "Envoyer";
}

sendBtn.addEventListener("click", () => {
  if (!selected.length) return;
  const form = new FormData();
  selected.forEach(f => form.append("files", f));
  const bars = queue.querySelectorAll(".progress > div");
  sendBtn.disabled = true;
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload");
  xhr.upload.addEventListener("progress", e => {
    if (e.lengthComputable) bars.forEach(b => b.style.width = (e.loaded/e.total*100) + "%");
  });
  xhr.addEventListener("load", () => {
    if (xhr.status === 200) {
      toast("Fichiers envoyés !");
      try { const d = JSON.parse(xhr.responseText); if (d.saved && _knownFiles) d.saved.forEach(n => _knownFiles.add(n)); } catch {}
      selected = []; renderQueue();
    } else {
      let msg = "Échec de l'envoi";
      try { msg = JSON.parse(xhr.responseText).error || msg; } catch {}
      toast(msg, "error"); sendBtn.disabled = false;
    }
  });
  xhr.addEventListener("error", () => { toast("Erreur réseau", "error"); sendBtn.disabled = false; });
  xhr.send(form);
});

// ── Fichiers ──
const fileListEl = $("#fileList");
$("#refreshBtn").addEventListener("click", loadFiles);

let _knownFiles = null;
let _filePollTimer = null;

let _unreadCount = 0;
const recvBadge = document.getElementById("recvBadge");

function updateBadge(delta) {
  const recvTab = document.querySelector('.tab[data-panel="receive"]');
  _unreadCount = Math.max(0, _unreadCount + delta);
  const active = recvTab && recvTab.classList.contains("active");
  if (_unreadCount > 0 && !active) {
    recvBadge.textContent = _unreadCount; recvBadge.classList.add("show");
  } else {
    recvBadge.textContent = ""; recvBadge.classList.remove("show");
  }
}

function notifNewFiles(names) {
  const msg = names.length === 1
    ? `Nouveau fichier : ${names[0]}`
    : `${names.length} nouveaux fichiers déposés`;
  toast(msg, "ok", 5000);
  updateBadge(names.length);
}

async function loadFiles() {
  try {
    const files = await fetch("/api/files").then(r => r.json());
    if (!Array.isArray(files)) return;
    const names = files.map(f => f.name);
    if (_knownFiles !== null) {
      const newOnes = names.filter(n => !_knownFiles.has(n));
      if (newOnes.length) notifNewFiles(newOnes);
    }
    _knownFiles = new Set(names);
    renderFiles(files);
    if (!_filePollTimer) _filePollTimer = setInterval(loadFiles, 8000);
  } catch { toast("Impossible de charger la liste", "error"); }
}

function renderFiles(files) {
  fileListEl.innerHTML = "";
  if (!files.length) {
    fileListEl.innerHTML = `<div class="empty">${ICON_FILE}Aucun fichier pour le moment.</div>`;
    return;
  }
  files.forEach(f => {
    const item = document.createElement("div");
    item.className = "item";
    item.innerHTML = `
      <div class="item-icon">${ICON_FILE}</div>
      <div class="meta">
        <div class="name">${escapeHtml(f.name)}</div>
        <div class="sub">${f.size_human} &middot; ${f.modified_human}</div>
      </div>
      <div class="actions">
        <a class="icon-btn" href="/download/${encodeURIComponent(f.name)}" title="Télécharger">${ICON_DL}</a>
        ${f.can_delete ? `<button class="icon-btn danger" title="Supprimer">${ICON_TRASH}</button>` : ""}
      </div>`;
    const _dangerBtn = item.querySelector(".icon-btn.danger"); if (_dangerBtn) _dangerBtn.addEventListener("click", () => deleteFile(f.name));
    fileListEl.appendChild(item);
  });
}

async function deleteFile(name) {
  if (!confirm("Supprimer " + name + " ?")) return;
  try {
    const res = await fetch("/api/delete/" + encodeURIComponent(name), { method:"POST" });
    if (res.ok) { toast("Supprimé"); loadFiles(); }
    else toast("Suppression impossible", "error");
  } catch { toast("Erreur réseau", "error"); }
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

// ── Activity feed ──
const ICON_UP  = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>`;
const ICON_DEL = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/></svg>`;

function relTime(ts) {
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 5)    return "à l'instant";
  if (s < 60)   return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400)return `${Math.floor(s / 3600)} h`;
  return `${Math.floor(s / 86400)} j`;
}

const activityFeed = $("#activityFeed");
async function loadActivity() {
  try {
    const items = await fetch("/api/activity").then(r => r.json());
    if (!Array.isArray(items) || !items.length) return;
    activityFeed.classList.add("show");
    activityFeed.innerHTML = items.map(item => {
      const isUpload = item.action === "upload";
      const icon = isUpload ? ICON_UP : ICON_DEL;
      const verb = isUpload ? "a envoyé" : "a supprimé";
      const cls  = isUpload ? "act-upload" : "act-delete";
      return `<div class="activity-item ${cls}">
        ${icon}
        <span class="activity-actor">${escapeHtml(item.actor)}</span>
        <span class="activity-verb">&nbsp;${verb}&nbsp;</span>
        <span class="activity-file">${escapeHtml(item.filename)}</span>
        <span class="activity-time">${relTime(item.ts)}</span>
      </div>`;
    }).join("");
  } catch {}
}

loadActivity();
setInterval(loadActivity, 8000);
