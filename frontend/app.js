// Followthrough frontend. Plain JS, no build step. Talks to the API at API_BASE.
const API_BASE = window.FOLLOWTHROUGH_API || "";

const $ = (sel) => document.querySelector(sel);

async function api(path, options = {}) {
  const res = await fetch(API_BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body && typeof options.body !== "string" ? JSON.stringify(options.body) : options.body,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : data.detail;
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return data;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const AVATAR_COLORS = ["#4f46e5", "#0f9d6b", "#d9480f", "#c2334d", "#0b7285", "#7048e8", "#5c940d"];
function avatar(name) {
  const n = name || "?";
  const color = n === "Unknown" ? "#868e96" : AVATAR_COLORS[[...n].reduce((a, c) => a + c.charCodeAt(0), 0) % AVATAR_COLORS.length];
  return `<span class="avatar" style="background:${color}">${esc(n[0].toUpperCase())}</span>`;
}

function confidence(c) {
  const pct = Math.round((c ?? 0) * 100);
  return `<span class="conf" title="Confidence"><span class="conf-bar ${pct < 70 ? "low" : ""}"><i style="width:${pct}%"></i></span>${pct}%</span>`;
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

// --- tabs -------------------------------------------------------------------------
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

// --- LLM badge --------------------------------------------------------------------
async function loadHealth() {
  const badge = $("#llm-badge");
  try {
    const { llm } = await api("/api/health");
    badge.className = `badge ${llm.live ? "live" : "mock"}`;
    badge.textContent = llm.live ? `● ${llm.provider} · ${llm.model}` : "● mock LLM (no key)";
  } catch {
    badge.className = "badge mock";
    badge.textContent = "API offline";
  }
}

// --- New Meeting --------------------------------------------------------------------
$("#m-date").value = new Date().toISOString().slice(0, 10);

$("#m-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  $("#m-transcript").value = await file.text();
  $("#m-file-name").textContent = file.name;
  if (!$("#m-title").value) $("#m-title").value = file.name.replace(/\.[^.]+$/, "").replace(/[-_]/g, " ");
});

$("#m-extract").addEventListener("click", async () => {
  const btn = $("#m-extract");
  const err = $("#m-error");
  err.hidden = true;
  const transcript = $("#m-transcript").value.trim();
  if (!transcript) {
    err.textContent = "Dictate or paste a transcript first.";
    err.hidden = false;
    return;
  }
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Extracting…`;
  try {
    const data = await api("/api/extract", {
      method: "POST",
      body: { transcript, title: $("#m-title").value.trim() || "Untitled meeting", meeting_date: $("#m-date").value },
    });
    renderExtraction(data);
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Extract decisions & tasks";
    loadHealth();
  }
});

function renderExtraction(data) {
  const ex = data.extraction;
  const decisions = ex.decisions.length
    ? ex.decisions.map((d) => `
        <div class="item">
          <div class="title">${esc(d.text)}</div>
          <div class="meta">
            <span class="badge topic">${esc(d.topic)}</span>
            ${avatar(d.speaker)} ${esc(d.speaker)} · ${confidence(d.confidence)}
          </div>
          ${d.evidence ? `<div class="evidence">“${esc(d.evidence)}”</div>` : ""}
        </div>`).join("")
    : `<p class="muted small">No decisions found.</p>`;

  const actions = ex.action_items.length
    ? ex.action_items.map((a) => `
        <div class="item">
          <div class="title">${esc(a.title)}</div>
          <div class="meta">
            ${a.owner ? `${avatar(a.owner)} ${esc(a.owner)}` : ""}
            ${a.deadline ? `<span class="badge">📅 ${fmtDate(a.deadline)}${a.deadline_text ? ` · “${esc(a.deadline_text)}”` : ""}</span>` : ""}
            ${a.needs_owner ? `<span class="badge flag">needs owner</span>` : ""}
            ${a.needs_deadline ? `<span class="badge flag">needs deadline</span>` : ""}
            ${confidence(a.confidence)}
          </div>
          ${a.evidence ? `<div class="evidence">“${esc(a.evidence)}”</div>` : ""}
        </div>`).join("")
    : `<p class="muted small">No action items found.</p>`;

  const box = $("#m-result");
  box.innerHTML = `
    <div class="card">
      <h2>${esc(data.title)} <span class="muted small">· ${fmtDate(data.meeting_date)}</span></h2>
      <p class="muted">${esc(ex.summary)}</p>
      <div class="speakers">${data.speakers.map((s) => `<span class="chip">${avatar(s)}${esc(s)}</span>`).join("")}</div>
      <p class="small muted" style="margin:10px 0 0">Extracted by <strong>${esc(data.llm_source)}</strong>. Staged as draft #${data.meeting_id}. Nothing has been saved to memory or the tracker yet.</p>
    </div>
    <div class="grid-2">
      <div class="card"><h3>Decisions (${ex.decisions.length})</h3>${decisions}</div>
      <div class="card"><h3>Action items (${ex.action_items.length})</h3>${actions}</div>
    </div>`;
  box.hidden = false;
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

loadHealth();
