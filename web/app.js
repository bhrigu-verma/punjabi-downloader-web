const flash = document.getElementById("flash");
const btnStart = document.getElementById("btnStart");
const btnStop = document.getElementById("btnStop");
const btnRefresh = document.getElementById("btnRefresh");
const pollSelect = document.getElementById("pollSelect");

const appStatePill = document.getElementById("appStatePill");
const statusClock = document.getElementById("statusClock");

const checksEl = document.getElementById("checks");
const configBadges = document.getElementById("configBadges");
const autoResumeMeta = document.getElementById("autoResumeMeta");

const workersList = document.getElementById("workersList");
const workerSelect = document.getElementById("workerSelect");
const logFilter = document.getElementById("logFilter");
const autoScrollToggle = document.getElementById("autoScrollToggle");
const logBox = document.getElementById("logBox");

const kpiTmux = document.getElementById("kpiTmux");
const kpiDone = document.getElementById("kpiDone");
const kpiPending = document.getElementById("kpiPending");
const kpiSuccess = document.getElementById("kpiSuccess");
const kpiWav = document.getElementById("kpiWav");
const kpiAuto = document.getElementById("kpiAuto");

const pathOutput = document.getElementById("pathOutput");
const pathLogs = document.getElementById("pathLogs");
const pathRuntime = document.getElementById("pathRuntime");

const copyOutputPath = document.getElementById("copyOutputPath");
const copyLogPath = document.getElementById("copyLogPath");
const copyRuntimePath = document.getElementById("copyRuntimePath");

const failedCount = document.getElementById("failedCount");
const failedList = document.getElementById("failedList");
const btnCopyFailed = document.getElementById("btnCopyFailed");

const outputsCount = document.getElementById("outputsCount");
const outputsList = document.getElementById("outputsList");

const eventFeed = document.getElementById("eventFeed");

let state = { workers: [] };
let selectedWorker = "";
let refreshInFlight = false;
let autoResumeInFlight = false;
let lastAutoResumeNoticeTs = 0;
let pollIntervalMs = Number(pollSelect.value || 4000);
let pollTimer = null;
let logRawText = "";
let failedUrlsCache = [];
const eventRows = [];

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function formatClock(ts) {
  if (!ts) return "-";
  try {
    return new Date(ts * 1000).toLocaleTimeString();
  } catch {
    return "-";
  }
}

function formatAgo(ts) {
  if (!ts) return "just now";
  const now = Math.floor(Date.now() / 1000);
  const diff = ts - now;
  const abs = Math.abs(diff);
  if (abs < 5) return "just now";

  let value = 0;
  let unit = "s";
  if (abs < 60) {
    value = abs;
    unit = "s";
  } else if (abs < 3600) {
    value = Math.floor(abs / 60);
    unit = "m";
  } else if (abs < 86400) {
    value = Math.floor(abs / 3600);
    unit = "h";
  } else {
    value = Math.floor(abs / 86400);
    unit = "d";
  }
  if (diff > 0) {
    return `in ${value}${unit}`;
  }
  return `${value}${unit} ago`;
}

function showMessage(msg, isError = false) {
  flash.textContent = msg;
  flash.style.color = isError ? "#9d3324" : "#486052";
}

function addEvent(message, tone = "info") {
  const now = Math.floor(Date.now() / 1000);
  eventRows.unshift({ ts: now, message, tone });
  if (eventRows.length > 24) {
    eventRows.length = 24;
  }
  renderEvents();
}

function renderEvents() {
  eventFeed.innerHTML = "";
  if (!eventRows.length) {
    eventFeed.innerHTML = '<div class="list-item empty">No events yet.</div>';
    return;
  }
  eventRows.forEach((ev) => {
    const row = document.createElement("div");
    row.className = "list-item";
    row.innerHTML = `
      <div class="line-strong">${escapeHtml(ev.message)}</div>
      <div class="meta">${formatClock(ev.ts)} (${formatAgo(ev.ts)})</div>
    `;
    eventFeed.appendChild(row);
  });
}

