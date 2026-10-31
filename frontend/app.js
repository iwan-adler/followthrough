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
  const d = new Date(iso.slice(0, 10) + "T00:00:00");
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function loading(el, text = "Loading…") {
  el.innerHTML = `<div class="empty">${esc(text)}</div>`;
}

async function withButton(btn, busyText, fn) {
  const label = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> ${esc(busyText)}`;
  try {
    return await fn();
  } finally {
    btn.disabled = false;
    btn.innerHTML = label;
  }
}

// --- tabs -------------------------------------------------------------------------
const TAB_LOADERS = { review: () => loadReview(), memory: () => loadMemory() };

function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  TAB_LOADERS[name]?.();
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

async function refreshBadges() {
  try {
    const open = await api("/api/conflicts?open_only=true");
    const tab = document.querySelector('.tab[data-tab="review"]');
    tab.innerHTML = `Review${open.length ? `<span class="count">${open.length}</span>` : ""}`;
  } catch { /* badge is cosmetic */ }
}

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

function showNewError(msg) {
  const err = $("#m-error");
  err.textContent = msg;
  err.hidden = !msg;
}

$("#m-extract").addEventListener("click", async (e) => {
  showNewError("");
  const transcript = $("#m-transcript").value.trim();
  if (!transcript) return showNewError("Dictate or paste a transcript first.");
  try {
    const data = await withButton(e.currentTarget, "Extracting…", () =>
      api("/api/extract", {
        method: "POST",
        body: { transcript, title: $("#m-title").value.trim() || "Untitled meeting", meeting_date: $("#m-date").value },
      }));
    reviewMeetingId = data.id;
    $("#m-transcript").value = "";
    $("#m-title").value = "";
    showTab("review");
  } catch (err) {
    showNewError(err.message);
  } finally {
    loadHealth();
    refreshBadges();
  }
});

$("#demo-load").addEventListener("click", async (e) => {
  showNewError("");
  try {
    const res = await withButton(e.currentTarget, "Loading…", () => api("/api/demo/load", { method: "POST" }));
    reviewMeetingId = res.meetings.find((m) => m.status === "staged")?.id ?? null;
    refreshBadges();
    showTab("review");
  } catch (err) {
    showNewError(err.message);
  }
});

$("#demo-sample").addEventListener("click", async () => {
  try {
    const samples = await api("/api/demo/transcripts");
    const s = samples[samples.length - 1];
    $("#m-title").value = s.title;
    $("#m-date").value = s.meeting_date;
    $("#m-transcript").value = s.transcript;
  } catch (err) {
    showNewError(err.message);
  }
});

// --- Review -------------------------------------------------------------------------
let reviewMeetingId = null;

async function loadReview() {
  const body = $("#review-body");
  try {
    const staged = (await api("/api/meetings")).filter((m) => m.status === "staged");
    const picker = $("#review-picker");
    if (!staged.length) {
      picker.hidden = true;
      body.innerHTML = `<div class="empty">Nothing waiting for review. Extract a meeting or load the demo.</div>`;
      return;
    }
    if (!staged.some((m) => m.id === reviewMeetingId)) reviewMeetingId = staged[0].id;
    picker.hidden = staged.length < 2;
    $("#review-select").innerHTML = staged
      .map((m) => `<option value="${m.id}" ${m.id === reviewMeetingId ? "selected" : ""}>${esc(m.title)} · ${fmtDate(m.meeting_date)}</option>`)
      .join("");
    if (!body.querySelector(`[data-meeting="${reviewMeetingId}"]`)) loading(body);
    renderReview(await api(`/api/meetings/${reviewMeetingId}`));
  } catch (err) {
    body.innerHTML = `<div class="alert error">${esc(err.message)}</div>`;
  }
}
$("#review-select").addEventListener("change", (e) => {
  reviewMeetingId = Number(e.target.value);
  loadReview();
});

const RESOLUTION_TEXT = {
  supersede: "New decision replaces the earlier one",
  keep: "Earlier decision stands; the new one is set aside",
  dismiss: "Not a real conflict, both stay active",
};

function renderConflict(c) {
  const r = c.resolution;
  const oldStruck = r === "supersede" ? "struck" : "";
  const newStruck = r === "keep" ? "struck" : "";
  const btn = (value, label, cls) =>
    `<button class="btn small ${cls} ${r === value ? "chosen" : ""}" data-resolve="${value}" data-conflict="${c.id}">${label}</button>`;
  return `
    <div class="conflict ${r ? "resolved" : ""}">
      <div class="conflict-head">
        <span class="badge kind-${esc(c.kind)}">${c.kind === "contradiction" ? "⚠ Contradiction" : "↻ Supersession"}</span>
        <span class="why">${esc(c.explanation)}</span>
      </div>
      <div class="versus">
        <div class="side old ${oldStruck}">
          <div class="label">Earlier · ${esc(c.old_meeting_title)} · ${fmtDate(c.old_meeting_date)}</div>
          <div class="text">${esc(c.old_text)}</div>
          <div class="meta">${avatar(c.old_speaker)} ${esc(c.old_speaker)} <span class="badge topic">${esc(c.old_topic)}</span></div>
        </div>
        <div class="arrow">→</div>
        <div class="side new ${newStruck}">
          <div class="label">New · ${esc(c.meeting_title)} · ${fmtDate(c.meeting_date)}</div>
          <div class="text">${esc(c.new_decision)}</div>
          <div class="meta">${avatar(c.new_speaker || "Unknown")} ${esc(c.new_speaker || "Unknown")}</div>
        </div>
      </div>
      <div class="conflict-actions">
        ${btn("supersede", "Supersede old", "primary")}
        ${btn("keep", "Keep old", "ghost")}
        ${btn("dismiss", "Dismiss", "ghost")}
        <span class="muted small">${r ? `✓ ${RESOLUTION_TEXT[r]}. Applied when you confirm.` : "Choose how to resolve before confirming."}</span>
      </div>
    </div>`;
}

function renderDecision(d) {
  return `
    <div class="item">
      <div class="title">${esc(d.text)}</div>
      <div class="meta">
        <span class="badge topic">${esc(d.topic)}</span>
        ${avatar(d.speaker)} ${esc(d.speaker)} · ${confidence(d.confidence)}
      </div>
      ${d.evidence ? `<div class="evidence">“${esc(d.evidence)}”</div>` : ""}
    </div>`;
}

function renderAction(a) {
  return `
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
    </div>`;
}

function renderReview(m) {
  const ex = m.extraction;
  const open = m.open_conflicts;
  $("#review-body").innerHTML = `
    <div data-meeting="${m.id}">
      <div class="card">
        <h2>${esc(m.title)} <span class="muted small">· ${fmtDate(m.meeting_date)}</span></h2>
        <p class="muted">${esc(m.summary)}</p>
        <div class="speakers">${m.speakers.map((s) => `<span class="chip">${avatar(s)}${esc(s)}</span>`).join("")}</div>
      </div>

      ${m.conflicts.length ? `
        <h3 class="section-title">⚠ ${m.conflicts.length} conflict${m.conflicts.length > 1 ? "s" : ""} with earlier decisions</h3>
        ${m.conflicts.map(renderConflict).join("")}` : `
        <div class="alert ok">✓ No conflicts with earlier decisions in memory.</div>`}

      <div class="grid-2" style="margin-top:18px">
        <div class="card"><h3>Decisions (${ex.decisions.length})</h3>${ex.decisions.map(renderDecision).join("") || `<p class="muted small">No decisions found.</p>`}</div>
        <div class="card"><h3>Action items (${ex.action_items.length})</h3>${ex.action_items.map(renderAction).join("") || `<p class="muted small">No action items found.</p>`}</div>
      </div>

      <div class="confirm-bar">
        <span class="msg">${open
          ? `<strong>Resolve ${open} conflict${open > 1 ? "s" : ""}</strong> to confirm. Nothing has been saved yet.`
          : `Nothing is saved to memory or posted anywhere until you confirm.`}</span>
        <button id="confirm-btn" class="btn primary" ${open ? "disabled" : ""}>Confirm &amp; save</button>
      </div>
      <div id="review-error" class="alert error" hidden></div>
    </div>`;
}

$("#review-body").addEventListener("click", async (e) => {
  const resolveBtn = e.target.closest("[data-resolve]");
  const errBox = $("#review-error");
  try {
    if (resolveBtn) {
      await api(`/api/conflicts/${resolveBtn.dataset.conflict}/resolve`, {
        method: "POST", body: { resolution: resolveBtn.dataset.resolve },
      });
      await loadReview();
      refreshBadges();
    } else if (e.target.closest("#confirm-btn")) {
      await withButton(e.target.closest("#confirm-btn"), "Confirming…", () =>
        api(`/api/meetings/${reviewMeetingId}/confirm`, { method: "POST" }));
      reviewMeetingId = null;
      refreshBadges();
      showTab("memory");
    }
  } catch (err) {
    if (errBox) {
      errBox.textContent = err.message;
      errBox.hidden = false;
    }
  }
});

// --- Memory -------------------------------------------------------------------------
async function loadMemory() {
  const body = $("#memory-body");
  if (!body.children.length) loading(body);
  try {
    const decisions = await api("/api/memory");
    if (!decisions.length) {
      body.innerHTML = `<div class="empty">No decisions in memory yet. Confirm a meeting to start the timeline.</div>`;
      return;
    }
    const byTopic = {};
    decisions.forEach((d) => (byTopic[d.topic] ??= []).push(d));
    body.innerHTML = Object.entries(byTopic).map(([topic, items]) => {
      const changed = items.some((d) => d.status === "superseded");
      return `
        <div class="card topic-block">
          <h3>${esc(topic)} ${changed ? `<span class="badge kind-supersession">changed</span>` : ""}</h3>
          <ul class="timeline">
            ${items.map((d) => `
              <li class="${d.status}">
                <div class="dtext">${esc(d.text)}</div>
                <div class="dmeta">${esc(d.meeting_title)} · ${fmtDate(d.meeting_date)} · ${esc(d.speaker)}
                  ${d.status === "superseded"
                    ? ` · <span>superseded by “${esc(d.superseded_by_text)}”</span>`
                    : ` · <span class="badge ok">active</span>`}
                </div>
              </li>`).join("")}
          </ul>
        </div>`;
    }).join("");
  } catch (err) {
    body.innerHTML = `<div class="alert error">${esc(err.message)}</div>`;
  }
}

loadHealth();
refreshBadges();
