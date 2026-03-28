const flash = document.getElementById("flash");
const btnStart = document.getElementById("btnStart");
const btnStop = document.getElementById("btnStop");
const btnRefresh = document.getElementById("btnRefresh");
const checksEl = document.getElementById("checks");
const workersList = document.getElementById("workersList");
const workerSelect = document.getElementById("workerSelect");
const logBox = document.getElementById("logBox");

const kpiTmux = document.getElementById("kpiTmux");
const kpiDone = document.getElementById("kpiDone");
const kpiWav = document.getElementById("kpiWav");
const pathOutput = document.getElementById("pathOutput");
const pathLogs = document.getElementById("pathLogs");
const pathRuntime = document.getElementById("pathRuntime");

let state = { workers: [] };
let selectedWorker = "00";

function showMessage(msg, isError = false) {
  flash.textContent = msg;
  flash.style.color = isError ? "#a93c1c" : "#4d6355";
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
  const checks = payload.checks || {};
  checksEl.innerHTML = "";
  Object.keys(checks).sort().forEach((name) => {
    const ok = !!checks[name];
    const div = document.createElement("div");
    div.className = `check ${ok ? "ok" : "bad"}`;
    div.textContent = `${name}: ${ok ? "OK" : "MISSING"}`;
    checksEl.appendChild(div);
  });
}

function renderWorkers(workers) {
  workersList.innerHTML = "";
  workers.forEach((w) => {
    const box = document.createElement("div");
    box.className = "worker";
    box.innerHTML = `
      <div class="worker-top">
        <strong>Worker ${w.id}</strong>
        <span>${w.processed}/${w.chunk_total} (${w.pct}%)</span>
      </div>
      <div class="bar"><i style="width:${w.pct}%"></i></div>
      <div class="meta">ok=${w.downloaded} fail=${w.failed} skip=${w.skipped}</div>
      <div class="meta">${w.last || "no activity yet"}</div>
    `;
    workersList.appendChild(box);
  });
}

function syncWorkerSelect(workers) {
  const ids = workers.map((w) => w.id);
  if (!ids.length) {
    workerSelect.innerHTML = "";
    return;
  }

  const needsRebuild =
    workerSelect.options.length !== ids.length ||
    ids.some((id, i) => workerSelect.options[i]?.value !== id);

  if (needsRebuild) {
    workerSelect.innerHTML = "";
    ids.forEach((id) => {
      const opt = document.createElement("option");
      opt.value = id;
      opt.textContent = id;
      workerSelect.appendChild(opt);
    });
  }

  if (!ids.includes(selectedWorker)) {
    selectedWorker = ids[0];
  }
  workerSelect.value = selectedWorker;
}

async function refreshStatus() {
  const status = await getJson("/api/status");
  state = status;

  kpiTmux.textContent = status.tmux_running ? "RUNNING" : "STOPPED";
  kpiDone.textContent = `${status.total_done} / ${status.total_urls}`;
  kpiWav.textContent = `${status.wav_count}`;
  pathOutput.textContent = status.output_dir;
  pathLogs.textContent = status.log_dir;
  pathRuntime.textContent = status.runtime_dir;

  renderWorkers(status.workers || []);
  syncWorkerSelect(status.workers || []);
}

async function refreshLogs() {
  if (!selectedWorker) {
    return;
  }
  const data = await getJson(`/api/logs?worker=${encodeURIComponent(selectedWorker)}&lines=180`);
  logBox.textContent = data.content || "No logs yet.";
  logBox.scrollTop = logBox.scrollHeight;
}

async function refreshAll() {
  try {
    const [preflight] = await Promise.all([getJson("/api/preflight")]);
    renderChecks(preflight);
    await refreshStatus();
    await refreshLogs();
  } catch (err) {
    showMessage(err.message || String(err), true);
  }
}

btnStart.addEventListener("click", async () => {
  try {
    const res = await getJson("/api/start", { method: "POST" });
    showMessage(res.message || "Started");
    await refreshAll();
  } catch (err) {
    showMessage(err.message || String(err), true);
  }
});

btnStop.addEventListener("click", async () => {
  try {
    const res = await getJson("/api/stop", { method: "POST" });
    showMessage(res.message || "Stopped");
    await refreshAll();
  } catch (err) {
    showMessage(err.message || String(err), true);
  }
});

btnRefresh.addEventListener("click", refreshAll);

workerSelect.addEventListener("change", async (e) => {
  selectedWorker = e.target.value;
  await refreshLogs();
});

refreshAll();
setInterval(refreshAll, 4000);
