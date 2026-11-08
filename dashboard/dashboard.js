"use strict";

const $ = (id) => document.getElementById(id);
const paths = {
  server: '<rect x="3" y="3" width="18" height="8" rx="2"/><rect x="3" y="13" width="18" height="8" rx="2"/><path d="M7 7h.01M7 17h.01m4-10h6m-6 10h6"/>',
  overview: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  disk: '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M3 13h18m-5 3h1m-5 0h1"/>',
  bolt: '<path d="m13 2-9 12h7l-1 8 10-12h-7z"/>',
  book: '<path d="M12 6c-3-3-6-3-9-2v15c3-1 6-1 9 2 3-3 6-3 9-2V4c-3-1-6-1-9 2zm0 0v15"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 9h6v6H9zM9 3v3m6-3v3m-6 12v3m6-3v3M3 9h3m-3 6h3m12-6h3m-3 6h3"/>',
  memory: '<rect x="2" y="7" width="20" height="9" rx="2"/><path d="M6 10v3m4-3v3m4-3v3m4-3v3M6 16v3m4-3v3m4-3v3m4-3v3"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5M5 9a7.5 7.5 0 0 1 13-3l2 3M4 15l2 3a7.5 7.5 0 0 0 13-3"/>',
  immich: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1"/><path d="m4 17 5-4 3 2 4-5 5 5"/>',
  video: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/>',
  jellyfin: '<path d="M4 6a2 2 0 0 1 3-2l12 7a1 1 0 0 1 0 2L7 20a2 2 0 0 1-3-2z"/>',
  navidrome: '<path d="M9 17V5l11-2v12M9 8l11-2"/><ellipse cx="6" cy="18" rx="3" ry="2"/><ellipse cx="17" cy="16" rx="3" ry="2"/>',
  mneme: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 10h10m-10 4h6"/>',
  desktop: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M12 17v4m-5 0h10"/>',
  network: '<rect x="9" y="2" width="6" height="5" rx="1"/><rect x="2" y="16" width="6" height="5" rx="1"/><rect x="16" y="16" width="6" height="5" rx="1"/><path d="M12 7v5m-7 4v-4h14v4"/>',
};
const icon = (key) => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (paths[key] || paths.layers) + '</svg>';
document.querySelectorAll("[data-icon]").forEach((el) => { el.innerHTML = icon(el.dataset.icon); });

const views = { overview: "Overview", workloads: "Workloads", storage: "Storage", worker: "GPU Worker", clio: "Clio", learn: "How it works" };
let snapshot;
let loading = false;
let stale = true;
let busyAction = "";
let expanded = new Set();
let chatBusy = false;
let conversation = [];
let toastTimer;