async function copyText(value, label) {
  if (!value) return;
  try {
    await navigator.clipboard.writeText(value);
    showMessage(`${label} copied`);
  } catch {
    showMessage(`Could not copy ${label.toLowerCase()}`, true);
  }
}

async function getJson(url, opts = {}) {
  const res = await fetch(url, opts);
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.message || data.error || `HTTP ${res.status}`);
  }
  return data;
}

function renderChecks(payload) {
  const checks = payload?.checks || {};
  checksEl.innerHTML = "";
  const names = Object.keys(checks).sort();
  if (!names.length) {
    checksEl.innerHTML = '<div class="check bad">No preflight checks returned.</div>';
    return;
  }
  names.forEach((name) => {
    const ok = Boolean(checks[name]);
    const div = document.createElement("div");
    div.className = `check ${ok ? "ok" : "bad"}`;
    div.textContent = `${ok ? "PASS" : "BLOCKED"} · ${name}`;
    checksEl.appendChild(div);
  });
}

function renderConfig(config, preflight, status) {
  configBadges.innerHTML = "";
  const checks = preflight?.checks || {};
  const checkNames = Object.keys(checks);
  const passed = checkNames.filter((k) => checks[k]).length;
  const readiness = checkNames.length ? Math.round((passed / checkNames.length) * 100) : 0;

  const tags = [
    `Readiness ${readiness}%`,
    `${config?.num_workers ?? "?"} worker(s)`,
    `Sequential ${config?.browser_sequential_mode ? "on" : "off"}`,
    `Cookies ${config?.use_cookies ? "on" : "off"}`,
    `WAV validation ${config?.strict_wav_validation ? "strict" : "basic"}`,
    `${config?.audio_sample_rate ?? "?"} Hz mono=${config?.audio_channels === 1 ? "yes" : "no"}`,
  ];

  tags.forEach((tag) => {
    const b = document.createElement("span");
    b.className = "badge";
    b.textContent = tag;
    configBadges.appendChild(b);
  });

  if (!status) {
    autoResumeMeta.textContent = "Auto-resume status unavailable.";
    return;
  }

  const nextAt = status.auto_resume_next_at;
  const nextText = nextAt ? `next attempt ${formatAgo(nextAt)}` : "no next attempt";
  autoResumeMeta.textContent =
    `Auto-resume ${status.auto_resume_enabled ? "enabled" : "disabled"}. ` +
    `Attempts ${status.auto_resume_attempts}/${status.auto_resume_max_attempts}. ` +
    `${status.auto_resume_reason || nextText}.`;
}

function renderState(status) {
  const running = Boolean(status.tmux_running);
  state = status;

  if (running) {
    appStatePill.className = "state-pill running";
    appStatePill.textContent = "Pipeline Running";
  } else if ((status.pending_urls || 0) > 0) {
    appStatePill.className = "state-pill warning";
    appStatePill.textContent = "Waiting / Recoverable";
  } else {
    appStatePill.className = "state-pill idle";
    appStatePill.textContent = "Idle";
  }

  statusClock.textContent = formatClock(status.timestamp);

  kpiTmux.textContent = running ? "RUNNING" : "IDLE";
  kpiDone.textContent = `${status.total_done} / ${status.total_urls}`;
  kpiPending.textContent = `${status.pending_urls || 0}`;
  kpiSuccess.textContent = `${Number(status.success_rate || 0).toFixed(1)}%`;
  kpiWav.textContent = `${status.wav_count || 0}`;
  kpiAuto.textContent = status.auto_resume_enabled
    ? `on (${status.auto_resume_attempts || 0})`
    : "off";

  pathOutput.textContent = status.output_dir || "-";
  pathLogs.textContent = status.log_dir || "-";
  pathRuntime.textContent = status.runtime_dir || "-";
}

