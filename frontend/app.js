// Followthrough frontend. Plain JS, no build step. Talks to the API at API_BASE.
const API_BASE = window.FOLLOWTHROUGH_API || "";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

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

// --- small helpers ------------------------------------------------------------------------
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const icon = (name, cls = "") => `<svg class="i ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

const AVATAR_COLORS = ["#5b6bd5", "#2f8f6f", "#c0703a", "#b5485d", "#3b82a0", "#8a63c9", "#6d8a2f"];
function avatar(name, cls = "") {
  const n = name || "?";
  const color = n === "Unknown" || n === "Unassigned" ? "#8b8a85"
    : AVATAR_COLORS[[...n].reduce((a, c) => a + c.charCodeAt(0), 0) % AVATAR_COLORS.length];
  return `<span class="avatar ${cls}" style="background:${color}">${esc(n[0].toUpperCase())}</span>`;
}
const person = (name) => `<span class="person">${avatar(name)}${esc(name)}</span>`;

function confidence(c) {
  const pct = Math.round((c ?? 0) * 100);
  return `<span class="conf" title="Model confidence"><span class="conf-bar ${pct < 70 ? "low" : ""}"><i style="width:${pct}%"></i></span>${pct}%</span>`;
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso.slice(0, 10) + "T00:00:00");
  return `${d.toLocaleDateString(undefined, { weekday: "short" })} ${d.toLocaleDateString(undefined, { month: "short" })} ${d.getDate()}`;
}

function fmtTime(iso) {
  if (!iso) return "never";
  return new Date(iso.replace(" ", "T")).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
}

function dueState(t) {
  if (t.status && t.status !== "open") return "done";
  if (!t.deadline) return "none";
  const days = Math.round((new Date(t.deadline + "T00:00:00") - new Date(new Date().toDateString())) / 86400000);
  return days < 0 ? "overdue" : days <= 1 ? "soon" : "upcoming";
}
const dueLabel = (t) => t.deadline ? `<span class="date ${dueState(t)}">${fmtDate(t.deadline)}</span>` : "";

function emptyState(iconName, text) {
  return `<div class="empty">${icon(iconName)}<span>${text}</span></div>`;
}

function skeleton(rows = 3) {
  return `<div class="card">${Array.from({ length: rows }, (_, i) =>
    `<div class="skeleton" style="width:${90 - i * 17}%;margin:${i ? 12 : 0}px 0 0"></div>`).join("")}</div>`;
}

async function withButton(btn, busyText, fn) {
  const label = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>${esc(busyText)}`;
  try {
    return await fn();
  } finally {
    btn.disabled = false;
    btn.innerHTML = label;
  }
}

function toast(html, kind = "ok") {
  let box = $("#toasts");
  if (!box) {
    box = Object.assign(document.createElement("div"), { id: "toasts" });
    box.setAttribute("aria-live", "polite");
    document.body.appendChild(box);
  }
  const el = Object.assign(document.createElement("div"), { className: `toast ${kind}` });
  el.innerHTML = `${icon(kind === "error" ? "alert" : "check")}<span>${html}</span>`;
  box.appendChild(el);
  setTimeout(() => el.classList.add("out"), 3600);
  setTimeout(() => el.remove(), 4000);
}

// --- navigation ------------------------------------------------------------------------------
const PAGES = {
  new: ["New meeting", "Dictate or paste a recap. Followthrough pulls out decisions and owned work."],
  review: ["Review", "Check what was extracted, resolve conflicts with past decisions, then confirm."],
  tasks: ["Tasks", "Confirmed action items and where they were posted."],
  memory: ["Decision memory", "Every decision, the meeting that made it, and what replaced it."],
  digest: ["Weekly digest", "One page per person: what changed, what's due, what needs attention."],
  reminders: ["Reminders", "Open items that are overdue or due within a day."],
};
const TAB_LOADERS = {
  review: () => loadReview(), tasks: () => loadTasks(), memory: () => loadMemory(),
  reminders: () => loadReminders(), digest: () => (digestPerson ? loadDigest(digestPerson) : loadDigestPeople()),
};
let currentTab = "new";

function showTab(name) {
  currentTab = name;
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  const [title, sub] = PAGES[name];
  $("#page-title").textContent = title;
  $("#page-sub").textContent = sub;
  TAB_LOADERS[name]?.();
}
$$(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

function setCount(tab, n) {
  const el = $(`.tab[data-tab="${tab}"] .count`);
  if (!el) return;
  el.textContent = n;
  el.hidden = !n;
}

async function refreshBadges() {
  loadStats();
  try {
    const [open, unread] = await Promise.all([
      api("/api/conflicts?open_only=true"), api("/api/notifications?unread_only=true"),
    ]);
    setCount("review", open.length);
    setCount("reminders", unread.length);
  } catch { /* counts are cosmetic */ }
}

async function loadStats() {
  try {
    const s = await api("/api/stats");
    const row = (n, label, cls = "") => `<dt class="${cls}">${n}</dt><dd>${label}</dd>`;
    $("#stats").innerHTML = [
      row(s.decisions, "active decisions"),
      row(s.open_tasks, "open tasks"),
      row(s.overdue, "overdue", s.overdue ? "warn" : ""),
      row(s.conflicts_caught, s.conflicts_caught === 1 ? "conflict caught" : "conflicts caught", s.conflicts_caught ? "hot" : ""),
    ].join("");
    $("#hero").hidden = s.meetings > 0;
  } catch { /* cosmetic */ }
}

async function loadHealth() {
  const badge = $("#llm-badge");
  try {
    const { llm } = await api("/api/health");
    badge.className = `llm ${llm.live ? "live" : "mock"}`;
    badge.textContent = llm.live ? llm.model : "offline rules (no key)";
    badge.title = llm.live
      ? `${llm.provider} · fallbacks: ${llm.fallback_models.join(", ") || "none"}${llm.last_error ? `\nLast error: ${llm.last_error}` : ""}`
      : "No LLM key set: a deterministic rules engine answers instead.";
  } catch {
    badge.className = "llm mock";
    badge.textContent = "API offline";
  }
}

// --- New meeting -------------------------------------------------------------------------------
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
  err.innerHTML = msg ? `${icon("alert")}<span>${esc(msg)}</span>` : "";
  err.hidden = !msg;
}

$("#m-extract").addEventListener("click", async (e) => {
  showNewError("");
  const transcript = $("#m-transcript").value.trim();
  if (!transcript) {
    $("#m-transcript").focus();
    return showNewError("Dictate or paste a transcript first.");
  }
  try {
    const data = await withButton(e.currentTarget, "Extracting…", () =>
      api("/api/extract", {
        method: "POST",
        body: { transcript, title: $("#m-title").value.trim() || "Untitled meeting", meeting_date: $("#m-date").value },
      }));
    reviewMeetingId = data.id;
    $("#m-transcript").value = "";
    $("#m-title").value = "";
    schedulePreview();
    showTab("review");
  } catch (err) {
    showNewError(err.message);
  } finally {
    loadHealth();
    refreshBadges();
  }
});

$("#m-transcript").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#m-extract").click();
});