function escape(value) {
  return String(value ?? "—").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
function bytes(value) {
  if (!Number.isFinite(value)) return "—";
  if (value < 1024) return value + " B";
  const scale = Math.min(4, Math.floor(Math.log(value) / Math.log(1024)));
  const amount = value / 1024 ** scale;
  return amount.toLocaleString(undefined, { maximumFractionDigits: amount < 10 ? 1 : 0 }) + " " + ["B", "KiB", "MiB", "GiB", "TiB"][scale];
}
function pct(value) {
  return Number.isFinite(value) ? (value > 0 && value < 0.1 ? "<0.1%" : value.toFixed(1) + "%") : "—";
}
function total(volumes) {
  return volumes.length && volumes.every((volume) => Number.isFinite(volume.bytes))
    ? volumes.reduce((sum, volume) => sum + volume.bytes, 0) : null;
}
function pill(label) {
  const tone = ["Running", "Online", "Live", "running"].includes(label) ? "" :
    ["Degraded", "Starting", "Stopping", "Busy", "Model missing", "Example data", "Stale data", "Setup required"].includes(label) ? "warn" : "neutral";
  return '<span class="pill ' + tone + '"><i></i>' + escape(label) + '</span>';
}
function progress(value, max, label) {
  return Number.isFinite(value) && Number.isFinite(max) && max > 0
    ? '<progress value="' + Math.min(value, max) + '" max="' + max + '" aria-label="' + escape(label) + '"></progress>' : "";
}
function uptime(value) {
  if (!Number.isFinite(value)) return "—";
  const days = Math.floor(value / 86400);
  const hours = Math.floor(value % 86400 / 3600);
  return days ? days + "d " + hours + "h" : hours ? hours + "h " + Math.floor(value % 3600 / 60) + "m" : Math.floor(value / 60) + "m";
}
function timestamp(seconds) {
  return new Date(seconds * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}
function notify(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 6500);
}
function navigate() {
  const hash = location.hash.slice(1);
  const view = Object.hasOwn(views, hash) ? hash : "overview";
  document.querySelectorAll(".view").forEach((section) => { section.hidden = section.id !== view; });
  document.querySelectorAll("[data-view]").forEach((link) => {
    if (link.dataset.view === view) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  $("crumb").textContent = views[view];
}
window.addEventListener("hashchange", navigate);
navigate();

function metric(iconKey, label, value, description, indicator = "") {
  return '<article class="metric"><div class="metric-header"><span>' + icon(iconKey) + '</span>' + escape(label) +
    '</div><div class="metric-value">' + escape(value) + '</div><div class="metric-footer">' + escape(description) +
    '</div><div class="metric-track">' + indicator + '</div></article>';
}
function renderHost(host) {
  const disks = host.disks.filter((disk) => Number.isFinite(disk.total));
  const used = disks.reduce((sum, disk) => sum + disk.used, 0);
  const capacity = disks.reduce((sum, disk) => sum + disk.total, 0);
  const available = disks.reduce((sum, disk) => sum + disk.free, 0);
  $("host-tag").textContent = host.name;
  $("metrics").innerHTML = [
    metric("cpu", "CPU utilization", pct(host.cpu), host.cores + " logical CPUs · " + host.processor,
      progress(host.cpu, 100, "Host CPU utilization")),
    metric("memory", "Memory in use", bytes(host.memory), bytes(host.memory_total - host.memory) + " available · " + bytes(host.memory_total) + " total",
      progress(host.memory, host.memory_total, "Host memory in use")),
    metric("disk", "Storage available", capacity ? bytes(available) : "—", bytes(capacity) + " mounted capacity",
      progress(used, capacity, "Mounted storage used")),
    metric("clock", "Host uptime", uptime(host.uptime), "Since the last Ubuntu boot"),
  ].join("");
  const markup = host.disks.map((disk) => '<div class="disk"><div class="disk-line"><strong>' +
    escape(disk.label) + '</strong><span>' + (Number.isFinite(disk.total)
      ? escape(bytes(disk.used) + " / " + bytes(disk.total)) : "Mount unavailable") + '</span></div>' +
    progress(disk.used, disk.total, disk.label + " used") + '<div class="disk-meta">' +
    escape(disk.mount + (Number.isFinite(disk.free) ? " · " + bytes(disk.free) + " free" : "")) + '</div></div>').join("");
  $("overview-disks").innerHTML = markup;
  $("all-disks").innerHTML = markup;
}
function volumeList(volumes) {
  return volumes.length ? '<ul>' + volumes.map((volume) => '<li><span>' + escape(volume.label) +
    '</span><span>' + escape(bytes(volume.bytes)) + '</span></li>').join("") + '</ul>' : '<p class="subtext">Waiting for scan.</p>';
}
function appLabel(row, expand, open) {
  const label = '<span class="app-icon ' + escape(row.id) + '">' + icon(row.id) + '</span><span><strong>' +
    escape(row.name) + '</strong><small>' + escape(row.description) + '</small></span>';
  return expand ? '<button class="app-label app-button" data-expand="' + escape(row.id) +
    '" aria-expanded="' + open + '">' + label + '</button>' : '<div class="app-label">' + label + '</div>';
}
function actionButton(key, verb, state, label, enabled) {
  return '<button type="button" data-action="' + escape(verb) + '" data-target="' + escape(key) +
    '" data-expected="' + escape(state) + '"' + (verb === "stop" || verb === "off" ? ' class="danger"' : "") +
    (enabled && !busyAction && !stale ? "" : " disabled") + '>' + escape(label) + '</button>';
}
function serviceTable(rows, target, details) {
  let markup = '<table class="work-table"><thead><tr><th scope="col">Service</th><th scope="col">State</th>' +
    '<th scope="col">Runtime</th><th scope="col">CPU</th><th scope="col">RAM</th><th scope="col">Storage</th>' +
    '<th scope="col" class="action-col">Control</th></tr></thead><tbody>';
  for (const row of rows) {
    const open = details && expanded.has(row.id);
    const isActive = ["Running", "Starting", "Degraded"].includes(row.state);
    let controls = actionButton(row.id, isActive ? "stop" : "start", row.state, isActive ? "Stop" : "Start", row.controls);
    if (row.state === "Degraded") controls = actionButton(row.id, "start", row.state, "Start", row.controls) + controls;
    if (!row.controls) {
      const reason = row.state === "Stopping" ? "Waiting for shutdown" : row.id === "mneme" ? "Bridge required" : "Unavailable";
      controls += '<small>' + reason + '</small>';
    }
    const knownSizes = row.volumes.filter((volume) => Number.isFinite(volume.bytes));
    const partial = knownSizes.length > 0 && knownSizes.length < row.volumes.length;
    markup += '<tr><td>' + appLabel(row, details, open) + '</td><td>' + pill(row.state) + '</td><td>' +
      escape(row.runtime) + '</td><td class="mono">' + escape(pct(row.cpu)) + '</td><td class="mono">' +
      escape(bytes(row.memory)) + '</td><td class="mono">' + escape(bytes(total(knownSizes))) +
      (partial ? '<small>Partial</small>' : '') +
      '</td><td class="action-col">' + controls + '</td></tr>';
    if (open) {
      markup += '<tr class="detail-row"><td colspan="7"><div class="detail-grid"><div><h3>Components</h3><ul>' +
        row.components.map((part) => '<li><span>' + escape(part.name) + ' · ' + escape(part.health) +
          '</span><span>' + escape(pct(part.cpu) + " CPU · " + bytes(part.memory) + " RAM") + '</span></li>').join("") +
        '</ul></div><div><h3>Disk allocation</h3>' + volumeList(row.volumes) + '</div></div></td></tr>';
    }
  }
  // Preserve keyboard focus and scroll position across background polling.
  const focus = target.contains(document.activeElement) ? document.activeElement.dataset : null;
  target.innerHTML = markup + '</tbody></table>';
  if (focus?.expand) target.querySelector('[data-expand="' + CSS.escape(focus.expand) + '"]')?.focus({ preventScroll: true });
  if (focus?.target) target.querySelector('[data-target="' + CSS.escape(focus.target) +
    '"][data-action="' + CSS.escape(focus.action) + '"]')?.focus({ preventScroll: true });
}
function renderServices(rows) {
  serviceTable(rows, $("overview-services"), false);
  serviceTable(rows, $("all-services"), true);
  $("storage-apps").innerHTML = rows.map((row) => '<article><h3>' + escape(row.name) + '</h3>' +
    volumeList(row.volumes) + '</article>').join("");
}
function renderPi(pi) {
  if (!pi) return;
  const dnsOnline = Number.isFinite(pi.dns_ms);
  const state = stale ? "Stale data" : dnsOnline && pi.web_online ? "Online" :
    dnsOnline || pi.web_online ? "Degraded" : "Unreachable";
  $("overview-pi").innerHTML = '<div class="worker-headline"><strong>' + escape(pi.name) + '</strong>' + pill(state) + '</div>' +
    '<div class="pair"><span>Wired address</span><span>' + escape(pi.address) + '</span></div>' +
    '<div class="pair"><span>DNS lookup</span><span>' + (dnsOnline ? escape(pi.dns_ms + " ms") : "Unavailable") + '</span></div>' +
    '<div class="pair"><span>Admin page</span><span>' + (pi.web_online ? "Reachable" : "Unavailable") + '</span></div>' +
    '<p class="worker-note">Last checked at ' + escape(timestamp(pi.checked_at)) + '</p>';
}
function renderWorker(worker) {
  const headline = '<div class="worker-headline"><strong>Gaming PC</strong>' + pill(worker.state) + '</div>';
  const pair = (key, val) => '<div class="pair"><span>' + escape(key) + '</span><span>' + escape(val) + '</span></div>';
  const focus = $("worker-details").contains(document.activeElement) ? document.activeElement.dataset.action : null;
  $("overview-worker").innerHTML = headline + pair("Model", worker.model || "Awaiting worker setup") +
    pair("GPU", "Radeon RX 7900 XT") + pair("Access", "Private Ollama endpoint");
  const loaded = worker.loaded === null ? "Unavailable" : worker.loaded.length
    ? worker.loaded.map((item) => item.name + " · " + bytes(item.vram)).join(", ") : "No models loaded";
  const powerHelp = worker.power_off ? "Windows will shut down when you confirm. Save any open work first." :
    worker.session === "unmanaged" ? "Windows is outside a managed session and the gateway has not offered shutdown." :
    "Power on sends Wake-on-LAN. Windows must allow shutdown and have nobody signed in.";
  $("worker-details").innerHTML = headline + pair("Model", worker.model) + pair("Installed model size", bytes(worker.installed_bytes)) +
    pair("Loaded models / VRAM", loaded) + pair("Managed power session", worker.session || "—") +
    '<div class="power-actions">' + actionButton("worker", "on", worker.state, "Power on", worker.power_on && !chatBusy) +
    actionButton("worker", "off", worker.state, "Shut down", worker.power_off && !chatBusy) + '</div>' +
    '<p class="worker-note">' + escape(powerHelp) + '</p>';
  if (focus) $("worker-details").querySelector('[data-action="' + CSS.escape(focus) + '"]')?.focus({ preventScroll: true });
  $("send-chat").disabled = chatBusy || stale || busyAction === "worker" || !worker.chat;
  $("clear-chat").disabled = chatBusy || !conversation.length;
  $("chat-help").textContent = stale ? "Waiting for a fresh dashboard sample." :
    worker.state === "Setup required" ? "Connect the gaming PC to Mneme to use Ollama here." :
    worker.state === "Model missing" ? "Install " + worker.model + " on the gaming PC to enable chat." :
    worker.chat ? "Prompts go to your private Ollama endpoint and may wake the PC." :
    "Start Ollama on the gaming PC to enable chat.";
}
function render() {
  if (!snapshot) return;
  const connection = stale ? "Stale data" : snapshot.mode === "demo" ? "Example data" : "Live";
  $("connection").className = "pill" + (connection === "Live" ? "" : " warn");
  $("connection").innerHTML = '<i></i>' + escape(connection);
  $("connection").title = "Last snapshot " + timestamp(snapshot.timestamp);
  $("data-warning").hidden = !stale && snapshot.admin;
  $("data-warning-message").textContent = stale ? "The last sample is stale. Controls are paused until the server reconnects." :
    "Mneme start and stop controls need the Ubuntu bridge. Run this once in Terminal and enter the Ubuntu sudo password.";
  $("bridge-command").hidden = stale || snapshot.admin;
  renderHost(snapshot.host);
  renderServices(snapshot.services);
  renderWorker(snapshot.worker);
  renderPi(snapshot.raspberry_pi);
  $("storage-time").textContent = snapshot.storage_timestamp ? "Directory scan at " + timestamp(snapshot.storage_timestamp) : "Waiting for the first directory scan.";
  $("processes").innerHTML = snapshot.processes.length ? '<table><thead><tr><th scope="col">PID</th><th scope="col">Process</th><th scope="col">CPU</th><th scope="col">Resident RAM</th></tr></thead><tbody>' +
    snapshot.processes.map((row) => '<tr><td class="mono">' + escape(row.pid) + '</td><td>' + escape(row.name) +
      '</td><td class="mono">' + escape(pct(row.cpu)) + '</td><td class="mono">' + escape(bytes(row.memory)) + '</td></tr>').join("") +
    '</tbody></table>' : '<p class="empty">Process details unavailable.</p>';
}

async function refresh() {
  if (loading) return;
  loading = true;
  try {
    const response = await fetch("/api/snapshot", { signal: AbortSignal.timeout(6500) });
    if (!response.ok) throw new Error("Waiting for the first sample.");
    snapshot = await response.json();
    stale = snapshot.mode !== "demo" && Date.now() / 1000 - snapshot.timestamp > 30;
  } catch (error) {
    stale = true;
    if (!snapshot) {
      $("overview-services").innerHTML = '<p class="empty">Waiting for the dashboard to collect its first sample.</p>';
      $("connection").textContent = "Waiting for data";
    }
  } finally {
    loading = false;
    render();
  }
}
async function post(url, body) {
  const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The request failed.");
  return data;
}
let clioJob = sessionStorage.getItem("clio-job");
let clioTimer;
let clioPending = false;
let clioResult;
let clioView;
$("clio-mode").value = sessionStorage.getItem("clio-mode") || "review";
$("clio-mode").addEventListener("change", () => sessionStorage.setItem("clio-mode", $("clio-mode").value));

function setClioJob(id) {
  clioJob = id;
  sessionStorage.setItem("clio-job", id);
  $("clio-preview").hidden = true;
  $("clio-player").removeAttribute("src");
  $("clio-player").removeAttribute("data-video-id");
  $("clio-start").value = "";
  clioView = null;
  clioResult = null;
}

function sourcePlayer(videoId, range = "") {
  $("clio-player").src = "https://www.youtube.com/embed/" + videoId + "?playsinline=1" + range;
}

function clipTime(ms) {
  const seconds = Math.floor(ms / 1000);
  return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
}

function showClioResult(status) {
  const result = status.result;
  const videoId = result?.video_id;
  if (!/^[A-Za-z0-9_-]{11}$/.test(videoId || "")) return;
  clioResult = result;
  $("clio-preview").hidden = false;
  if ($("clio-player").dataset.videoId !== videoId) {
    $("clio-player").dataset.videoId = videoId;
    sourcePlayer(videoId);
    $("clio-source").href = "https://www.youtube.com/watch?v=" + videoId;
    $("clio-source").textContent = result.title || "Open on YouTube";
  }

  const moments = result.moments || [];
  const previewing = status.state === "source_review";
  const reviewing = status.state === "review";
  const playable = reviewing || status.state === "ready";
  $("clio-manual").hidden = !reviewing;
  $("clio-review-actions").hidden = !reviewing && !previewing;
  $("clio-generate").hidden = !previewing;
  $("clio-save").hidden = !reviewing || !moments.length;
  $("clio-save").textContent = result.direct_link ? "Submit review" : "Submit review & find next video";
  const rejectLabel = previewing ? "Skip video" : "Reject video";
  $("clio-reject").textContent = result.direct_link ? rejectLabel : rejectLabel + " & find next";
  const view = clioJob + ":" + status.state;
  if (view === clioView) return;
  clioView = view;
  $("clio-clips").replaceChildren();
  $("clio-highlights").hidden = !moments.length;
  for (const moment of moments) {
    const card = document.createElement("fieldset");
    const legend = document.createElement("legend");
    legend.textContent = "Clip " + moment.rank + " · " + clipTime(moment.start_ms) + "–" + clipTime(moment.end_ms);
    card.append(legend);
    if (playable && /^clip-0[1-3]\.mp4$/.test(moment.file || "")) {
      const video = document.createElement("video");
      video.controls = true;
      video.playsInline = true;
      video.preload = "none";
      video.src = "/api/clio/" + encodeURIComponent(clioJob) + "/" + moment.file;
      card.append(video);
    } else {
      const link = document.createElement("a");
      link.href = "https://www.youtube.com/watch?v=" + videoId + "&t=" + Math.floor(moment.start_ms / 1000);
      link.textContent = "Open at this timestamp on YouTube";
      link.target = "_blank";
      link.rel = "noopener";
      card.append(link);
    }
    if (reviewing) {
      const decisions = document.createElement("div");
      decisions.className = "clio-decisions";
      for (const value of ["approve", "reject"]) {
        const label = document.createElement("label");
        const input = document.createElement("input");
        input.type = "radio";
        input.name = "clip-" + moment.rank;
        input.value = value;
        input.required = true;
        label.append(input, value === "approve" ? "Approve" : "Reject");
        decisions.append(label);
      }
      card.append(decisions);
    }
    $("clio-clips").append(card);
  }
  if (reviewing) {
    const slots = [1, 2, 3];
    const occupied = new Set(moments.map((moment) => moment.rank));
    $("clio-slot").replaceChildren(...slots.map((rank) => new Option(
      (occupied.has(rank) ? "Replace clip " : "Add clip ") + rank, rank
    )));
    $("clio-slot").value = slots.find((rank) => !occupied.has(rank)) || 1;
  }
}

async function updateClio() {
  clearTimeout(clioTimer);
  if (!clioJob || clioPending) return;
  const job = clioJob;
  try {
    const response = await fetch("/api/clio/" + encodeURIComponent(job), { signal: AbortSignal.timeout(7000) });
    const status = await response.json();
    if (job !== clioJob || clioPending) return;
    if (!response.ok) throw new Error(status.error || "Clio is unavailable.");
    if (status.next_job_id) {
      setClioJob(status.next_job_id);
      return updateClio();
    }
    const count = status.result?.moments?.length || 0;
    const messages = {
      source_review: "Watch the chosen video, then generate clips or skip it.",
      review: count ? "Preview the clips and choose which to send to your MacBook." : "Choose a start time to cut a clip from this source.",
      ready: `${count} clips ready. Waiting for your MacBook to collect them.`,
      delivered: `${count} clips saved to Desktop → Clio.`,
      rejected: "Video rejected.",
      expired: "Delivery expired after 24 hours. Submit the topic again to regenerate the clips.",
    };
    $("clio-status").textContent = status.message || messages[status.state] || "";
    $("clio-submit").disabled = status.state === "running";
    showClioResult(status);
    if (["running", "ready"].includes(status.state)) clioTimer = setTimeout(updateClio, 4000);
  } catch (error) {
    if (job !== clioJob || clioPending) return;
    $("clio-status").textContent = error.message;
    $("clio-submit").disabled = false;
    clioTimer = setTimeout(updateClio, 8000);
  }
}

async function clioAction(action) {
  if (clioPending) return;
  clioPending = true;
  clearTimeout(clioTimer);
  $("clio-notice").textContent = "";
  const buttons = document.querySelectorAll("#clio form button");
  buttons.forEach((button) => { button.disabled = true; });
  try {
    await action();
  } catch (error) {
    $("clio-notice").textContent = error.message;
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
    clioPending = false;
    updateClio();
  }
}

$("clio-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const topic = $("clio-topic").value.trim();
  if (!topic) return;
  await clioAction(async () => {
    const data = await post("/api/clio", { topic, mode: $("clio-mode").value });
    setClioJob(data.job_id);
    $("clio-status").textContent = "Starting the search…";
  });
});

function clioRange() {
  const parts = $("clio-start").value.trim().split(":");
  if (parts.length > 3 || parts.some((part, index) => !/^\d+$/.test(part) || (index > 0 && Number(part) >= 60))) {
    throw new Error("Enter a start time in seconds, minutes:seconds, or hours:minutes:seconds.");
  }
  const start = parts.reduce((seconds, part) => seconds * 60 + Number(part), 0);
  const duration = clioResult.duration_seconds;
  const end = start + Math.min(30, duration);
  if (end > duration) throw new Error("Choose a start time with enough video left to cut the clip.");
  return { start, end };
}

$("clio-preview-range").addEventListener("click", () => {
  try {
    const { start, end } = clioRange();
    $("clio-notice").textContent = "";
    sourcePlayer(clioResult.video_id, "&start=" + start + "&end=" + Math.ceil(end));
  } catch (error) {
    $("clio-notice").textContent = error.message;
  }
});
$("clio-full-video").addEventListener("click", () => sourcePlayer(clioResult.video_id));

$("clio-manual").addEventListener("submit", (event) => {
  event.preventDefault();
  clioAction(async () => {
    const { start } = clioRange();
    await post("/api/clio/" + encodeURIComponent(clioJob) + "/clips", {
      start_ms: start * 1000, rank: Number($("clio-slot").value),
    });
    clioView = null;
  });
});

function submitClioReview(approved) {
  clioAction(async () => {
    const data = await post("/api/clio/" + encodeURIComponent(clioJob) + "/review", { approved });
    if (data.next_job_id) setClioJob(data.next_job_id);
    $("clio-notice").textContent = approved.length
      ? approved.length + (approved.length === 1 ? " clip queued" : " clips queued") + " for Desktop → Clio."
      : "Video rejected.";
  });
}

$("clio-review").addEventListener("submit", (event) => {
  event.preventDefault();
  const approved = [...new FormData(event.currentTarget)]
    .filter(([, decision]) => decision === "approve")
    .map(([name]) => Number(name.slice(5)));
  submitClioReview(approved);
});
$("clio-reject").addEventListener("click", () => submitClioReview([]));
$("clio-generate").addEventListener("click", () => clioAction(async () => {
  await post("/api/clio/" + encodeURIComponent(clioJob) + "/generate", {});
}));
if (clioJob) updateClio();

$("refresh").addEventListener("click", refresh);
document.addEventListener("click", (event) => {
  const expander = event.target.closest("[data-expand]");
  if (expander) {
    const id = expander.dataset.expand;
    if (expanded.has(id)) expanded.delete(id);
    else expanded.add(id);
    renderServices(snapshot.services);
  }
  const button = event.target.closest("[data-action]");
  if (!button || button.disabled || !snapshot || stale) return;
  const { target: id, action, expected } = button.dataset;
  const name = id === "worker" ? "the gaming PC" : snapshot.services.find((row) => row.id === id)?.name;
  const verb = { on: "Power on", off: "Shut down", stop: "Stop", start: "Start" }[action];
  const dialog = $("confirm");
  $("confirm-title").textContent = verb + " " + name + "?";
  $("confirm-message").textContent = id === "worker"
    ? (action === "on" ? "The dashboard will send Wake-on-LAN if Ollama is unreachable and wait for the worker."
      : "The Windows gateway will request a graceful shutdown. Save any open work before continuing.")
    : action === "stop" ? (id === "mneme" ? "Wait for any imports or card generation to finish. Stopping Mneme clears temporary uploads." :
      "Active streams or jobs in this workload may be interrupted.") : "Start the existing workload and check its health here.";
  $("confirm-action").textContent = verb;
  $("confirm-action").className = ["stop", "off"].includes(action) ? "danger" : "primary";
  dialog.returnValue = "";
  dialog.showModal();
  dialog.addEventListener("close", async () => {
    if (dialog.returnValue !== "confirm" || busyAction || stale) return;
    busyAction = id;
    render();
    notify(verb + " requested…");
    try {
      const data = await post("/api/action", { id, action, expected });
      notify(data.message);
    } catch (error) {
      notify(error.message);
    } finally {
      busyAction = "";
      await refresh();
    }
  }, { once: true });
});

function appendTurn(role, text) {
  const turn = document.createElement("article");
  turn.className = "chat-turn " + role;
  const title = document.createElement("strong");
  title.textContent = role === "user" ? "You" : "Ollama";
  turn.append(title);
  const addText = (value) => {
    if (!value) return;
    const p = document.createElement("p");
    p.textContent = value;
    turn.append(p);
  };
  const fences = /\x60\x60\x60([\w.+-]*)[^\S\n]*\n([\s\S]*?)\x60\x60\x60/g;
  let offset = 0;
  if (role === "assistant") {
    for (const match of text.matchAll(fences)) {
      addText(text.slice(offset, match.index));
      const block = document.createElement("div");
      block.className = "code-block";
      const header = document.createElement("header");
      header.textContent = match[1] || "Code";
      const copy = document.createElement("button");
      copy.type = "button";
      copy.textContent = "Copy code";
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(match[2]);
          notify("Code copied.");
        } catch {
          notify("Select the code to copy it manually.");
        }
      });
      const pre = document.createElement("pre");
      pre.textContent = match[2];
      header.append(copy);
      block.append(header, pre);
      turn.append(block);
      offset = match.index + match[0].length;
    }
  }
  addText(text.slice(offset));
  $("chat-log").append(turn);
}
function renderChat(waiting = false) {
  $("chat-log").replaceChildren();
  if (!conversation.length) {
    $("chat-log").innerHTML = '<div class="chat-empty"><div><strong>Start a local conversation</strong>Try “Write a Python script that prints the free disk space and explain each line.”</div></div>';
  }
  for (const message of conversation) appendTurn(message.role, message.content);
  if (waiting) appendTurn("assistant", "Waiting for the worker. Waking the PC and loading a model can take a minute.");
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
}
$("clear-chat").addEventListener("click", () => {
  conversation = [];
  renderChat();
  if (snapshot) renderWorker(snapshot.worker);
});
$("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const prompt = $("prompt").value.trim();
  if (!prompt || chatBusy || stale || !snapshot?.worker.chat) return;
  chatBusy = true;
  conversation.push({ role: "user", content: prompt });
  $("prompt").value = "";
  renderChat(true);
  renderWorker(snapshot.worker);
  try {
    const history = conversation.slice(-9);
    while (history.reduce((count, message) => count + message.content.length, 0) > 12000) history.splice(0, 2);
    const data = await post("/api/chat", { messages: history });
    conversation.push({ role: "assistant", content: data.text });
  } catch (error) {
    conversation.pop();
    $("prompt").value = prompt;
    notify(error.message);
  } finally {
    chatBusy = false;
    renderChat();
    renderWorker(snapshot.worker);
    refresh();
  }
});
$("send-chat").disabled = true;
$("clear-chat").disabled = true;
renderChat();
refresh();
setInterval(refresh, 8000);
