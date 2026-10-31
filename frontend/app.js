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
const TAB_LOADERS = {
  review: () => loadReview(), tasks: () => loadTasks(), memory: () => loadMemory(), reminders: () => loadReminders(),
  digest: () => (digestPerson ? loadDigest(digestPerson) : loadDigestPeople()),
};

function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  TAB_LOADERS[name]?.();
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

async function refreshBadges() {
  loadStats();
  const badge = (tab, label, n) => {
    document.querySelector(`.tab[data-tab="${tab}"]`).innerHTML = `${label}${n ? `<span class="count">${n}</span>` : ""}`;
  };
  try {
    const [open, unread] = await Promise.all([
      api("/api/conflicts?open_only=true"), api("/api/notifications?unread_only=true"),
    ]);
    badge("review", "Review", open.length);
    badge("reminders", "Reminders", unread.length);
  } catch { /* badges are cosmetic */ }
}

// --- Header stats & first-run hero ----------------------------------------------------------
async function loadStats() {
  try {
    const s = await api("/api/stats");
    const stat = (n, label, cls = "") => `<span class="stat ${cls}"><strong>${n}</strong> ${label}</span>`;
    $("#stats").innerHTML = [
      stat(s.decisions, "decisions"),
      stat(s.open_tasks, "open tasks"),
      s.overdue ? stat(s.overdue, "overdue", "warn") : "",
      stat(s.conflicts_caught, `conflict${s.conflicts_caught === 1 ? "" : "s"} caught`, s.conflicts_caught ? "hot" : ""),
    ].join("");
    $("#hero").hidden = s.meetings > 0;
  } catch { /* cosmetic */ }
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
  schedulePreview();
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
    schedulePreview();
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

$("#demo-reset").addEventListener("click", async () => {
  if (!window.confirm("Delete all meetings, decisions, tasks and reminders?")) return;
  await api("/api/demo/reset", { method: "POST" }).catch((err) => toast(esc(err.message), "error"));
  reviewMeetingId = null;
  digestPerson = null;
  ["#memory-body", "#tasks-body", "#notif-body", "#ask-result"].forEach((sel) => ($(sel).innerHTML = ""));
  refreshBadges();
  toast("Clean slate. Load the demo or start dictating.");
});

$("#demo-sample").addEventListener("click", async () => {
  try {
    const samples = await api("/api/demo/transcripts");
    const s = samples[samples.length - 1];
    $("#m-title").value = s.title;
    $("#m-date").value = s.meeting_date;
    $("#m-transcript").value = s.transcript;
    schedulePreview();
  } catch (err) {
    showNewError(err.message);
  }
});

// --- Live dictation preview ------------------------------------------------------------
let previewTimer = null;
let previewSeq = 0;

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(runPreview, 450);
}

async function runPreview() {
  const transcript = $("#m-transcript").value;
  const seq = ++previewSeq;
  if (!transcript.trim()) return renderLive(null);
  try {
    const p = await api("/api/preview", { method: "POST", body: { transcript, meeting_date: $("#m-date").value } });
    if (seq === previewSeq) renderLive(p);  // ignore stale responses
  } catch { /* preview is best effort */ }
}

function renderLive(p) {
  const panel = $("#live-panel");
  if (!p) {
    panel.classList.remove("active");
    panel.innerHTML = `<div class="live-head"><span class="live-dot"></span> Listening</div>
      <p class="muted small">Start talking. Decisions, tasks, self-corrections and conflicts with past
      meetings appear here as you speak.</p>`;
    return;
  }
  panel.classList.add("active");
  const v = p.voice;
  const stat = (n, label) => `<div class="live-stat"><strong>${n}</strong><span>${label}</span></div>`;
  panel.innerHTML = `
    <div class="live-head"><span class="live-dot on"></span> Hearing ${p.speakers.length} speaker${p.speakers.length === 1 ? "" : "s"}</div>
    <div class="live-stats">
      ${stat(p.decisions.length, "decisions")}
      ${stat(p.action_items.length, "tasks")}
      ${stat(v.corrections.length, "corrections")}
      ${stat(v.filler_words, "fillers cut")}
    </div>
    ${p.conflicts.map((c) => `
      <div class="live-conflict">
        <strong>⚠ Contradicts ${esc(c.old_meeting_title)}</strong>
        <div class="small">${esc(c.explanation)}</div>
      </div>`).join("")}
    ${v.corrections.length ? `<ul class="corrections compact">${v.corrections.slice(-4).map((c) =>
      `<li><del>${esc(c.said)}</del> → <ins>${esc(c.meant)}</ins></li>`).join("")}</ul>` : ""}
    ${p.action_items.length ? `<div class="live-list">${p.action_items.slice(-5).map((a) => `
      <div class="live-item">☐ ${esc(a.title)} <span class="muted">${a.owner ? "· " + esc(a.owner) : ""}${a.deadline ? " · " + fmtDate(a.deadline) : ""}</span></div>`).join("")}</div>` : ""}
    <p class="muted tiny">Instant local read. <strong>Extract</strong> runs the full AI pass.</p>`;
}

