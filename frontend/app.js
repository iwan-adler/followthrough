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

// --- Review (editable draft + confirmation gate) ---------------------------------------
let reviewMeetingId = null;
let draft = null;            // the staged meeting being edited
let saveTimer = null;
let savePromise = null;
let dirty = false;

async function loadReview() {
  const body = $("#review-body");
  if (dirty) await saveDraft().catch(() => {});
  try {
    const staged = (await api("/api/meetings")).filter((m) => m.status === "staged");
    const picker = $("#review-picker");
    if (!staged.length) {
      picker.hidden = true;
      draft = null;
      body.innerHTML = `<div class="empty">Nothing waiting for review. Extract a meeting or load the demo.</div>`;
      return;
    }
    if (!staged.some((m) => m.id === reviewMeetingId)) reviewMeetingId = staged[0].id;
    picker.hidden = staged.length < 2;
    $("#review-select").innerHTML = staged
      .map((m) => `<option value="${m.id}" ${m.id === reviewMeetingId ? "selected" : ""}>${esc(m.title)} · ${fmtDate(m.meeting_date)}</option>`)
      .join("");
    if (!body.querySelector(`[data-meeting="${reviewMeetingId}"]`)) loading(body);
    draft = await api(`/api/meetings/${reviewMeetingId}`);
    renderReview();
  } catch (err) {
    body.innerHTML = `<div class="alert error">${esc(err.message)}</div>`;
  }
}
$("#review-select").addEventListener("change", async (e) => {
  if (dirty) await saveDraft().catch(() => {});
  reviewMeetingId = Number(e.target.value);
  loadReview();
});

// Blank rows stay on screen while being filled in but are never sent to the server,
// so map each row to its index among the non-blank decisions (what conflicts refer to).
function serverIndex(list, i, field) {
  if (!list[i][field]?.trim()) return -1;
  return list.slice(0, i).filter((x) => x[field]?.trim()).length;
}

function payload() {
  return {
    title: draft.title,
    extraction: {
      ...draft.extraction,
      decisions: draft.extraction.decisions.filter((d) => d.text.trim()),
      action_items: draft.extraction.action_items.filter((a) => a.title.trim()),
    },
  };
}

function setSaveStatus(text, isError = false) {
  const el = $("#save-status");
  if (el) {
    el.textContent = text;
    el.classList.toggle("error-text", isError);
  }
}

function scheduleSave() {
  dirty = true;
  setSaveStatus("Unsaved changes…");
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveDraft().catch(() => {}), 700);
}

async function saveDraft() {
  clearTimeout(saveTimer);
  if (!dirty) return savePromise;
  dirty = false;
  setSaveStatus("Saving…");
  const id = draft.id;
  savePromise = api(`/api/meetings/${id}/draft`, { method: "PUT", body: payload() })
    .then((m) => {
      if (!draft || draft.id !== id) return;
      draft.conflicts = m.conflicts;
      draft.open_conflicts = m.open_conflicts;
      renderConflicts();
      renderConfirmBar();
      markConflictedDecisions();
      setSaveStatus(dirty ? "Unsaved changes…" : "All changes saved");
      refreshBadges();
    })
    .catch((e) => {
      dirty = true;
      setSaveStatus(`Save failed: ${e.message}`, true);
      throw e;
    });
  return savePromise;
}

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

function renderConflicts() {
  const n = draft.conflicts.length;
  $("#conflicts-box").innerHTML = n
    ? `<h3 class="section-title">⚠ ${n} conflict${n > 1 ? "s" : ""} with earlier decisions</h3>${draft.conflicts.map(renderConflict).join("")}`
    : `<div class="alert ok">✓ No conflicts with earlier decisions in memory.</div>`;
}

function speakerOptions() {
  return `<datalist id="people-list">${draft.speakers.filter((s) => s !== "Unknown").map((s) => `<option value="${esc(s)}">`).join("")}</datalist>`;
}