async function loadDemo(btn) {
  showNewError("");
  try {
    const res = await withButton(btn || $("#demo-load"), "Loading…", () => api("/api/demo/load", { method: "POST" }));
    reviewMeetingId = res.meetings.find((m) => m.status === "staged")?.id ?? null;
    refreshBadges();
    showTab("review");
  } catch (err) {
    showNewError(err.message);
  }
}
$("#demo-load").addEventListener("click", (e) => loadDemo(e.currentTarget));

async function resetAll() {
  if (!window.confirm("Delete all meetings, decisions, tasks and reminders?")) return;
  await api("/api/demo/reset", { method: "POST" }).catch((err) => toast(esc(err.message), "error"));
  reviewMeetingId = null;
  digestPerson = null;
  selectedDecision = null;
  ["#memory-body", "#tasks-body", "#notif-body", "#ask-result", "#digest-body"].forEach((sel) => ($(sel).innerHTML = ""));
  refreshBadges();
  showTab("new");
  toast("Clean slate. Load the demo or start dictating.");
}
$("#demo-reset").addEventListener("click", resetAll);

async function insertSample() {
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
}
$("#demo-sample").addEventListener("click", insertSample);

// --- Live dictation preview ------------------------------------------------------------------
let previewTimer = null;
let previewSeq = 0;
let lastLive = {};

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(runPreview, 400);
}

async function runPreview() {
  const transcript = $("#m-transcript").value;
  const seq = ++previewSeq;
  if (!transcript.trim()) return renderLive(null);
  try {
    const p = await api("/api/preview", { method: "POST", body: { transcript, meeting_date: $("#m-date").value } });
    if (seq === previewSeq) renderLive(p);  // ignore responses that arrive out of order
  } catch { /* preview is best effort */ }
}

function renderLive(p) {
  const panel = $("#live-panel");
  if (!p) {
    lastLive = {};
    panel.innerHTML = `
      <div class="live-head"><span class="live-dot"></span>Live read</div>
      <p class="live-empty">Start talking. Decisions, tasks, self-corrections and contradictions with past meetings
        show up here while you speak.</p>`;
    return;
  }
  const v = p.voice;
  const counts = { decisions: p.decisions.length, tasks: p.action_items.length, corrections: v.corrections.length, fillers: v.filler_words };
  const stat = (key, label) => `<div class="live-stat ${lastLive[key] !== undefined && lastLive[key] !== counts[key] ? "bump" : ""}">
    <strong>${counts[key]}</strong><span>${label}</span></div>`;
  panel.innerHTML = `
    <div class="live-head"><span class="live-dot on"></span>Hearing ${plural(p.speakers.length, "speaker")}</div>
    <div class="live-stats">${stat("decisions", "decisions")}${stat("tasks", "tasks")}${stat("corrections", "fixes")}${stat("fillers", "filler")}</div>
    ${p.conflicts.map((c) => `
      <div class="live-conflict">${icon("alert")}<div><strong>Contradicts ${esc(c.old_meeting_title)}</strong>${esc(c.explanation)}</div></div>`).join("")}
    ${v.corrections.length ? `<ul class="corrections">${v.corrections.slice(-3).map((c) =>
      `<li><del>${esc(c.said)}</del>${icon("arrow")}<ins>${esc(c.meant)}</ins></li>`).join("")}</ul>` : ""}
    ${p.action_items.length ? `<div class="live-list">${p.action_items.slice(-5).map((a) => `
      <div class="live-item">${icon("tasks")}<span>${esc(a.title)} <span class="faint">${a.owner ? "· " + esc(a.owner) : ""}${a.deadline ? " · " + fmtDate(a.deadline) : ""}</span></span></div>`).join("")}</div>` : ""}
    <p class="tiny faint">Instant local read. Extract runs the full model pass.</p>`;
  lastLive = counts;
}

$("#m-transcript").addEventListener("input", schedulePreview);
$("#m-date").addEventListener("change", schedulePreview);

// --- Review: editable draft, transcript links, conflicts, confirmation gate ----------------------
let reviewMeetingId = null;
let draft = null;
let saveTimer = null;
let savePromise = null;
let dirty = false;
let lastConfirm = null;

async function loadReview() {
  const body = $("#review-body");
  if (dirty) await saveDraft().catch(() => {});
  try {
    const staged = (await api("/api/meetings")).filter((m) => m.status === "staged");
    const picker = $("#review-picker");
    if (!staged.length) {
      picker.hidden = true;
      draft = null;
      body.innerHTML = emptyState("inbox", "Nothing waiting for review. Extract a meeting or load the demo.");
      return;
    }
    if (!staged.some((m) => m.id === reviewMeetingId)) reviewMeetingId = staged[0].id;
    picker.hidden = staged.length < 2;
    $("#review-select").innerHTML = staged.map((m) =>
      `<button class="seg ${m.id === reviewMeetingId ? "active" : ""}" data-meeting-id="${m.id}">${esc(m.title)}
        <span class="date">${fmtDate(m.meeting_date)}</span></button>`).join("");
    if (!body.querySelector(`[data-meeting="${reviewMeetingId}"]`)) body.innerHTML = skeleton(4);
    draft = await api(`/api/meetings/${reviewMeetingId}`);
    renderReview();
  } catch (err) {
    body.innerHTML = `<div class="alert error">${icon("alert")}${esc(err.message)}</div>`;
  }
}
$("#review-select").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-meeting-id]");
  if (!b) return;
  if (dirty) await saveDraft().catch(() => {});
  reviewMeetingId = Number(b.dataset.meetingId);
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
  supersede: "The new decision replaces the earlier one",
  keep: "The earlier decision stands; the new one is set aside",
  dismiss: "Not a real conflict; both stay active",
};

function renderConflict(c, i) {
  const r = c.resolution;
  const btn = (value, label, cls, key) =>
    `<button class="btn small ${cls} ${r === value ? "chosen" : ""}" data-resolve="${value}" data-conflict="${c.id}"
      title="Shortcut: ${key}">${label}</button>`;
  return `
    <div class="conflict ${r ? "resolved" : ""}" data-conflict-card="${i}">
      <div class="conflict-head">
        <span class="kind ${esc(c.kind)}">${icon(c.kind === "contradiction" ? "alert" : "history")}${c.kind === "contradiction" ? "Contradiction" : "Supersession"}</span>
        <span class="why">${esc(c.explanation)}</span>
      </div>
      <div class="versus">
        <div class="side old ${r === "supersede" ? "struck" : ""}">
          <div class="label">Earlier · ${esc(c.old_meeting_title)} · ${fmtDate(c.old_meeting_date)}</div>
          <div class="text">${esc(c.old_text)}</div>
          <div class="meta">${person(c.old_speaker)}<span class="tag topic">${esc(c.old_topic)}</span></div>
        </div>
        <div class="arrow">${icon("arrow")}</div>
        <div class="side new ${r === "keep" ? "struck" : ""}">
          <div class="label">This meeting · ${fmtDate(c.meeting_date)}</div>
          <div class="text">${esc(c.new_decision)}</div>
          <div class="meta">${person(c.new_speaker || "Unknown")}</div>
        </div>
      </div>
      ${c.affected_tasks?.length ? `
        <div class="impact">
          <span><strong>Impact</strong> · ${plural(c.affected_tasks.length, "open task")} built on the earlier decision</span>
          ${c.affected_tasks.map((t) => `<div class="impact-task">${icon("tasks")}${esc(t.title)}
            ${t.owner ? person(t.owner) : `<span class="tag warn">unassigned</span>`}${t.tracker_ref ? `<span class="tag ref">${esc(t.tracker_ref)}</span>` : ""}</div>`).join("")}
          <span class="faint small">${r === "supersede" ? "You can close these as obsolete when you confirm." : "Superseding the earlier decision would make these obsolete."}</span>
        </div>` : ""}
      <div class="conflict-actions">
        ${btn("supersede", "Supersede old", "primary", "S")}
        ${btn("keep", "Keep old", "ghost", "K")}
        ${btn("dismiss", "Not a conflict", "ghost", "D")}
        <span class="faint small">${r ? `${icon("check")} ${RESOLUTION_TEXT[r]}. Applied when you confirm.` : `Press <kbd>S</kbd> <kbd>K</kbd> or <kbd>D</kbd> to resolve the next open conflict.`}</span>
      </div>
    </div>`;
}