$("#m-transcript").addEventListener("input", schedulePreview);
$("#m-date").addEventListener("change", schedulePreview);

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
      ${c.affected_tasks?.length ? `
        <div class="impact">
          <strong>Impact:</strong> ${c.affected_tasks.length} open task${c.affected_tasks.length === 1 ? "" : "s"} built on the earlier decision
          ${c.affected_tasks.map((t) => `<div class="impact-task">↳ ${esc(t.title)}
            <span class="muted small">${t.owner ? esc(t.owner) : "unassigned"}${t.tracker_ref ? ` · ${esc(t.tracker_ref)}` : ""}</span></div>`).join("")}
          <span class="muted small">${r === "supersede" ? "You can close these as obsolete when you confirm." : "Superseding the old decision would make these obsolete."}</span>
        </div>` : ""}
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
        ${renderVoicePanel(draft.voice, draft.transcript)}
      </div>
      <form class="command-bar" id="cmd-form" autocomplete="off">
        <span class="cmd-icon" aria-hidden="true">🎙</span>
        <input id="cmd-input" aria-label="Tell Followthrough what to change"
          placeholder="Tell Followthrough what to change… “make Sam own the launch announcement and push it to Friday”">
        <button class="btn primary small" id="cmd-apply">Apply</button>
      </form>
      <div id="cmd-result"></div>
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
      toast({ supersede: "↻ New decision will replace the old one", keep: "Earlier decision stays", dismiss: "Marked as not a conflict" }[b.dataset.resolve]);
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
      if (dirty || savePromise) await withButton(btn, "Saving…", saveDraft);
      const closeIds = await askToConfirm();
      if (!closeIds) return;
      lastConfirm = await withButton(btn, "Posting…", () =>
        api(`/api/meetings/${draft.id}/confirm`, { method: "POST", body: { close_task_ids: closeIds } }));
      reviewMeetingId = null;
      draft = null;
      refreshBadges();
      showTab(TAB_AFTER_CONFIRM);
    }
  } catch (err) {
    showReviewError(err.message);
  }
});

const TAB_AFTER_CONFIRM = "tasks";

// --- Voice cleanup panel & final confirmation dialog -----------------------------------
function renderVoicePanel(v, transcript) {
  if (!v) return "";
  const stats = [
    `${v.utterances} utterance${v.utterances === 1 ? "" : "s"}`,
    `${v.speakers.length} speaker${v.speakers.length === 1 ? "" : "s"}`,
    `${v.filler_words} filler word${v.filler_words === 1 ? "" : "s"} dropped`,
    `${v.corrections.length} self-correction${v.corrections.length === 1 ? "" : "s"} resolved`,
  ];
  if (v.unattributed) stats.push(`${v.unattributed} unattributed (Unknown)`);
  return `
    <div class="voice-panel">
      <div class="voice-stats"><span class="badge live">🎙 Speech cleanup</span> <span class="muted small">${stats.join(" · ")}</span></div>
      ${v.corrections.length ? `<ul class="corrections">${v.corrections.map((c) => `
        <li>${avatar(c.speaker)} <del>${esc(c.said)}</del> <span class="muted">→</span> <ins>${esc(c.meant)}</ins></li>`).join("")}</ul>` : ""}
      <details><summary class="small muted">Show original transcript</summary><pre class="transcript">${esc(transcript)}</pre></details>
    </div>`;
}