function renderWorkers(workers) {
  workersList.innerHTML = "";
  if (!workers || !workers.length) {
    workersList.innerHTML = '<div class="list-item empty">No workers configured.</div>';
    return;
  }

  workers.forEach((w) => {
    const selectedClass = w.id === selectedWorker ? " selected" : "";
    const statusLine = w.status_file || w.last || "no activity yet";
    const box = document.createElement("div");
    box.className = `worker${selectedClass}`;
    box.dataset.worker = w.id;
    box.innerHTML = `
      <div class="worker-top">
        <span class="worker-title">Worker ${escapeHtml(w.id)}</span>
        <span class="worker-progress">${w.processed}/${w.chunk_total} (${w.pct}%)</span>
      </div>
      <div class="bar"><i style="width:${Math.max(0, Math.min(100, Number(w.pct || 0)))}%"></i></div>
      <div class="meta">ok=${w.downloaded} fail=${w.failed} skip=${w.skipped}</div>
      <div class="meta">${escapeHtml(statusLine)}</div>
    `;
    workersList.appendChild(box);
  });
}

function syncWorkerSelect(workers) {
  const ids = (workers || []).map((w) => w.id);
  if (!ids.length) {
    workerSelect.innerHTML = "";
    selectedWorker = "";
    return;
  }

  const needsRebuild =
    workerSelect.options.length !== ids.length ||
    ids.some((id, idx) => workerSelect.options[idx]?.value !== id);

  if (needsRebuild) {
    workerSelect.innerHTML = "";
    ids.forEach((id) => {
      const opt = document.createElement("option");
      opt.value = id;
      opt.textContent = id;
      workerSelect.appendChild(opt);
    });
  }

  if (!selectedWorker || !ids.includes(selectedWorker)) {
    selectedWorker = ids[0];
  }
  workerSelect.value = selectedWorker;
}

function applyLogFilter() {
  const q = (logFilter.value || "").trim().toLowerCase();
  if (!q) {
    logBox.textContent = logRawText || "No logs yet.";
  } else {
    const filtered = (logRawText || "")
      .split("\n")
      .filter((ln) => ln.toLowerCase().includes(q))
      .join("\n");
    logBox.textContent = filtered || "No lines match current filter.";
  }
  if (autoScrollToggle.checked) {
    logBox.scrollTop = logBox.scrollHeight;
  }
}

async function refreshLogs() {
  if (!selectedWorker) {
    logRawText = "No worker selected.";
    applyLogFilter();
    return;
  }
  const data = await getJson(`/api/logs?worker=${encodeURIComponent(selectedWorker)}&lines=220`);
  logRawText = data.content || "No logs yet.";
  applyLogFilter();
}

function renderFailed(payload) {
  failedList.innerHTML = "";
  const rows = [];
  failedUrlsCache = [];

  const workers = payload?.workers || {};
  Object.keys(workers)
    .sort()
    .forEach((wid) => {
      (workers[wid] || []).forEach((url) => {
        rows.push({ worker: wid, url });
        failedUrlsCache.push(url);
      });
    });

  failedCount.textContent = String(rows.length);

  if (!rows.length) {
    failedList.innerHTML = '<div class="list-item empty">No failed URLs. Queue is clean.</div>';
    return;
  }

  rows.slice(0, 120).forEach((row) => {
    const item = document.createElement("div");
    item.className = "list-item";
    item.innerHTML = `
      <div class="line-strong">Worker ${escapeHtml(row.worker)}</div>
      <div class="meta">${escapeHtml(row.url)}</div>
    `;
    failedList.appendChild(item);
  });

  if (rows.length > 120) {
    const more = document.createElement("div");
    more.className = "list-item empty";
    more.textContent = `${rows.length - 120} more failed URLs not shown.`;
    failedList.appendChild(more);
  }
}

function renderOutputs(payload) {
  outputsList.innerHTML = "";
  outputsCount.textContent = String(payload?.count || 0);

  const items = payload?.items || [];
  if (!items.length) {
    outputsList.innerHTML = '<div class="list-item empty">No WAV outputs yet.</div>';
    return;
  }

  items.forEach((row) => {
    const item = document.createElement("div");
    item.className = "list-item";
    item.innerHTML = `
      <div class="line-strong">${escapeHtml(row.name)}</div>
      <div class="meta">${formatBytes(row.size)} · ${formatAgo(row.modified_ts)}</div>
      <div class="meta">${escapeHtml(row.path)}</div>
    `;
    outputsList.appendChild(item);
  });
}