function renderConflicts() {
  const n = draft.conflicts.length;
  $("#conflicts-box").innerHTML = n
    ? `<h3 class="section-title">${icon("alert")}${plural(n, "conflict")} with earlier decisions</h3>${draft.conflicts.map(renderConflict).join("")}`
    : `<div class="alert ok">${icon("check")}<span>No conflicts with earlier decisions in memory.</span></div>`;
}

function renderDecisionRow(d, i) {
  return `
    <div class="row-item" data-kind="decisions" data-i="${i}">
      <div class="edit-main">
        <textarea class="inline title-input" rows="1" data-field="text" placeholder="What was decided?">${esc(d.text)}</textarea>
        <button class="icon-btn" data-delete title="Remove decision">${icon("trash")}</button>
      </div>
      <div class="row-meta">
        <label class="mini">Topic<input class="inline sm" data-field="topic" value="${esc(d.topic)}" placeholder="topic"></label>
        <label class="mini">By<input class="inline sm" data-field="speaker" list="people-list" value="${esc(d.speaker)}" placeholder="speaker"></label>
        ${d.confidence != null ? confidence(d.confidence) : ""}
        <span class="tag danger conflict-flag" hidden>${icon("alert")}conflict</span>
      </div>
    </div>`;
}

function actionFlags(a) {
  return `${!a.owner?.trim() ? `<span class="tag warn">needs owner</span>` : ""}${!a.deadline ? `<span class="tag warn">needs deadline</span>` : ""}`;
}

function renderActionRow(a, i) {
  return `
    <div class="row-item" data-kind="action_items" data-i="${i}">
      <div class="edit-main">
        <input class="inline title-input" data-field="title" value="${esc(a.title)}" placeholder="What needs to happen?">
        <button class="icon-btn" data-delete title="Remove action item">${icon("trash")}</button>
      </div>
      <div class="row-meta">
        <label class="mini">Owner<input class="inline sm" data-field="owner" list="people-list" value="${esc(a.owner ?? "")}" placeholder="unassigned"></label>
        <label class="mini">Due<input class="inline sm" type="date" data-field="deadline" value="${esc(a.deadline ?? "")}"></label>
        ${a.deadline_text ? `<span class="faint small" title="As spoken">“${esc(a.deadline_text)}”</span>` : ""}
        <span class="flags">${actionFlags(a)}</span>
        ${a.confidence != null ? confidence(a.confidence) : ""}
      </div>
    </div>`;
}

function renderLists() {
  const ex = draft.extraction;
  $("#decisions-box").innerHTML = `
    <div class="list-head"><h3 class="section-title" style="margin:0">Decisions <span class="faint">${ex.decisions.length}</span></h3></div>
    ${ex.decisions.map(renderDecisionRow).join("")}
    <button class="add-row" data-add="decisions">${icon("plus")}Add decision</button>`;
  $("#actions-box").innerHTML = `
    <div class="list-head"><h3 class="section-title" style="margin:0">Action items <span class="faint">${ex.action_items.length}</span></h3></div>
    ${ex.action_items.map(renderActionRow).join("")}
    <button class="add-row" data-add="action_items">${icon("plus")}Add action item</button>`;
  markConflictedDecisions();
}