async function askToConfirm() {
  const info = await api("/api/tracker").catch(() => ({ mode: "mock" }));
  const ex = draft.extraction;
  const items = ex.action_items.filter((a) => a.title.trim());
  const decisions = ex.decisions.filter((d) => d.text.trim());
  const where = info.mode === "github" ? `GitHub Issues in <strong>${esc(info.repo)}</strong>` : "the <strong>local mock tracker</strong>";
  const effects = draft.conflicts.map((c) => ({
    supersede: `“${esc(c.old_text)}” will be marked superseded by “${esc(c.new_decision)}”`,
    keep: `“${esc(c.old_text)}” stays; “${esc(c.new_decision)}” is set aside`,
    dismiss: `“${esc(c.old_text)}” and “${esc(c.new_decision)}” both stay active`,
  }[c.resolution]));
  const obsolete = [...new Map(draft.conflicts.filter((c) => c.resolution === "supersede")
    .flatMap((c) => c.affected_tasks || []).map((t) => [t.id, t])).values()];
  $("#confirm-summary").innerHTML = `
    <p>This will create <strong>${items.length} task${items.length === 1 ? "" : "s"}</strong> in ${where}
       and save <strong>${decisions.length} decision${decisions.length === 1 ? "" : "s"}</strong> to team memory.</p>
    ${items.length ? `<ul class="confirm-list">${items.map((a) => `
      <li><span>${esc(a.title)}</span>
        <span class="muted small">${a.owner ? esc(a.owner) : "<span class='warn-text'>no owner</span>"} ·
          ${a.deadline ? fmtDate(a.deadline) : "<span class='warn-text'>no deadline</span>"}</span></li>`).join("")}</ul>` : ""}
    ${effects.length ? `<p class="small"><strong>Memory changes</strong></p><ul class="bullets small">${effects.map((e) => `<li>${e}</li>`).join("")}</ul>` : ""}
    ${obsolete.length ? `<p class="small"><strong>Close tasks made obsolete</strong></p>
      <div class="obsolete-list">${obsolete.map((t) => `
        <label><input type="checkbox" data-close="${t.id}" checked> ${esc(t.title)}
          <span class="muted small">${t.owner ? esc(t.owner) : "unassigned"}${t.tracker_ref ? ` · ${esc(t.tracker_ref)}` : ""} · ${esc(t.reason)}</span></label>`).join("")}</div>` : ""}
    <p class="muted small">Nothing has been posted yet. You can go back and edit.</p>`;
  $("#confirm-ok").textContent = items.length ? `Post ${items.length} task${items.length === 1 ? "" : "s"} & save` : "Save to memory";
  const dialog = $("#confirm-dialog");
  dialog.showModal();
  // Resolves to the task ids to close (possibly empty) when confirmed, or null when cancelled.
  // Explicit handlers rather than the dialog's close event, which some embedded browsers delay.
  return new Promise((resolve) => {
    const finish = (ok) => {
      const ids = [...dialog.querySelectorAll("[data-close]:checked")].map((b) => Number(b.dataset.close));
      dialog.close();
      resolve(ok ? ids : null);
    };
    $("#confirm-ok").onclick = () => finish(true);
    $("#confirm-cancel").onclick = () => finish(false);
    dialog.oncancel = (e) => { e.preventDefault(); finish(false); };  // Esc
  });
}

// Ctrl/Cmd+Enter extracts from the dictation box.
$("#m-transcript").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#m-extract").click();
});

// --- Toasts -------------------------------------------------------------------------------
function toast(html, kind = "ok") {
  let box = $("#toasts");
  if (!box) {
    box = document.createElement("div");
    box.id = "toasts";
    box.setAttribute("aria-live", "polite");
    document.body.appendChild(box);
  }
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = html;
  box.appendChild(el);
  setTimeout(() => el.classList.add("out"), 3800);
  setTimeout(() => el.remove(), 4300);
}

