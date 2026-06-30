const _cfg = JSON.parse(document.getElementById('page-config').textContent);
const $ = s => document.querySelector(s);
const TUNNEL_MODE = _cfg.tunnelMode;

let toastTimer;

function toast(msg, type = "ok", duration = 3000) {
  const el = $("#toast");
  el.textContent = msg; el.className = "toast " + type; el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), duration);
}
function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

const ICON_PHONE  = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="2" width="14" height="20" rx="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>`;
const ICON_LAPTOP = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="2" y1="20" x2="22" y2="20"/></svg>`;
const ICON_CHECK  = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg>`;
const ICON_X      = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
const ICON_SAVE   = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>`;
const ICON_REVOKE = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>`;
const ICON_TRASH  = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>`;

async function api(id, body) {
  const res = await fetch("/api/admin/devices/" + id, {
    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)
  });
  return res.ok;
}

function card(d) {
  const label = escapeHtml(d.name || "Appareil sans nom");
  const isMobile = /iphone|android|mobile|phone|tablet|ipad/i.test(d.name || "");
  const statusMap = {
    approved: { cls:"ok",   text:"Approuvé"   },
    pending:  { cls:"warn", text:"En attente" },
    denied:   { cls:"danger", text:"Refusé"   },
  };
  const st = statusMap[d.status] || { cls:"", text:escapeHtml(d.status) };
  const sendChk = d.can_send    ? "checked" : "";
  const recvChk = d.can_receive ? "checked" : "";

  const actions = d.status === "approved" ? `
    <button class="action-btn save"   data-act="update">${ICON_SAVE} Enregistrer</button>
    <button class="action-btn revoke" data-act="revoke">${ICON_REVOKE} Retirer l'accès</button>
    <button class="action-btn delete" data-act="delete">${ICON_TRASH} Supprimer</button>
  ` : `
    <button class="action-btn approve" data-act="approve">${ICON_CHECK} Approuver</button>
    <button class="action-btn deny"    data-act="deny">${ICON_X} Refuser</button>
    <button class="action-btn delete"  data-act="delete">${ICON_TRASH} Supprimer</button>
  `;

  const el = document.createElement("div");
  el.className = "device-card" + (d.status === "pending" ? " is-pending" : "");
  el.innerHTML = `
    <div class="device-top">
      <div class="device-avatar${isMobile ? " is-mobile" : ""}">${isMobile ? ICON_PHONE : ICON_LAPTOP}</div>
      <div class="device-meta">
        <div class="device-name">${label}</div>
        <div class="device-time">Vu ${escapeHtml(d.last_seen_human)}${d.mac ? ` · <span class="mac-addr">${escapeHtml(d.mac)}</span>` : ""}</div>
      </div>
      <span class="status-badge ${st.cls}">${st.text}</span>
    </div>
    ${d.status === "approved" ? `
    <div class="device-perms">
      <span class="perms-label">Permissions</span>
      <label class="toggle-label">
        <input type="checkbox" class="toggle cs" ${sendChk} />
        <span>Envoyer</span>
      </label>
      <label class="toggle-label">
        <input type="checkbox" class="toggle cr" ${recvChk} />
        <span>Recevoir</span>
      </label>
    </div>` : ""}
    <div class="device-actions">${actions}</div>`;

  el.querySelectorAll("button[data-act]").forEach(btn =>
    btn.addEventListener("click", async () => {
      const act = btn.dataset.act;
      let body = { action: act };
      if (act === "approve") {
        body = { action: "approve", can_send: true, can_receive: true };
      } else if (act === "update") {
        body = { action: "update", can_send: el.querySelector(".cs").checked, can_receive: el.querySelector(".cr").checked };
      }
      if (await api(d.id, body)) { toast("Mis à jour"); load(); }
      else toast("Erreur", "error");
    })
  );
  return el;
}

async function load() {
  const res = await fetch("/api/admin/devices");
  if (res.status === 403) { location.href = "/admin/login"; return; }
  const devices = await res.json();

  const pendingEl = $("#pending"), knownEl = $("#known");
  pendingEl.innerHTML = ""; knownEl.innerHTML = "";

  const waiting = devices.filter(d => d.status === "pending");
  const others  = devices.filter(d => d.status !== "pending");

  const pc = $("#pendingCount"), kc = $("#knownCount");
  pc.textContent = waiting.length; pc.style.display = waiting.length ? "inline-flex" : "none";
  kc.textContent = others.length;  kc.style.display = others.length  ? "inline-flex" : "none";

  if (!waiting.length) pendingEl.innerHTML = `<div class="empty">${ICON_PHONE}Aucune demande en attente.</div>`;
  waiting.forEach(d => pendingEl.appendChild(card(d)));

  if (!others.length) knownEl.innerHTML = `<div class="empty">${ICON_LAPTOP}Aucun appareil connu.</div>`;
  others.forEach(d => knownEl.appendChild(card(d)));
}

load();
setInterval(load, 4000);

// ── Notifications dépôt ──
let _knownFiles = null;

function notifNewFiles(names) {
  const msg = names.length === 1
    ? `Nouveau fichier : ${names[0]}`
    : `${names.length} nouveaux fichiers déposés`;
  toast(msg, "ok", 5000);
}

async function pollFiles() {
  try {
    const res = await fetch("/api/files");
    if (!res.ok) return;
    const files = await res.json();
    if (!Array.isArray(files)) return;
    const names = files.map(f => f.name);
    if (_knownFiles !== null) {
      const newOnes = names.filter(n => !_knownFiles.has(n));
      if (newOnes.length) notifNewFiles(newOnes);
    }
    _knownFiles = new Set(names);
  } catch {}
}

pollFiles();
setInterval(pollFiles, 5000);

// ── Vider le partage ──
$("#clearFilesBtn").addEventListener("click", async () => {
  if (!confirm("Supprimer tous les fichiers du dossier partagé ?")) return;
  const res = await fetch("/api/admin/clear-files", { method: "POST" });
  if (res.ok) {
    const d = await res.json();
    toast(`${d.deleted} fichier(s) supprimé(s)`, "ok");
  } else {
    toast("Erreur", "error");
  }
});

// ── Vider la liste des appareils ──
$("#clearDevicesBtn").addEventListener("click", async () => {
  if (!confirm("Supprimer tous les appareils de la liste ? Ils devront être réapprouvés.")) return;
  const res = await fetch("/api/admin/clear-devices", { method: "POST" });
  if (res.ok) { toast("Liste des appareils vidée", "ok"); load(); }
  else toast("Erreur", "error");
});

// ── Modal ──
const overlay = $("#overlay");
function openModal() { overlay.classList.add("show"); setTimeout(() => $("#closeModal").focus(), 50); }
function closeModal() { overlay.classList.remove("show"); }
$("#connectBtn").addEventListener("click", openModal);
$("#closeModal").addEventListener("click", closeModal);
overlay.addEventListener("click", e => { if (e.target === overlay) closeModal(); });
document.addEventListener("keydown", e => { if (e.key === "Escape" && overlay.classList.contains("show")) closeModal(); });

// ── Copier URL ──
function copyUrl(text) {
  if (!text || text === "—") return;
  navigator.clipboard.writeText(text).then(() => toast("Lien copié !", "ok")).catch(() => toast("Impossible de copier", "error"));
}

if (!TUNNEL_MODE) {
  const LAN_PIN_SUFFIX = _cfg.pinSuffix;
  let lanUrlShare = _cfg.lanUrl + LAN_PIN_SUFFIX;
  setInterval(async () => {
    try {
      const info = await fetch("/api/network-info").then(r => r.json());
      if (info.lan_url) {
        lanUrlShare = info.lan_url + LAN_PIN_SUFFIX;
        $("#lanUrl").textContent = info.lan_url;
      }
    } catch {}
  }, 30000);
  $("#copyLanBtn").addEventListener("click", () => copyUrl(lanUrlShare));
}

if (TUNNEL_MODE) {
  $("#copyTunnelBtn").addEventListener("click", () => {
    const base = $("#tunnelUrl").textContent.trim();
    copyUrl(base !== "—" ? base + _cfg.pinSuffix : null);
  });

  // ── Polling URL tunnel ──
  let tunnelReady = false;
  async function pollTunnelUrl() {
    if (tunnelReady) return;
    try {
      const d = await fetch("/api/tunnel-url").then(r => r.json());
      if (d.url) {
        tunnelReady = true;
        $("#tunnelUrl").textContent = d.url;
        const img = $("#tunnelQrImg");
        img.src = "/qr.svg?tunnel=1&t=" + Date.now();
        img.onload = () => {
          $("#tunnelLoading").style.display = "none";
          img.style.display = "block";
        };
        return;
      }
    } catch {}
    setTimeout(pollTunnelUrl, 2000);
  }
  pollTunnelUrl();
}