function markConflictedDecisions() {
  const open = new Set(draft.conflicts.filter((c) => !c.resolution).map((c) => c.new_decision_index));
  $$('#decisions-box [data-kind="decisions"]').forEach((row) => {
    const flagged = open.has(serverIndex(draft.extraction.decisions, Number(row.dataset.i), "text"));
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
      ? `<strong>Resolve ${plural(open, "conflict")}</strong> before confirming. Nothing has been saved yet.`
      : `Nothing is saved to memory or posted to the tracker until you confirm.`}
      ${warnings.length ? `<br><span class="warn-text">${plural(items.length, "task")}: ${warnings.join(", ")}.</span>` : ""}
      <br><span id="save-status" class="faint small">${dirty ? "Unsaved changes…" : "All changes saved"}</span></span>
    <button id="discard-btn" class="btn ghost">Discard draft</button>
    <button id="confirm-btn" class="btn primary" ${open ? "disabled" : ""}>Confirm &amp; create ${plural(items.length, "task")}</button>`;
}

// Transcript linking: each extracted item points at the utterance it most likely came from.
const STOP = new Set("the and for that this with from will have just are was were you your our can get its it's i'll we're going about into then them they".split(" "));
const tokens = (s) => new Set((s || "").toLowerCase().match(/[a-z0-9$]{3,}/g)?.filter((w) => !STOP.has(w)) || []);

function sourceUtterance(item) {
  const want = tokens(`${item.evidence || ""} ${item.text || item.title || ""}`);
  let best = -1, bestScore = 0;
  draft.utterances.forEach((u, i) => {
    const have = tokens(u.text);
    const score = [...want].filter((w) => have.has(w)).length;
    if (score > bestScore) [best, bestScore] = [i, score];
  });
  return bestScore >= 2 ? best : -1;
}

function markWords(text, item) {
  const want = tokens(`${item.text || item.title || ""}`);
  return esc(text).replace(/[A-Za-z0-9$]{3,}/g, (w) => (want.has(w.toLowerCase()) ? `<mark>${w}</mark>` : w));
}

function renderTranscript() {
  $("#transcript").innerHTML = draft.utterances.map((u, i) => `
    <div class="utt" data-utt="${i}">${avatar(u.speaker)}<div><div class="who">${esc(u.speaker)}</div><div class="said">${esc(u.text)}</div></div></div>`).join("")
    || `<p class="faint small" style="padding:8px">No transcript.</p>`;
}

function linkRow(row, on) {
  $$(".utt.hit").forEach((u) => {
    u.classList.remove("hit");
    u.querySelector(".said").innerHTML = esc(draft.utterances[u.dataset.utt].text);
  });
  $$(".row-item.linked").forEach((r) => r.classList.remove("linked"));
  if (!on || !row) return;
  const item = draft.extraction[row.dataset.kind][Number(row.dataset.i)];
  const idx = sourceUtterance(item);
  if (idx < 0) return;
  const u = $(`.utt[data-utt="${idx}"]`);
  u.classList.add("hit");
  u.querySelector(".said").innerHTML = markWords(draft.utterances[idx].text, item);
  row.classList.add("linked");
  u.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function renderVoiceStrip(v) {
  if (!v) return "";
  const metric = (n, label) => `<span class="metric"><strong>${n}</strong> ${label}</span>`;
  return `
    <div class="voice-strip">
      ${icon("mic")}
      ${metric(v.utterances, v.utterances === 1 ? "utterance" : "utterances")}
      ${metric(v.filler_words, "filler words cut")}
      ${metric(v.corrections.length, v.corrections.length === 1 ? "self-correction resolved" : "self-corrections resolved")}
      ${v.unattributed ? metric(v.unattributed, "unattributed") : ""}
      ${v.corrections.length ? `<ul class="corrections" style="flex-direction:row;flex-wrap:wrap;gap:6px 16px">${v.corrections.map((c) =>
        `<li>${avatar(c.speaker)}<del>${esc(c.said)}</del>${icon("arrow")}<ins>${esc(c.meant)}</ins></li>`).join("")}</ul>` : ""}
    </div>`;
}

function renderReview() {
  $("#review-body").innerHTML = `
    <div data-meeting="${draft.id}">
      <div class="card">
        <input id="draft-title" class="heading-input" value="${esc(draft.title)}" aria-label="Meeting title">
        <div class="review-meta"><span class="date">${fmtDate(draft.meeting_date)}</span><span class="faint">·</span><span class="faint">Draft #${draft.id}</span>
          <span class="faint">·</span><span class="faint">Click any text to edit</span></div>
        <p class="summary">${esc(draft.summary)}</p>
        <div class="speakers">${draft.speakers.map(person).join("")}</div>
        ${renderVoiceStrip(draft.voice)}
      </div>
      <form class="command-bar" id="cmd-form" autocomplete="off">
        ${icon("mic")}
        <input id="cmd-input" aria-label="Tell Followthrough what to change"
          placeholder="Say what to change, e.g. “make Sam own the launch announcement and push it to Friday”">
        <button class="btn primary small" id="cmd-apply">Apply</button>
      </form>
      <div id="cmd-result"></div>
      <div id="conflicts-box"></div>
      <datalist id="people-list">${draft.speakers.filter((s) => s !== "Unknown").map((s) => `<option value="${esc(s)}">`).join("")}</datalist>
      <div class="split" style="margin-top:20px">
        <div>
          <div id="decisions-box" style="margin-bottom:20px"></div>
          <div id="actions-box"></div>
        </div>
        <div class="card transcript-card">
          <div class="list-head"><h3 class="section-title" style="margin:0">Transcript</h3><span class="faint small">Hover an item to see its source</span></div>
          <div class="utterances" id="transcript"></div>
        </div>
      </div>
      <div class="confirm-bar" id="confirm-bar"></div>
      <div id="review-error" class="alert error" hidden></div>
    </div>`;
  renderConflicts();
  renderLists();
  renderTranscript();
  renderConfirmBar();
}

function showReviewError(msg) {
  const box = $("#review-error");
  if (box) {
    box.innerHTML = msg ? `${icon("alert")}<span>${esc(msg)}</span>` : "";
    box.hidden = !msg;
  }
}

const reviewBody = $("#review-body");
reviewBody.addEventListener("mouseover", (e) => {
  const row = e.target.closest(".row-item");
  if (row && !row.classList.contains("linked")) linkRow(row, true);
});
reviewBody.addEventListener("focusin", (e) => linkRow(e.target.closest(".row-item"), true));
reviewBody.addEventListener("mouseleave", () => linkRow(null, false));

reviewBody.addEventListener("input", (e) => {
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
  if (field === "deadline") item.deadline_text = null;  // an explicit date wins over the spoken phrase
  if (row.dataset.kind === "action_items") {
    row.querySelector(".flags").innerHTML = actionFlags(item);
    renderConfirmBar();
  }
  scheduleSave();
});

async function resolveConflict(conflictId, resolution) {
  if (dirty) await saveDraft();
  await api(`/api/conflicts/${conflictId}/resolve`, { method: "POST", body: { resolution } });
  toast({ supersede: "The new decision will replace the old one", keep: "The earlier decision stays", dismiss: "Marked as not a conflict" }[resolution]);
  const m = await api(`/api/meetings/${draft.id}`);
  draft.conflicts = m.conflicts;
  draft.open_conflicts = m.open_conflicts;
  renderConflicts();
  renderConfirmBar();
  markConflictedDecisions();
  refreshBadges();
}

reviewBody.addEventListener("click", async (e) => {
  const t = e.target;
  try {
    if (t.closest("[data-resolve]")) {
      const b = t.closest("[data-resolve]");
      await resolveConflict(b.dataset.conflict, b.dataset.resolve);
    } else if (t.closest("[data-add]")) {
      const kind = t.closest("[data-add]").dataset.add;
      draft.extraction[kind].push(kind === "decisions"
        ? { text: "", topic: "", speaker: draft.speakers[0] || "Unknown", confidence: 1, evidence: "" }
        : { title: "", owner: null, deadline: null, deadline_text: null, speaker: "Unknown", confidence: 1, evidence: "", needs_owner: true, needs_deadline: true });
      renderLists();
      renderConfirmBar();
      const rows = $$(`[data-kind="${kind}"]`);
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
      showTab("tasks");
    }
  } catch (err) {
    showReviewError(err.message);
  }
});

reviewBody.addEventListener("submit", async (e) => {
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
      result.innerHTML = `<div class="alert">${icon("message")}<span>Didn't catch an edit in “${esc(command)}”. Try “assign the auth flow to Raj” or “push the launch announcement to Friday”.</span></div>`;
      return;
    }
    Object.assign(draft, res);
    input.value = "";
    renderConflicts();
    renderLists();
    renderTranscript();
    renderConfirmBar();
    result.innerHTML = `<div class="alert ok cmd-applied">${res.changes.map((c) => `<span>${icon("check")} ${esc(c)}</span>`).join("")}</div>`;
    toast(`${plural(res.changes.length, "change")} applied`);
    refreshBadges();
  } catch (err) {
    result.innerHTML = `<div class="alert error">${icon("alert")}<span>${esc(err.message)}</span></div>`;
  }
});