// --- Spoken edits on the Review screen --------------------------------------------------------
$("#review-body").addEventListener("submit", async (e) => {
  if (e.target.id !== "cmd-form") return;
  e.preventDefault();
  const input = $("#cmd-input");
  const command = input.value.trim();
  if (!command || !draft) return;
  const result = $("#cmd-result");
  try {
    if (dirty || savePromise) await saveDraft();
    const res = await withButton($("#cmd-apply"), "Applying…", () =>
      api(`/api/meetings/${draft.id}/command`, { method: "POST", body: { command } }));
    if (!res.changes.length) {
      result.innerHTML = `<div class="alert">Didn't catch an edit in “${esc(command)}”. Try “assign the auth flow to Raj” or “push the launch announcement to Friday”.</div>`;
      return;
    }
    Object.assign(draft, res);
    input.value = "";
    renderConflicts();
    renderLists();
    renderConfirmBar();
    result.innerHTML = `<div class="alert ok cmd-applied">${res.changes.map((c) => `<div>✓ ${esc(c)}</div>`).join("")}</div>`;
    toast(`✓ ${res.changes.length} change${res.changes.length === 1 ? "" : "s"} applied`);
    refreshBadges();
  } catch (err) {
    result.innerHTML = `<div class="alert error">${esc(err.message)}</div>`;
  }
});

// --- Tasks --------------------------------------------------------------------------
let lastConfirm = null;   // result of the most recent confirm, shown once as a banner

function dueState(t) {
  if (t.status !== "open") return "done";
  if (!t.deadline) return "none";
  const today = new Date(new Date().toDateString());
  const due = new Date(t.deadline + "T00:00:00");
  const days = Math.round((due - today) / 86400000);
  if (days < 0) return "overdue";
  if (days <= 1) return "soon";
  return "upcoming";
}

const DUE_GROUPS = [
  ["overdue", "Overdue"], ["soon", "Due within a day"], ["upcoming", "Upcoming"],
  ["none", "No deadline"], ["done", "Done"],
];

function trackerLink(t) {
  if (t.tracker_error) {
    return `<span class="badge flag" title="${esc(t.tracker_error)}">not posted</span>
            <button class="btn small ghost" data-retry="${t.id}">Retry</button>`;
  }
  if (!t.tracker_url) return "";
  if (t.tracker === "github") {
    return `<a class="badge topic" href="${esc(t.tracker_url)}" target="_blank" rel="noopener">GitHub ${esc(t.tracker_ref)} ↗</a>`;
  }
  return `<span class="badge topic" title="Local mock tracker ticket">${esc(t.tracker_ref)}</span>`;
}

function renderTask(t) {
  const state = dueState(t);
  return `
    <div class="item task ${state}">
      <label class="check"><input type="checkbox" data-done="${t.id}" ${t.status !== "open" ? "checked" : ""}
        ${t.status === "cancelled" ? "disabled" : ""} aria-label="Mark done"></label>
      <div class="task-main">
        <div class="title">${esc(t.title)}</div>
        ${t.status === "cancelled" ? `<div class="small closed-note">✕ Closed as obsolete. ${esc(t.closed_reason || "")}</div>` : ""}
        <div class="meta">
          ${t.owner ? `${avatar(t.owner)} ${esc(t.owner)}` : `<span class="badge flag">needs owner</span>`}
          ${t.deadline ? `<span class="badge due-${state}">📅 ${fmtDate(t.deadline)}</span>` : `<span class="badge flag">needs deadline</span>`}
          <span class="muted small">from ${esc(t.meeting_title)}</span>
          ${t.possibly_obsolete ? `<span class="badge kind-supersession" title="${esc(t.possibly_obsolete)}">⚠ may be obsolete</span>` : ""}
          ${trackerLink(t)}
        </div>
      </div>
    </div>`;
}