function renderDecisionRow(d, i) {
  return `
    <div class="item edit" data-kind="decisions" data-i="${i}">
      <div class="edit-main">
        <textarea class="inline title-input" rows="2" data-field="text" placeholder="What was decided?">${esc(d.text)}</textarea>
        <button class="icon-btn" data-delete title="Remove decision">✕</button>
      </div>
      <div class="meta">
        <label class="mini">topic <input class="inline sm" data-field="topic" value="${esc(d.topic)}" placeholder="topic"></label>
        <label class="mini">by <input class="inline sm" data-field="speaker" list="people-list" value="${esc(d.speaker)}" placeholder="speaker"></label>
        ${d.confidence != null ? confidence(d.confidence) : ""}
        <span class="badge kind-contradiction conflict-flag" hidden>conflict</span>
      </div>
      ${d.evidence ? `<div class="evidence">“${esc(d.evidence)}”</div>` : ""}
    </div>`;
}

function actionFlags(a) {
  return `${!a.owner?.trim() ? `<span class="badge flag">needs owner</span>` : ""}${!a.deadline ? `<span class="badge flag">needs deadline</span>` : ""}`;
}

function renderActionRow(a, i) {
  return `
    <div class="item edit" data-kind="action_items" data-i="${i}">
      <div class="edit-main">
        <input class="inline title-input" data-field="title" value="${esc(a.title)}" placeholder="What needs to happen?">
        <button class="icon-btn" data-delete title="Remove action item">✕</button>
      </div>
      <div class="meta">
        <label class="mini">owner <input class="inline sm" data-field="owner" list="people-list" value="${esc(a.owner ?? "")}" placeholder="unassigned"></label>
        <label class="mini">due <input class="inline sm" type="date" data-field="deadline" value="${esc(a.deadline ?? "")}"></label>
        ${a.deadline_text ? `<span class="muted small" title="As spoken">“${esc(a.deadline_text)}”</span>` : ""}
        <span class="flags">${actionFlags(a)}</span>
        ${a.confidence != null ? confidence(a.confidence) : ""}
      </div>
      ${a.evidence ? `<div class="evidence">“${esc(a.evidence)}”</div>` : ""}
    </div>`;
}

function renderLists() {
  const ex = draft.extraction;
  $("#decisions-box").innerHTML = `
    <h3>Decisions (${ex.decisions.length})</h3>
    ${ex.decisions.map(renderDecisionRow).join("") || `<p class="muted small">No decisions found.</p>`}
    <button class="btn small ghost" data-add="decisions">+ Add decision</button>`;
  $("#actions-box").innerHTML = `
    <h3>Action items (${ex.action_items.length})</h3>
    ${ex.action_items.map(renderActionRow).join("") || `<p class="muted small">No action items found.</p>`}
    <button class="btn small ghost" data-add="action_items">+ Add action item</button>`;
  markConflictedDecisions();
}

function markConflictedDecisions() {
  const open = new Set(draft.conflicts.filter((c) => !c.resolution).map((c) => c.new_decision_index));
  document.querySelectorAll('#decisions-box .item[data-kind="decisions"]').forEach((row) => {
    const idx = serverIndex(draft.extraction.decisions, Number(row.dataset.i), "text");
    const flagged = open.has(idx);
    row.classList.toggle("has-conflict", flagged);
    row.querySelector(".conflict-flag").hidden = !flagged;
  });
}

function renderConfirmBar() {
  const open = draft.open_conflicts;
  const items = draft.extraction.action_items.filter((a) => a.title.trim());
  const noOwner = items.filter((a) => !a.owner?.trim()).length;
  const noDate = items.filter((a) => !a.deadline).length;
  const warnings = [noOwner && `${noOwner} without an owner`, noDate && `${noDate} without a deadline`].filter(Boolean);
  $("#confirm-bar").innerHTML = `
    <span class="msg">${open
      ? `<strong>Resolve ${open} conflict${open > 1 ? "s" : ""}</strong> to confirm. Nothing has been saved yet.`
      : `Nothing is saved to memory or posted to the tracker until you confirm.`}
      ${warnings.length ? `<br><span class="warn-text">⚠ ${items.length} task${items.length === 1 ? "" : "s"}: ${warnings.join(", ")}.</span>` : ""}
      <br><span id="save-status" class="muted small">${dirty ? "Unsaved changes…" : "All changes saved"}</span></span>
    <button id="discard-btn" class="btn ghost">Discard draft</button>
    <button id="confirm-btn" class="btn primary" ${open ? "disabled" : ""}>Confirm &amp; create ${items.length} task${items.length === 1 ? "" : "s"}</button>`;
}