async function askToConfirm() {
  const info = await api("/api/tracker").catch(() => ({ mode: "mock" }));
  const ex = draft.extraction;
  const items = ex.action_items.filter((a) => a.title.trim());
  const decisions = ex.decisions.filter((d) => d.text.trim());
  const where = info.mode === "github" ? `GitHub Issues in <strong class="mono">${esc(info.repo)}</strong>` : "the <strong>local mock tracker</strong>";
  const effects = draft.conflicts.map((c) => ({
    supersede: `<span class="struck-text">${esc(c.old_text)}</span> is replaced by “${esc(c.new_decision)}”`,
    keep: `“${esc(c.old_text)}” stays; <span class="struck-text">${esc(c.new_decision)}</span> is set aside`,
    dismiss: `“${esc(c.old_text)}” and “${esc(c.new_decision)}” both stay active`,
  }[c.resolution]));
  const obsolete = [...new Map(draft.conflicts.filter((c) => c.resolution === "supersede")
    .flatMap((c) => c.affected_tasks || []).map((t) => [t.id, t])).values()];
  $("#confirm-summary").innerHTML = `
    <p>This creates <strong>${plural(items.length, "task")}</strong> in ${where} and saves
       <strong>${plural(decisions.length, "decision")}</strong> to team memory.</p>
    ${items.length ? `<ul class="confirm-list">${items.map((a) => `
      <li><span>${esc(a.title)}</span>
        <span class="small">${a.owner ? esc(a.owner) : "<span class='warn-text'>no owner</span>"} ·
          ${a.deadline ? `<span class="date">${fmtDate(a.deadline)}</span>` : "<span class='warn-text'>no deadline</span>"}</span></li>`).join("")}</ul>` : ""}
    ${effects.length ? `<div class="confirm-section">Memory changes</div><ul class="bullets small">${effects.map((e) => `<li>${e}</li>`).join("")}</ul>` : ""}
    ${obsolete.length ? `<div class="confirm-section">Close tasks made obsolete</div>
      <div class="obsolete-list">${obsolete.map((t) => `
        <label><input type="checkbox" data-close="${t.id}" checked> ${esc(t.title)}
          <span class="faint small">${t.owner ? esc(t.owner) : "unassigned"}${t.tracker_ref ? ` · ${esc(t.tracker_ref)}` : ""} · ${esc(t.reason)}</span></label>`).join("")}</div>` : ""}
    <p class="faint small">Nothing has been posted yet.</p>`;
  $("#confirm-ok").textContent = items.length ? `Post ${plural(items.length, "task")} & save` : "Save to memory";
  const dialog = $("#confirm-dialog");
  dialog.showModal();
  $("#confirm-ok").focus();
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
    dialog.oncancel = (e) => { e.preventDefault(); finish(false); };
  });
}

// --- Tasks ----------------------------------------------------------------------------------------
let taskView = "list";
const DUE_GROUPS = [["overdue", "Overdue"], ["soon", "Due within a day"], ["upcoming", "Upcoming"], ["none", "No deadline"], ["done", "Closed"]];

function trackerRef(t) {
  if (t.tracker_error) {
    return `<span class="tag danger" title="${esc(t.tracker_error)}">not posted</span>
      <button class="btn small ghost" data-retry="${t.id}">Retry</button>`;
  }
  if (!t.tracker_url) return "";
  return t.tracker === "github"
    ? `<a class="tag ref" href="${esc(t.tracker_url)}" target="_blank" rel="noopener">${esc(t.tracker_ref)} ${icon("external")}</a>`
    : `<span class="tag ref" title="Local mock tracker ticket">${esc(t.tracker_ref)}</span>`;
}

function taskTags(t) {
  return `${t.possibly_obsolete ? `<span class="tag warn" title="${esc(t.possibly_obsolete)}">${icon("alert")}may be obsolete</span>` : ""}${trackerRef(t)}`;
}

function renderTaskRow(t) {
  const state = dueState(t);
  return `
    <div class="task ${state}">
      <label class="check"><input type="checkbox" data-done="${t.id}" ${t.status !== "open" ? "checked" : ""}
        ${t.status === "cancelled" ? "disabled" : ""} aria-label="Mark done"></label>
      <div class="title"><span>${esc(t.title)}</span>
        <small>${t.status === "cancelled" ? `<span class="closed-note">Closed as obsolete. ${esc(t.closed_reason || "")}</span>` : `from ${esc(t.meeting_title)}`}</small></div>
      <div class="cols">
        ${taskTags(t)}
        ${t.owner ? person(t.owner) : `<span class="tag warn">needs owner</span>`}
        ${t.deadline ? dueLabel(t) : `<span class="tag warn">no date</span>`}
      </div>
    </div>`;
}

function renderBoard(tasks) {
  const owners = [...new Set(tasks.map((t) => t.owner || "Unassigned"))].sort((a, b) =>
    (a === "Unassigned") - (b === "Unassigned") || a.localeCompare(b));
  return `<div class="board">${owners.map((o) => {
    const mine = tasks.filter((t) => (t.owner || "Unassigned") === o)
      .sort((a, b) => (a.status !== "open") - (b.status !== "open") || (a.deadline || "9").localeCompare(b.deadline || "9"));
    return `<div class="lane"><div class="lane-head">${avatar(o)}${esc(o)}<span class="n">${mine.length}</span></div>
      ${mine.map((t) => `
        <div class="card-task ${dueState(t)}">
          <div>${esc(t.title)}</div>
          <div class="row-meta">${dueLabel(t)}${taskTags(t)}</div>
        </div>`).join("")}</div>`;
  }).join("")}</div>`;
}

async function loadTasks() {
  const body = $("#tasks-body");
  if (!body.children.length) body.innerHTML = skeleton(5);
  try {
    const [info, all] = await Promise.all([api("/api/tracker"), api("/api/tasks")]);
    $("#tracker-badge").innerHTML = info.mode === "github"
      ? `${icon("link")}Posting to GitHub <span class="mono">${esc(info.repo)}</span>`
      : `${icon("link")}Posting to the local mock tracker`;

    const ownerSel = $("#tasks-owner");
    const owners = [...new Set(all.map((t) => t.owner).filter(Boolean))].sort();
    const current = ownerSel.value;
    ownerSel.innerHTML = `<option value="">Everyone</option>` + owners.map((o) => `<option ${o === current ? "selected" : ""}>${esc(o)}</option>`).join("");

    if (lastConfirm) {
      const n = lastConfirm.tasks.length;
      const posted = lastConfirm.tasks.filter((t) => t.tracker_url).length;
      const where = lastConfirm.tracker.mode === "github" ? `GitHub (${lastConfirm.tracker.repo})` : "the local mock tracker";
      const closed = lastConfirm.closed?.length ? ` Closed ${plural(lastConfirm.closed.length, "obsolete task")}.` : "";
      $("#tasks-banner").innerHTML = `<div class="alert ${posted === n ? "ok" : "error"}">${icon(posted === n ? "check" : "alert")}
        <span>Confirmed. ${posted} of ${plural(n, "task")} posted to ${esc(where)}.${posted < n ? " Use Retry on the ones that failed." : ""}${closed}</span></div>`;
      lastConfirm = null;
    } else {
      $("#tasks-banner").innerHTML = "";
    }

    const status = $("#tasks-status").value;
    const tasks = all.filter((t) => (!ownerSel.value || t.owner === ownerSel.value)
      && (!status || (status === "open" ? t.status === "open" : t.status !== "open")));
    if (!tasks.length) {
      body.innerHTML = emptyState("tasks", all.length ? "No tasks match these filters." : "No tasks yet. Confirm a meeting to create them.");
      return;
    }
    body.innerHTML = taskView === "board" ? renderBoard(tasks) : DUE_GROUPS.map(([key, label]) => {
      const group = tasks.filter((t) => dueState(t) === key);
      return group.length ? `<div class="task-group ${key}"><h3>${label} <span class="n">${group.length}</span></h3>
        <div class="task-list">${group.map(renderTaskRow).join("")}</div></div>` : "";
    }).join("");
  } catch (err) {
    body.innerHTML = `<div class="alert error">${icon("alert")}${esc(err.message)}</div>`;
  }
}
$("#tasks-owner").addEventListener("change", loadTasks);
$("#tasks-status").addEventListener("change", loadTasks);
$$("[data-view]").forEach((b) => b.addEventListener("click", () => {
  taskView = b.dataset.view;
  $$("[data-view]").forEach((x) => x.classList.toggle("active", x === b));
  loadTasks();
}));

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
    if (box.checked) toast("Marked done");
  } catch (err) {
    toast(esc(err.message), "error");
  }
  loadTasks();
  loadStats();
});