async function loadTasks() {
  const body = $("#tasks-body");
  if (!body.children.length) loading(body);
  try {
    const [info, all] = await Promise.all([api("/api/tracker"), api("/api/tasks")]);
    $("#tracker-badge").className = `badge ${info.mode === "github" ? "live" : "mock"}`;
    $("#tracker-badge").textContent = info.mode === "github" ? `Tracker: GitHub · ${info.repo}` : "Tracker: local mock (no GITHUB_TOKEN)";

    const ownerSel = $("#tasks-owner");
    const owners = [...new Set(all.map((t) => t.owner).filter(Boolean))].sort();
    const current = ownerSel.value;
    ownerSel.innerHTML = `<option value="">Everyone</option>` + owners.map((o) => `<option ${o === current ? "selected" : ""}>${esc(o)}</option>`).join("");

    if (lastConfirm) {
      const n = lastConfirm.tasks.length;
      const posted = lastConfirm.tasks.filter((t) => t.tracker_url).length;
      const where = lastConfirm.tracker.mode === "github" ? `GitHub (${lastConfirm.tracker.repo})` : "the local mock tracker";
      $("#tasks-banner").innerHTML = `<div class="alert ${posted === n ? "ok" : "error"}">✓ Confirmed. ${posted} of ${n} task${n === 1 ? "" : "s"} posted to ${esc(where)}.${posted < n ? " Use Retry on the ones that failed." : ""}${lastConfirm.closed?.length ? ` Closed ${lastConfirm.closed.length} obsolete task${lastConfirm.closed.length === 1 ? "" : "s"}.` : ""}</div>`;
      lastConfirm = null;
    } else {
      $("#tasks-banner").innerHTML = "";
    }

    const status = $("#tasks-status").value;
    const tasks = all.filter((t) => (!ownerSel.value || t.owner === ownerSel.value) && (!status || (status === "open" ? t.status === "open" : t.status !== "open")));
    if (!tasks.length) {
      body.innerHTML = `<div class="empty">${all.length ? "No tasks match these filters." : "No tasks yet. Confirm a meeting to create them."}</div>`;
      return;
    }
    body.innerHTML = DUE_GROUPS.map(([key, label]) => {
      const group = tasks.filter((t) => dueState(t) === key);
      return group.length ? `<h3 class="section-title">${label} <span class="muted small">${group.length}</span></h3>${group.map(renderTask).join("")}` : "";
    }).join("");
  } catch (err) {
    body.innerHTML = `<div class="alert error">${esc(err.message)}</div>`;
  }
}
$("#tasks-owner").addEventListener("change", loadTasks);
$("#tasks-status").addEventListener("change", loadTasks);

$("#tasks-body").addEventListener("click", async (e) => {
  const retry = e.target.closest("[data-retry]");
  if (!retry) return;
  try {
    await withButton(retry, "Posting…", () => api(`/api/tasks/${retry.dataset.retry}/retry`, { method: "POST" }));
  } catch (err) {
    toast(esc(err.message), "error");
  }
  loadTasks();
});

$("#tasks-body").addEventListener("change", async (e) => {
  const box = e.target.closest("[data-done]");
  if (!box) return;
  try {
    await api(`/api/tasks/${box.dataset.done}`, { method: "PATCH", body: { status: box.checked ? "done" : "open" } });
  } catch (err) {
    toast(esc(err.message), "error");
  }
  loadTasks();
});

// --- Reminders ----------------------------------------------------------------------
function fmtTime(iso) {
  if (!iso) return "never";
  const d = new Date(iso.replace(" ", "T"));
  return d.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
}

async function loadReminderStatus() {
  try {
    const s = await api("/api/reminders/status");
    $("#reminder-status").textContent = s.scheduler_running
      ? `Background check every ${s.interval_minutes} min · last run ${fmtTime(s.last_run)} · next ${fmtTime(s.next_run)}`
      : `Background scheduler off · last run ${fmtTime(s.last_run)}`;
  } catch {
    $("#reminder-status").textContent = "Scheduler status unavailable";
  }
}

function renderNotification(n) {
  const overdue = n.kind === "overdue";
  return `
    <div class="item notif ${n.read ? "read" : ""} ${overdue ? "overdue" : "soon"}">
      <div class="notif-icon">${overdue ? "🔴" : "⏰"}</div>
      <div class="task-main">
        <div class="title">${esc(n.message)}</div>
        <div class="meta">
          ${avatar(n.person)} ${esc(n.person)}
          <span class="badge ${overdue ? "due-overdue" : "due-soon"}">${overdue ? "overdue" : "due soon"}</span>
          ${n.tracker_ref ? `<span class="badge topic">${esc(n.tracker_ref)}</span>` : ""}
          <span class="muted small">${fmtTime(n.created_at)}</span>
        </div>
      </div>
      ${n.read ? "" : `<button class="btn small ghost" data-read="${n.id}">Mark read</button>`}
    </div>`;
}