function renderReview() {
  $("#review-body").innerHTML = `
    <div data-meeting="${draft.id}">
      <div class="card">
        <input id="draft-title" class="inline heading-input" value="${esc(draft.title)}" aria-label="Meeting title">
        <span class="muted small">${fmtDate(draft.meeting_date)} · draft #${draft.id} · click any text to edit</span>
        <p class="muted">${esc(draft.summary)}</p>
        <div class="speakers">${draft.speakers.map((s) => `<span class="chip">${avatar(s)}${esc(s)}</span>`).join("")}</div>
      </div>
      <div id="conflicts-box"></div>
      ${speakerOptions()}
      <div class="grid-2" style="margin-top:18px">
        <div class="card" id="decisions-box"></div>
        <div class="card" id="actions-box"></div>
      </div>
      <div class="confirm-bar" id="confirm-bar"></div>
      <div id="review-error" class="alert error" hidden></div>
    </div>`;
  renderConflicts();
  renderLists();
  renderConfirmBar();
}

function showReviewError(msg) {
  const box = $("#review-error");
  if (box) {
    box.textContent = msg;
    box.hidden = !msg;
  }
}

$("#review-body").addEventListener("input", (e) => {
  if (!draft) return;
  if (e.target.id === "draft-title") {
    draft.title = e.target.value;
    return scheduleSave();
  }
  const field = e.target.dataset.field;
  const row = e.target.closest("[data-kind]");
  if (!field || !row) return;
  const item = draft.extraction[row.dataset.kind][Number(row.dataset.i)];
  item[field] = e.target.value || (field === "deadline" || field === "owner" ? null : "");
  if (field === "deadline") item.deadline_text = null;  // explicit date wins over the spoken phrase
  if (row.dataset.kind === "action_items") {
    row.querySelector(".flags").innerHTML = actionFlags(item);
    renderConfirmBar();
  }
  scheduleSave();
});

$("#review-body").addEventListener("click", async (e) => {
  const t = e.target;
  try {
    if (t.closest("[data-resolve]")) {
      const b = t.closest("[data-resolve]");
      if (dirty) await saveDraft();
      await api(`/api/conflicts/${b.dataset.conflict}/resolve`, { method: "POST", body: { resolution: b.dataset.resolve } });
      const m = await api(`/api/meetings/${draft.id}`);
      draft.conflicts = m.conflicts;
      draft.open_conflicts = m.open_conflicts;
      renderConflicts();
      renderConfirmBar();
      markConflictedDecisions();
      refreshBadges();
    } else if (t.closest("[data-add]")) {
      const kind = t.closest("[data-add]").dataset.add;
      draft.extraction[kind].push(kind === "decisions"
        ? { text: "", topic: "", speaker: draft.speakers[0] || "Unknown", confidence: 1, evidence: "" }
        : { title: "", owner: null, deadline: null, deadline_text: null, speaker: "Unknown", confidence: 1, evidence: "", needs_owner: true, needs_deadline: true });
      renderLists();
      renderConfirmBar();
      const rows = document.querySelectorAll(`[data-kind="${kind}"]`);
      rows[rows.length - 1]?.querySelector(".title-input")?.focus();
    } else if (t.closest("[data-delete]")) {
      const row = t.closest("[data-kind]");
      draft.extraction[row.dataset.kind].splice(Number(row.dataset.i), 1);
      renderLists();
      renderConfirmBar();
      scheduleSave();
    } else if (t.closest("#discard-btn")) {
      if (!window.confirm("Discard this draft? Nothing from it will be saved.")) return;
      clearTimeout(saveTimer);
      dirty = false;
      await api(`/api/meetings/${draft.id}`, { method: "DELETE" });
      reviewMeetingId = null;
      refreshBadges();
      loadReview();
    } else if (t.closest("#confirm-btn")) {
      const btn = t.closest("#confirm-btn");
      await withButton(btn, "Confirming…", async () => {
        if (dirty || savePromise) await saveDraft();
        return api(`/api/meetings/${draft.id}/confirm`, { method: "POST" });
      });
      reviewMeetingId = null;
      draft = null;
      refreshBadges();
      showTab(TAB_AFTER_CONFIRM);
    }
  } catch (err) {
    showReviewError(err.message);
  }
});

const TAB_AFTER_CONFIRM = "memory";

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