// --- Decision memory: map + ask ------------------------------------------------------------------
let memoryData = [];
let selectedDecision = null;

async function loadMemory() {
  const body = $("#memory-body");
  if (!body.children.length) body.innerHTML = skeleton(4);
  try {
    memoryData = await api("/api/memory");
    renderMemory();
  } catch (err) {
    body.innerHTML = `<div class="alert error">${icon("alert")}${esc(err.message)}</div>`;
  }
}

function renderMemory() {
  const body = $("#memory-body");
  if (!memoryData.length) {
    body.innerHTML = emptyState("history", "No decisions in memory yet. Confirm a meeting to start the map.");
    return;
  }
  const meetings = [...new Map(memoryData.map((d) => [d.meeting_id, { id: d.meeting_id, title: d.meeting_title, date: d.meeting_date }])).values()]
    .sort((a, b) => a.date.localeCompare(b.date) || a.id - b.id);
  const topics = [...new Set(memoryData.map((d) => d.topic))];
  const byId = Object.fromEntries(memoryData.map((d) => [d.id, d]));
  const cols = `grid-template-columns: 160px repeat(${meetings.length}, minmax(190px, 1fr))`;
  let cells = `<div class="map-cell map-corner"></div>` + meetings.map((m) =>
    `<div class="map-cell map-col">${esc(m.title)}<span class="date">${fmtDate(m.date)}</span></div>`).join("");
  for (const topic of topics) {
    const items = memoryData.filter((d) => d.topic === topic);
    const changes = items.filter((d) => d.status === "superseded").length;
    cells += `<div class="map-cell map-row">${esc(topic)}<small>${changes ? `changed ${changes}×` : "stable"}</small></div>`;
    for (const m of meetings) {
      const here = items.filter((d) => d.meeting_id === m.id);
      const replacedLater = here.some((d) => d.status === "superseded" && byId[d.superseded_by]?.meeting_id !== m.id);
      cells += `<div class="map-cell">${here.map((d) => `
        <button class="chip-d ${d.status} ${d.id === selectedDecision ? "selected" : ""}" data-decision="${d.id}" title="${esc(d.speaker)}">${esc(d.text)}</button>`).join("")}
        ${replacedLater ? `<span class="link" title="Replaced in a later meeting">${icon("arrow")}</span>` : ""}</div>`;
    }
  }
  body.innerHTML = `
    <div class="map-wrap"><div class="map" style="${cols}">${cells}</div></div>
    <div class="map-legend">
      <span><i class="l-active"></i>Active decision</span>
      <span><i class="l-old"></i>Superseded</span>
      <span>${icon("arrow")}Replaced in a later meeting</span>
      <span class="faint">Click a decision for its history</span>
    </div>
    <div id="decision-detail"></div>`;
  if (selectedDecision && byId[selectedDecision]) renderDecisionDetail(byId[selectedDecision], byId);
}

function renderDecisionDetail(d, byId) {
  const replaces = memoryData.filter((x) => x.superseded_by === d.id);
  const next = d.superseded_by ? byId[d.superseded_by] : null;
  $("#decision-detail").innerHTML = `
    <div class="card detail">
      <div class="review-head">
        <div><span class="tag topic">${esc(d.topic)}</span> <span class="tag ${d.status === "active" ? "ok" : ""}">${d.status}</span>
          <h2 style="margin-top:8px;font-size:17px" class="${d.status === "superseded" ? "struck-text" : ""}">${esc(d.text)}</h2></div>
        <button class="icon-btn" style="opacity:1" data-close-detail title="Close">${icon("x")}</button>
      </div>
      <dl class="detail-grid">
        <dt>Decided by</dt><dd>${person(d.speaker)}</dd>
        <dt>Meeting</dt><dd>${esc(d.meeting_title)} · <span class="date">${fmtDate(d.meeting_date)}</span></dd>
        <dt>Confidence</dt><dd>${confidence(d.confidence)}</dd>
        ${replaces.length ? `<dt>Replaced</dt><dd>${replaces.map((r) => `<span class="struck-text">${esc(r.text)}</span> <span class="faint">(${esc(r.meeting_title)})</span>`).join("<br>")}</dd>` : ""}
        ${next ? `<dt>Replaced by</dt><dd>“${esc(next.text)}” <span class="faint">(${esc(next.meeting_title)}, ${fmtDate(next.meeting_date)})</span></dd>` : ""}
        ${d.change ? `<dt>Why flagged</dt><dd>${esc(d.change.explanation)}</dd>` : ""}
      </dl>
    </div>`;
}

$("#memory-body").addEventListener("click", (e) => {
  if (e.target.closest("[data-close-detail]")) {
    selectedDecision = null;
    return renderMemory();
  }
  const chip = e.target.closest("[data-decision]");
  if (!chip) return;
  selectedDecision = Number(chip.dataset.decision);
  renderMemory();
  $("#decision-detail").scrollIntoView({ block: "nearest", behavior: "smooth" });
});

async function askMemory(question) {
  const box = $("#ask-result");
  box.innerHTML = `<div class="card answer"><div class="skeleton" style="width:70%"></div><div class="skeleton" style="width:45%;margin-top:10px"></div></div>`;
  try {
    const r = await withButton($("#ask-btn"), "Asking…", () => api("/api/ask", { method: "POST", body: { question } }));
    const cites = [
      ...r.decisions.map((d) => `
        <div class="cite"><span class="tag topic">${esc(d.topic)}</span>
          <span class="${d.status === "superseded" ? "struck-text" : ""}">${esc(d.text)}</span>
          <span class="faint small">${esc(d.speaker)} · ${esc(d.meeting_title)} · ${fmtDate(d.meeting_date)}</span></div>`),
      ...r.tasks.map((t) => `
        <div class="cite"><span class="tag">task</span>${esc(t.title)}
          <span class="faint small">${esc(t.owner || "unassigned")}${t.deadline ? ` · ${fmtDate(t.deadline)}` : ""}${t.tracker_ref ? ` · ${esc(t.tracker_ref)}` : ""}</span></div>`),
    ];
    box.innerHTML = `
      <div class="card answer">
        <div class="answer-q">${esc(question)}</div>
        <p class="answer-text">${esc(r.answer)}</p>
        ${cites.length ? `<div class="cites"><div class="tiny faint">SOURCES FROM MEMORY</div>${cites.join("")}</div>` : ""}
        <div class="tiny faint">Answered by ${esc(r.source)} from stored decisions only</div>
      </div>`;
  } catch (err) {
    box.innerHTML = `<div class="alert error">${icon("alert")}${esc(err.message)}</div>`;
  } finally {
    loadHealth();
  }
}
$("#ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("#ask-input").value.trim();
  if (q) askMemory(q);
});