async function loadReminders() {
  loadReminderStatus();
  const body = $("#notif-body");
  if (!body.children.length) loading(body);
  try {
    const all = await api("/api/notifications");
    const sel = $("#notif-person");
    const people = [...new Set(all.map((n) => n.person))].sort();
    const current = sel.value;
    sel.innerHTML = `<option value="">Everyone</option>` + people.map((p) => `<option ${p === current ? "selected" : ""}>${esc(p)}</option>`).join("");
    const list = all.filter((n) => !sel.value || n.person === sel.value);
    body.innerHTML = list.length
      ? list.map(renderNotification).join("")
      : `<div class="empty">No reminders yet. Items due within 24 hours or overdue show up here. Try “Run reminders now”.</div>`;
  } catch (err) {
    body.innerHTML = `<div class="alert error">${esc(err.message)}</div>`;
  }
}

$("#notif-person").addEventListener("change", loadReminders);

$("#run-reminders").addEventListener("click", async (e) => {
  try {
    const res = await withButton(e.currentTarget, "Checking…", () => api("/api/reminders/run", { method: "POST" }));
    const n = res.created.length;
    $("#reminder-banner").innerHTML = `<div class="alert ${n ? "ok" : ""}">${n
      ? `✓ Created ${n} reminder${n === 1 ? "" : "s"}.`
      : "No new reminders: everything due soon or overdue has already been flagged today."}</div>`;
  } catch (err) {
    $("#reminder-banner").innerHTML = `<div class="alert error">${esc(err.message)}</div>`;
  }
  loadReminders();
  refreshBadges();
});

$("#notif-read-all").addEventListener("click", async () => {
  await api("/api/notifications/read-all", { method: "POST" }).catch(() => {});
  loadReminders();
  refreshBadges();
});

$("#notif-body").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-read]");
  if (!b) return;
  await api(`/api/notifications/${b.dataset.read}/read`, { method: "POST" }).catch(() => {});
  loadReminders();
  refreshBadges();
});

// The scheduler creates reminders in the background; keep the tab badge current.
setInterval(refreshBadges, 60000);

// --- Digest -------------------------------------------------------------------------
let digestPerson = null;

async function loadDigestPeople() {
  const box = $("#digest-people");
  try {
    const people = await api("/api/digest/people");
    if (!people.length) {
      box.innerHTML = `<span class="muted small">No people yet. Confirm a meeting first.</span>`;
      return;
    }
    box.innerHTML = people.map((p) =>
      `<button class="chip person ${p === digestPerson ? "active" : ""}" data-person="${esc(p)}">${avatar(p)}${esc(p)}</button>`).join("");
  } catch (err) {
    box.innerHTML = `<span class="error-text small">${esc(err.message)}</span>`;
  }
}

function digestTaskList(items, empty) {
  if (!items.length) return `<p class="muted small">${empty}</p>`;
  return items.map((t) => `
    <div class="digest-task">
      <span>${esc(t.title)}</span>
      <span class="meta">
        ${t.deadline ? `<span class="badge due-${dueState({ ...t, status: "open" })}">${fmtDate(t.deadline)}</span>` : ""}
        ${t.tracker_ref ? `<span class="badge topic">${esc(t.tracker_ref)}</span>` : ""}
      </span>
    </div>`).join("");
}