async function maybeAutoResume(status) {
  if (!status || autoResumeInFlight) return;
  if (!status.auto_resume_enabled) return;
  if (status.tmux_running) return;
  if (!status.manual_start_seen) return;
  if ((status.pending_urls || 0) <= 0) return;

  if (!status.auto_resume_can_start) {
    const now = Math.floor(Date.now() / 1000);
    if ((now - lastAutoResumeNoticeTs) > 30 && status.auto_resume_reason) {
      showMessage(`Auto-resume waiting: ${status.auto_resume_reason}`);
      lastAutoResumeNoticeTs = now;
    }
    return;
  }

  autoResumeInFlight = true;
  try {
    const res = await getJson("/api/start?source=auto", { method: "POST" });
    const msg = `Auto-resume: ${res.message || "workers started"}`;
    showMessage(msg);
    addEvent(msg);
    setTimeout(() => {
      refreshAll();
    }, 350);
  } catch (err) {
    const msg = `Auto-resume failed: ${err.message || String(err)}`;
    showMessage(msg, true);
    addEvent(msg, "error");
  } finally {
    autoResumeInFlight = false;
  }
}

function installTimer() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    refreshAll();
  }, pollIntervalMs);
}

async function refreshAll() {
  if (refreshInFlight) return;
  refreshInFlight = true;
  try {
    const [preflight, status, config, failed, outputs] = await Promise.all([
      getJson("/api/preflight"),
      getJson("/api/status"),
      getJson("/api/config"),
      getJson("/api/failed"),
      getJson("/api/recent-outputs?limit=20"),
    ]);

    renderChecks(preflight);
    renderState(status);
    renderConfig(config, preflight, status);
    renderWorkers(status.workers || []);
    syncWorkerSelect(status.workers || []);
    renderFailed(failed);
    renderOutputs(outputs);

    await refreshLogs();
    await maybeAutoResume(status);
  } catch (err) {
    const msg = err.message || String(err);
    showMessage(msg, true);
    addEvent(`Refresh failed: ${msg}`, "error");
  } finally {
    refreshInFlight = false;
  }
}

btnStart.addEventListener("click", async () => {
  try {
    const res = await getJson("/api/start?source=manual", { method: "POST" });
    const msg = res.message || "Pipeline started";
    showMessage(msg);
    addEvent(msg);
    await refreshAll();
  } catch (err) {
    const msg = err.message || String(err);
    showMessage(msg, true);
    addEvent(`Start failed: ${msg}`, "error");
  }
});

btnStop.addEventListener("click", async () => {
  try {
    const res = await getJson("/api/stop", { method: "POST" });
    const msg = res.message || "Pipeline stopped";
    showMessage(msg);
    addEvent(msg);
    await refreshAll();
  } catch (err) {
    const msg = err.message || String(err);
    showMessage(msg, true);
    addEvent(`Stop failed: ${msg}`, "error");
  }
});

btnRefresh.addEventListener("click", refreshAll);

pollSelect.addEventListener("change", () => {
  pollIntervalMs = Number(pollSelect.value || 4000);
  installTimer();
  showMessage(`Auto-refresh interval set to ${Math.round(pollIntervalMs / 1000)}s`);
});

workerSelect.addEventListener("change", async (e) => {
  selectedWorker = e.target.value;
  renderWorkers(state.workers || []);
  await refreshLogs();
});

workersList.addEventListener("click", async (e) => {
  const target = e.target.closest(".worker");
  if (!target) return;
  const wid = target.dataset.worker;
  if (!wid) return;
  selectedWorker = wid;
  workerSelect.value = wid;
  renderWorkers(state.workers || []);
  await refreshLogs();
});

logFilter.addEventListener("input", applyLogFilter);

copyOutputPath.addEventListener("click", () => copyText(pathOutput.textContent, "Output path"));
copyLogPath.addEventListener("click", () => copyText(pathLogs.textContent, "Log path"));
copyRuntimePath.addEventListener("click", () => copyText(pathRuntime.textContent, "Runtime path"));
btnCopyFailed.addEventListener("click", () => copyText(failedUrlsCache.join("\n"), "Failed URL list"));

addEvent("Console ready");
refreshAll();
installTimer();