// --- Digest -----------------------------------------------------------------------------------------
let digestPerson = null;

async function loadDigestPeople() {
  const box = $("#digest-people");
  try {
    const people = await api("/api/digest/people");
    box.innerHTML = people.length
      ? people.map((p) => `<button class="person-chip ${p === digestPerson ? "active" : ""}" data-person="${esc(p)}">${avatar(p)}${esc(p)}</button>`).join("")
      : `<span class="faint small">No people yet. Confirm a meeting first.</span>`;
    if (!digestPerson) $("#digest-body").innerHTML = emptyState("mail", "Pick a person to write their weekly digest.");
  } catch (err) {
    box.innerHTML = `<span class="error-text small">${esc(err.message)}</span>`;
  }
}

function digestTaskList(items, empty) {
  if (!items.length) return `<p class="faint small">${empty}</p>`;
  return items.map((t) => `
    <div class="digest-task"><span>${esc(t.title)}</span>
      <span class="meta">${dueLabel({ ...t, status: "open" })}${t.tracker_ref ? `<span class="tag ref">${esc(t.tracker_ref)}</span>` : ""}</span></div>`).join("");
}

function renderDigest(d) {
  const n = d.narrative;
  const later = d.open_items.filter((t) => !d.due_this_week.some((x) => x.id === t.id) && !d.overdue.some((x) => x.id === t.id));
  $("#digest-body").innerHTML = `
    <div class="card">
      <div class="digest-head">
        ${avatar(d.person, "lg")}
        <div style="flex:1">
          <h2>${esc(n.subject)}</h2>
          <span class="faint small"><span class="date">${fmtDate(d.week.start)} – ${fmtDate(d.week.end)}</span> · written by ${esc(d.source)}</span>
        </div>
        <button class="btn small ghost" id="digest-copy">${icon("copy")}Copy as Markdown</button>
      </div>
      <p class="digest-summary">${esc(n.summary)}</p>
      <div class="grid-2">
        <div><h3>What changed</h3>
          ${n.highlights.length ? `<ul class="bullets">${n.highlights.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>` : `<p class="faint small">No new decisions affecting you this week.</p>`}</div>
        <div><h3>Heads up</h3>
          ${n.heads_up.length ? `<ul class="bullets warn">${n.heads_up.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>` : `<p class="faint small">Nothing needs attention.</p>`}</div>
      </div>
    </div>
    <div class="grid-3">
      <div class="card"><h3>Overdue <span class="faint">${d.overdue.length}</span></h3>${digestTaskList(d.overdue, "Nothing overdue.")}</div>
      <div class="card"><h3>Next 7 days <span class="faint">${d.due_this_week.length}</span></h3>${digestTaskList(d.due_this_week, "Nothing due this week.")}</div>
      <div class="card"><h3>Also open <span class="faint">${later.length}</span></h3>${digestTaskList(later, "No other open items.")}</div>
    </div>
    ${d.unresolved_conflicts.length ? `
      <div class="card">
        <h3>Unresolved conflicts involving ${esc(d.person)}</h3>
        ${d.unresolved_conflicts.map((c) => `<p class="small" style="margin-bottom:8px"><span class="kind ${esc(c.kind)}">${icon("alert")}${esc(c.kind)}</span> ${esc(c.explanation)}</p>`).join("")}
        <button class="btn small primary" data-goto="review">Resolve in Review</button>
      </div>` : ""}`;
}

async function loadDigest(person) {
  digestPerson = person;
  loadDigestPeople();
  $("#digest-body").innerHTML = skeleton(5);
  try {
    renderDigest(await api(`/api/digest/${encodeURIComponent(person)}`));
  } catch (err) {
    $("#digest-body").innerHTML = `<div class="alert error">${icon("alert")}${esc(err.message)}</div>`;
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
    toast("Digest copied as Markdown");
  } catch {
    toast("Couldn't copy to the clipboard", "error");
  }
});

// --- Reminders --------------------------------------------------------------------------------------
async function loadReminderStatus() {
  try {
    const s = await api("/api/reminders/status");
    $("#reminder-status").innerHTML = `${icon("clock")}${s.scheduler_running
      ? `Checks every ${s.interval_minutes} min · last ${fmtTime(s.last_run)} · next ${fmtTime(s.next_run)}`
      : `Background checks off · last ${fmtTime(s.last_run)}`}`;
  } catch {
    $("#reminder-status").innerHTML = `${icon("clock")}Scheduler status unavailable`;
  }
}

function renderNotification(n) {
  const overdue = n.kind === "overdue";
  return `
    <div class="notif ${n.read ? "read" : ""} ${overdue ? "overdue" : "soon"}">
      <div class="notif-icon">${icon(overdue ? "alert" : "clock")}</div>
      <div class="body">
        <div>${esc(n.message)}</div>
        <div class="row-meta">${person(n.person)}
          <span class="tag ${overdue ? "danger" : "warn"}">${overdue ? "overdue" : "due soon"}</span>
          ${n.tracker_ref ? `<span class="tag ref">${esc(n.tracker_ref)}</span>` : ""}
          <span class="date">${fmtTime(n.created_at)}</span></div>
      </div>
      ${n.read ? "" : `<button class="btn small ghost" data-read="${n.id}">Mark read</button>`}
    </div>`;
}

async function loadReminders() {
  loadReminderStatus();
  const body = $("#notif-body");
  if (!body.children.length) body.innerHTML = skeleton(3);
  try {
    const all = await api("/api/notifications");
    const sel = $("#notif-person");
    const people = [...new Set(all.map((n) => n.person))].sort();
    const current = sel.value;
    sel.innerHTML = `<option value="">Everyone</option>` + people.map((p) => `<option ${p === current ? "selected" : ""}>${esc(p)}</option>`).join("");
    const list = all.filter((n) => !sel.value || n.person === sel.value);
    body.innerHTML = list.length
      ? `<div class="task-list">${list.map(renderNotification).join("")}</div>`
      : emptyState("bell", "No reminders. Items due within a day or overdue appear here; try “Run reminders now”.");
  } catch (err) {
    body.innerHTML = `<div class="alert error">${icon("alert")}${esc(err.message)}</div>`;
  }
}
$("#notif-person").addEventListener("change", loadReminders);

async function runReminders(btn) {
  try {
    const res = await withButton(btn || $("#run-reminders"), "Checking…", () => api("/api/reminders/run", { method: "POST" }));
    const n = res.created.length;
    $("#reminder-banner").innerHTML = `<div class="alert ${n ? "ok" : ""}">${icon(n ? "check" : "clock")}<span>${n
      ? `Created ${plural(n, "reminder")}.`
      : "No new reminders: everything due soon or overdue was already flagged today."}</span></div>`;
  } catch (err) {
    $("#reminder-banner").innerHTML = `<div class="alert error">${icon("alert")}${esc(err.message)}</div>`;
  }
  loadReminders();
  refreshBadges();
}
$("#run-reminders").addEventListener("click", (e) => runReminders(e.currentTarget));

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

// --- Demo preflight ------------------------------------------------------------------------------------
const PREFLIGHT_ICON = { ok: ["check", "ok"], warn: ["alert", "warn"], fail: ["x", "danger"] };

async function runPreflight() {
  const dialog = $("#preflight-dialog");
  if (!dialog.open) dialog.showModal();
  $("#preflight-body").innerHTML = `<p class="muted small" style="margin-bottom:12px">Checking the model, cache, tracker and demo data…</p>
    ${[80, 60, 70, 50].map((w) => `<div class="skeleton" style="width:${w}%;margin-top:12px"></div>`).join("")}`;
  try {
    const r = await api("/api/preflight");
    const verdict = { ok: "Everything is ready.", warn: "Ready, with things worth a look.", fail: "Something needs fixing before you present." }[r.status];
    $("#preflight-body").innerHTML = `
      <div class="alert ${r.status === "ok" ? "ok" : r.status === "fail" ? "error" : ""}">${icon(PREFLIGHT_ICON[r.status][0])}<span>${verdict}</span></div>
      <ul class="preflight">${r.checks.map((c) => `
        <li><span class="tag ${PREFLIGHT_ICON[c.status][1]}">${icon(PREFLIGHT_ICON[c.status][0])}${c.status}</span>
          <div><strong>${esc(c.name)}</strong><div class="small muted">${esc(c.detail)}</div>
          ${c.fix ? `<div class="small">${esc(c.fix)}</div>` : ""}</div></li>`).join("")}</ul>`;
  } catch (err) {
    $("#preflight-body").innerHTML = `<div class="alert error">${icon("alert")}<span>${esc(err.message)}</span></div>`;
  } finally {
    loadHealth();
  }
}
$("#demo-preflight").addEventListener("click", runPreflight);
$("#preflight-rerun").addEventListener("click", runPreflight);
$("#preflight-close").addEventListener("click", () => $("#preflight-dialog").close());

// --- Command palette & keyboard ----------------------------------------------------------------------
let paletteItems = [];
let paletteSel = 0;

async function paletteCommands(query) {
  const nav = Object.entries(PAGES).map(([tab, [title]]) => ({
    group: "Go to", label: title, icon: $(`.tab[data-tab="${tab}"] use`).getAttribute("href").slice(3), run: () => showTab(tab),
  }));
  const actions = [
    { label: "Start dictating a meeting", icon: "mic", run: () => { showTab("new"); $("#m-transcript").focus(); } },
    { label: "Load demo meetings", icon: "play", run: () => loadDemo() },
    { label: "Run demo preflight check", icon: "check", run: runPreflight },
    { label: "Insert sample transcript", icon: "file", run: () => { showTab("new"); insertSample(); } },
    { label: "Run reminders now", icon: "bell", run: () => { showTab("reminders"); runReminders(); } },
    { label: "Toggle light / dark theme", icon: "reset", run: toggleTheme },
    { label: "Reset all data", icon: "trash", run: resetAll },
  ].map((a) => ({ ...a, group: "Actions" }));
  let people = [];
  try { people = await api("/api/digest/people"); } catch { /* optional */ }
  const digests = people.map((p) => ({ group: "Digests", label: `Digest for ${p}`, icon: "mail", run: () => { showTab("digest"); loadDigest(p); } }));
  const q = query.trim().toLowerCase();
  const matches = [...nav, ...actions, ...digests].filter((c) => !q || q.split(/\s+/).every((w) => c.label.toLowerCase().includes(w)));
  if (q) matches.push({ group: "Ask memory", label: `Ask: “${query.trim()}”`, icon: "message", run: () => { showTab("memory"); $("#ask-input").value = query.trim(); askMemory(query.trim()); } });
  return matches;
}

async function renderPalette() {
  paletteItems = await paletteCommands($("#palette-input").value);
  paletteSel = Math.min(paletteSel, Math.max(paletteItems.length - 1, 0));
  let lastGroup = "";
  $("#palette-list").innerHTML = paletteItems.map((c, i) => {
    const head = c.group !== lastGroup ? `<li class="group">${esc(c.group)}</li>` : "";
    lastGroup = c.group;
    return `${head}<li role="option" data-cmd="${i}" class="${i === paletteSel ? "sel" : ""}">${icon(c.icon)}${esc(c.label)}</li>`;
  }).join("") || `<li class="group">No matches</li>`;
  $("#palette-list .sel")?.scrollIntoView({ block: "nearest" });
}

function openPalette() {
  const p = $("#palette");
  if (p.open) return;
  $("#palette-input").value = "";
  paletteSel = 0;
  p.showModal();
  $("#palette-input").focus();
  renderPalette();
}
function runPaletteItem(i) {
  const cmd = paletteItems[i];
  $("#palette").close();
  cmd?.run();
}
$("#palette-open").addEventListener("click", openPalette);
$("#palette-input").addEventListener("input", () => { paletteSel = 0; renderPalette(); });
$("#palette-input").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    paletteSel = (paletteSel + (e.key === "ArrowDown" ? 1 : -1) + paletteItems.length) % Math.max(paletteItems.length, 1);
    renderPalette();
  } else if (e.key === "Enter") {
    e.preventDefault();
    runPaletteItem(paletteSel);
  }
});
$("#palette-list").addEventListener("click", (e) => {
  const li = e.target.closest("[data-cmd]");
  if (li) runPaletteItem(Number(li.dataset.cmd));
});
$("#palette").addEventListener("click", (e) => { if (e.target.id === "palette") $("#palette").close(); });

function toggleTheme() {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("ft-theme", document.documentElement.dataset.theme); } catch { /* optional */ }
}
try {
  const saved = localStorage.getItem("ft-theme");
  if (saved) document.documentElement.dataset.theme = saved;
} catch { /* optional */ }

const TAB_KEYS = { 1: "new", 2: "review", 3: "tasks", 4: "memory", 5: "digest", 6: "reminders" };
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    return $("#palette").open ? $("#palette").close() : openPalette();
  }
  const typing = e.target.closest("input, textarea, select, [contenteditable]") || document.querySelector("dialog[open]");
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
  if (TAB_KEYS[e.key]) return showTab(TAB_KEYS[e.key]);
  // Resolve the next open conflict from the keyboard on the Review screen.
  const res = { s: "supersede", k: "keep", d: "dismiss" }[e.key.toLowerCase()];
  if (res && currentTab === "review" && draft) {
    const next = draft.conflicts.find((c) => !c.resolution);
    if (next) resolveConflict(next.id, res).catch((err) => showReviewError(err.message));
  }
});

// --- boot ---------------------------------------------------------------------------------------------
setInterval(refreshBadges, 60000);  // the scheduler creates reminders in the background
showTab("new");
renderLive(null);
loadHealth();
refreshBadges();