function renderDigest(d) {
  const n = d.narrative;
  const later = d.open_items.filter((t) => !d.due_this_week.some((x) => x.id === t.id) && !d.overdue.some((x) => x.id === t.id));
  $("#digest-body").innerHTML = `
    <div class="card digest">
      <div class="digest-head">
        ${avatar(d.person)}
        <div class="grow">
          <h2>${esc(n.subject)}</h2>
          <span class="muted small">${fmtDate(d.week.start)} – ${fmtDate(d.week.end)} · written by ${esc(d.source)}</span>
        </div>
        <button class="btn small ghost" id="digest-copy">Copy as Markdown</button>
      </div>
      <p class="digest-summary">${esc(n.summary)}</p>
      <div class="grid-2">
        <div>
          <h3>What changed</h3>
          ${n.highlights.length ? `<ul class="bullets">${n.highlights.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>` : `<p class="muted small">No new decisions affecting you this week.</p>`}
        </div>
        <div>
          <h3>Heads up</h3>
          ${n.heads_up.length ? `<ul class="bullets warn">${n.heads_up.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>` : `<p class="muted small">Nothing needs attention. Nice.</p>`}
        </div>
      </div>
    </div>
    <div class="grid-3">
      <div class="card"><h3>Overdue <span class="muted small">${d.overdue.length}</span></h3>${digestTaskList(d.overdue, "Nothing overdue.")}</div>
      <div class="card"><h3>Due in the next 7 days <span class="muted small">${d.due_this_week.length}</span></h3>${digestTaskList(d.due_this_week, "Nothing due this week.")}</div>
      <div class="card"><h3>Also open <span class="muted small">${later.length}</span></h3>${digestTaskList(later, "No other open items.")}</div>
    </div>
    ${d.unresolved_conflicts.length ? `
      <div class="card">
        <h3>Unresolved conflicts involving you</h3>
        ${d.unresolved_conflicts.map((c) => `<p class="small"><span class="badge kind-${esc(c.kind)}">${esc(c.kind)}</span> ${esc(c.explanation)}</p>`).join("")}
        <button class="btn small primary" data-goto="review">Resolve in Review</button>
      </div>` : ""}`;
}

async function loadDigest(person) {
  digestPerson = person;
  loadDigestPeople();
  const body = $("#digest-body");
  loading(body, `Writing ${person}'s digest…`);
  try {
    renderDigest(await api(`/api/digest/${encodeURIComponent(person)}`));
  } catch (err) {
    body.innerHTML = `<div class="alert error">${esc(err.message)}</div>`;
  } finally {
    loadHealth();
  }
}

$("#digest-people").addEventListener("click", (e) => {
  const chip = e.target.closest("[data-person]");
  if (chip) loadDigest(chip.dataset.person);
});

$("#digest-body").addEventListener("click", async (e) => {
  if (e.target.closest("[data-goto]")) return showTab(e.target.closest("[data-goto]").dataset.goto);
  const copy = e.target.closest("#digest-copy");
  if (!copy) return;
  try {
    const md = await fetch(`${API_BASE}/api/digest/${encodeURIComponent(digestPerson)}/markdown`).then((r) => r.text());
    await navigator.clipboard.writeText(md);
    copy.textContent = "✓ Copied";
  } catch {
    copy.textContent = "Copy failed";
  }
  setTimeout(() => (copy.textContent = "Copy as Markdown"), 1800);
});

// --- Ask your memory ---------------------------------------------------------------------
$("#ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = $("#ask-input").value.trim();
  if (!question) return;
  const box = $("#ask-result");
  box.innerHTML = `<div class="card answer"><span class="muted">Thinking…</span></div>`;
  try {
    const r = await withButton($("#ask-btn"), "Asking…", () => api("/api/ask", { method: "POST", body: { question } }));
    const cites = [
      ...r.decisions.map((d) => `
        <div class="cite ${d.status}"><span class="badge topic">${esc(d.topic)}</span>
          <span class="${d.status === "superseded" ? "struck-text" : ""}">${esc(d.text)}</span>
          <span class="muted small">${esc(d.speaker)} · ${esc(d.meeting_title)} · ${fmtDate(d.meeting_date)}</span></div>`),
      ...r.tasks.map((t) => `
        <div class="cite"><span class="badge">task</span> ${esc(t.title)}
          <span class="muted small">${esc(t.owner || "unassigned")}${t.deadline ? ` · ${fmtDate(t.deadline)}` : ""}${t.tracker_ref ? ` · ${esc(t.tracker_ref)}` : ""}</span></div>`),
    ];
    box.innerHTML = `
      <div class="card answer">
        <div class="answer-q muted small">“${esc(question)}”</div>
        <p class="answer-text">${esc(r.answer)}</p>
        ${cites.length ? `<div class="cites"><div class="muted tiny">SOURCES FROM MEMORY</div>${cites.join("")}</div>` : ""}
        <div class="muted tiny">answered by ${esc(r.source)}</div>
      </div>`;
  } catch (err) {
    box.innerHTML = `<div class="alert error">${esc(err.message)}</div>`;
  } finally {
    loadHealth();
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
