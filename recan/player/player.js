"use strict";

/* =========================================================================
   Code Playback — player runtime
   ========================================================================= */

const BUNDLE = window.__BUNDLE__;
const $ = (id) => document.getElementById(id);

const STATE = {
  events:          BUNDLE.events,
  bursts:          BUNDLE.bursts || [],
  idleGaps:        BUNDLE.idle_gaps || [],
  focusIntervals:  BUNDLE.focus_intervals || [],
  snapshots:       BUNDLE.snapshots || [],
  meta:            BUNDLE.metadata,
  summary:         BUNDLE.summary,

  playheadIdx: 0,
  playing: false,
  speed: 1,
  document: "",
};

/* ---------- document reconstruction ---------- */

function applyEdit(text, offset, oldFragment, newFragment) {
  const end = offset + oldFragment.length;
  return text.slice(0, offset) + newFragment + text.slice(end);
}

function findSnapshotAtOrBefore(targetIdx) {
  let candidate = null;
  for (const snap of STATE.snapshots) {
    if (snap.after_idx <= targetIdx) candidate = snap;
    else break;
  }
  return candidate;
}

function rebuildDocumentTo(targetIdx) {
  if (targetIdx < 0) { STATE.document = ""; return; }
  const snap = findSnapshotAtOrBefore(targetIdx);
  let doc, cursor;
  if (snap) { doc = snap.document_text; cursor = snap.after_idx; }
  else      { doc = ""; cursor = -1; }
  for (let i = cursor + 1; i <= targetIdx; i++) {
    const ev = STATE.events[i];
    if (ev.type === "edit") {
      doc = applyEdit(doc, ev.offset, ev.oldFragment, ev.newFragment);
    }
  }
  STATE.document = doc;
}

function stepForward() {
  if (STATE.playheadIdx >= STATE.events.length - 1) return false;
  STATE.playheadIdx += 1;
  const ev = STATE.events[STATE.playheadIdx];
  if (ev.type === "edit") {
    STATE.document = applyEdit(STATE.document, ev.offset, ev.oldFragment, ev.newFragment);
  }
  return true;
}

/* ---------- formatting ---------- */

function fmtDuration(s) {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

function formatAbsoluteTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  const startD = new Date(STATE.meta.start_time);
  const dayD = d.toISOString().slice(0, 10);
  const dayStart = startD.toISOString().slice(0, 10);
  const time = d.toISOString().slice(11, 19);
  return dayD === dayStart ? time : `${dayD} ${time}`;
}

function basename(p) {
  if (!p) return "(unknown)";
  const parts = p.split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

/* ---------- editor render ---------- */

function renderEditor() {
  const codeEl = $("editor-code");
  codeEl.textContent = STATE.document;
  codeEl.className = "language-" + (STATE.meta.language || "plaintext");
  if (window.hljs && window.hljs.highlightElement) {
    delete codeEl.dataset.highlighted;
    try { window.hljs.highlightElement(codeEl); } catch (e) { /* noop */ }
  }
}

function renderTopbar() {
  const full = STATE.meta.document || "(unknown)";
  const f = $("filename");
  f.textContent = basename(full);
  f.title = full;
  $("lang-pill").textContent = STATE.meta.language || "plaintext";
}

/* ---------- editor summary chips ---------- */

function renderSummaryChips() {
  $("sum-edits").textContent     = STATE.summary.edit_count;
  $("sum-bursts").textContent    = STATE.summary.burst_count;
  $("sum-unfocused").textContent = fmtDuration(STATE.summary.unfocused_seconds);
  $("sum-idle").textContent      = String(STATE.idleGaps.length);
}

/* ---------- stats sidebar ---------- */

function renderStatsSidebar() {
  const focused = Math.max(0, STATE.summary.total_seconds - STATE.summary.unfocused_seconds);
  const pctFocused = STATE.summary.total_seconds
    ? Math.round((focused / STATE.summary.total_seconds) * 100)
    : 100;

  const startedAt = formatAbsoluteTime(STATE.meta.start_time);
  const endedAt   = formatAbsoluteTime(STATE.meta.end_time);

  const stats = `
    <div class="stats-section">
      <div class="stats-section-title">Session</div>
      <div class="stat-grid">
        <div class="stat full">
          <span class="stat-label">Total time</span>
          <span class="stat-value">${fmtDuration(STATE.summary.total_seconds)}</span>
          <span class="delta">${startedAt} → ${endedAt}</span>
        </div>
        <div class="stat">
          <span class="stat-label">Focused</span>
          <span class="stat-value">${fmtDuration(focused)}</span>
          <span class="delta">${pctFocused}% of total</span>
        </div>
        <div class="stat">
          <span class="stat-label">Unfocused</span>
          <span class="stat-value">${fmtDuration(STATE.summary.unfocused_seconds)}</span>
          <span class="delta">${100 - pctFocused}% of total</span>
        </div>
        <div class="stat">
          <span class="stat-label">Edits</span>
          <span class="stat-value">${STATE.summary.edit_count}</span>
        </div>
        <div class="stat">
          <span class="stat-label">Bursts</span>
          <span class="stat-value">${STATE.summary.burst_count}</span>
        </div>
      </div>
    </div>
    <div class="stats-section">
      <div class="stats-section-title">Flags <span style="color:var(--fg-subtle);font-weight:500;letter-spacing:0">${STATE.bursts.length + STATE.focusIntervals.length}</span></div>
      <div class="flags" id="flags-list"></div>
    </div>
  `;
  $("stats").innerHTML = stats;
  renderFlagsList();
}

function renderFlagsList() {
  const host = $("flags-list");
  if (!host) return;

  const items = [];
  // bursts
  STATE.bursts.forEach((b, i) => {
    const startT = STATE.events[b.start_idx]?.timestamp;
    const dur = b.end_idx > b.start_idx
      ? (Date.parse(STATE.events[b.end_idx].timestamp) - Date.parse(STATE.events[b.start_idx].timestamp)) / 1000
      : 0;
    items.push({
      kind: "burst",
      title: `Burst #${i + 1}`,
      meta: `${fmtDuration(dur)} · ${(b.end_idx - b.start_idx + 1)} edits`,
      idx: b.start_idx,
      sortT: Date.parse(startT || STATE.meta.start_time),
    });
  });
  // unfocused
  STATE.focusIntervals.forEach((fi, i) => {
    const startT = STATE.events[fi.blur_idx]?.timestamp;
    const dur = fi.focus_idx > fi.blur_idx
      ? (Date.parse(STATE.events[fi.focus_idx].timestamp) - Date.parse(STATE.events[fi.blur_idx].timestamp)) / 1000
      : 0;
    items.push({
      kind: "unfocused",
      title: `Unfocused #${i + 1}`,
      meta: `${fmtDuration(dur)} away`,
      idx: fi.blur_idx,
      sortT: Date.parse(startT || STATE.meta.start_time),
    });
  });

  if (items.length === 0) {
    host.innerHTML = `<div class="flag-empty">No anomalies detected — looks like a clean session.</div>`;
    return;
  }

  items.sort((a, b) => a.sortT - b.sortT);

  host.innerHTML = items.map((it) => `
    <button class="flag ${it.kind}" data-idx="${it.idx}">
      <span class="flag-icon">${it.kind === "burst" ? "!" : "↗"}</span>
      <span class="flag-body">
        <span class="flag-title">${it.title}</span>
        <span class="flag-meta">${it.meta}</span>
      </span>
    </button>
  `).join("");

  host.querySelectorAll(".flag").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.getAttribute("data-idx"), 10);
      scrubToIdx(idx);
    });
  });
}

/* ---------- visual timeline math ---------- */

const IDLE_AFTER_IDX = new Set(STATE.idleGaps.map((g) => g.after_idx));
let skipIdle = false;
let VISUAL = computeVisual();

function computeVisual() {
  const offs = new Array(STATE.events.length);
  if (!STATE.events.length) return { offsets: offs, total: 0 };
  let acc = 0;
  offs[0] = 0;
  let prev = Date.parse(STATE.events[0].timestamp);
  for (let i = 1; i < STATE.events.length; i++) {
    const t = Date.parse(STATE.events[i].timestamp);
    const real = (t - prev) / 1000;
    const step = skipIdle && IDLE_AFTER_IDX.has(i - 1) ? 0 : real;
    acc += step;
    offs[i] = acc;
    prev = t;
  }
  return { offsets: offs, total: acc };
}

function visualPctForIdx(idx) {
  if (VISUAL.total <= 0) return 0;
  const o = VISUAL.offsets[Math.max(0, Math.min(idx, VISUAL.offsets.length - 1))];
  return (o / VISUAL.total) * 100;
}

function visualPctForIdxAfter(idx) {
  if (idx < 0) return 0;
  if (idx >= VISUAL.offsets.length - 1) return 100;
  const next = VISUAL.offsets[idx + 1];
  if (VISUAL.total <= 0) return 0;
  return (next / VISUAL.total) * 100;
}

/* ---------- timeline render ---------- */

function renderTimelineMarkers() {
  const host = $("timeline-markers");
  const parts = [];

  for (const gap of STATE.idleGaps) {
    const a = visualPctForIdx(gap.after_idx);
    const b = visualPctForIdxAfter(gap.after_idx);
    parts.push(`<div class="seg-idle" style="left:${a}%;width:${Math.max(0.4, b - a)}%"></div>`);
  }
  for (const fi of STATE.focusIntervals) {
    const a = visualPctForIdx(fi.blur_idx);
    const b = visualPctForIdx(fi.focus_idx);
    parts.push(`<div class="seg-unfocused" style="left:${a}%;width:${Math.max(0.6, b - a)}%"></div>`);
  }
  for (const burst of STATE.bursts) {
    const a = visualPctForIdx(burst.start_idx);
    parts.push(`<div class="tick-burst" style="left:${a}%"></div>`);
  }
  host.innerHTML = parts.join("");
}

function renderPlayhead() {
  const idx = Math.max(0, STATE.playheadIdx);
  const pct = visualPctForIdx(idx);
  $("playhead").style.left = pct + "%";
  $("progress-fill").style.width = pct + "%";
  $("es-progress").textContent = Math.round(pct) + "%";
}

function renderTimeReadout() {
  const ev = STATE.playheadIdx >= 0 ? STATE.events[STATE.playheadIdx] : null;
  $("now-time").textContent = ev ? formatAbsoluteTime(ev.timestamp) : formatAbsoluteTime(STATE.meta.start_time);
  $("total-time").textContent = " / " + formatAbsoluteTime(STATE.meta.end_time);
}

/* ---------- playback loop ---------- */

let lastTick = 0;
let rafId = null;
let accumBudget = 0;

function tick(now) {
  if (!STATE.playing) return;
  if (lastTick === 0) lastTick = now;
  const dtMs = now - lastTick;
  lastTick = now;
  accumBudget += (dtMs / 1000) * STATE.speed;

  while (STATE.playheadIdx < STATE.events.length - 1) {
    const cur = STATE.playheadIdx >= 0 ? VISUAL.offsets[STATE.playheadIdx] : 0;
    const nextIdx = STATE.playheadIdx + 1;
    const next = VISUAL.offsets[nextIdx];
    const step = next - cur;
    if (step <= accumBudget) {
      stepForward();
      accumBudget -= step;
    } else {
      break;
    }
  }

  renderEditor();
  renderPlayhead();
  renderTimeReadout();
  updateUnfocusedOverlay();
  maybeFlashBurst();

  if (STATE.playheadIdx >= STATE.events.length - 1) {
    setPlaying(false);
    return;
  }
  rafId = requestAnimationFrame(tick);
}

const PLAY_SVG  = '<path d="M4 2.5v11l9-5.5z"/>';
const PAUSE_SVG = '<path d="M4 2.5h3v11H4zM9 2.5h3v11H9z"/>';

function setPlaying(p) {
  STATE.playing = p;
  $("play-icon").innerHTML = p ? PAUSE_SVG : PLAY_SVG;
  $("play-btn").setAttribute("aria-label", p ? "Pause" : "Play");
  if (p) {
    lastTick = 0;
    accumBudget = 0;
    rafId = requestAnimationFrame(tick);
  } else if (rafId) {
    cancelAnimationFrame(rafId);
    rafId = null;
  }
}

/* ---------- scrubbing ---------- */

function scrubToIdx(idx) {
  idx = Math.max(0, Math.min(idx, STATE.events.length - 1));
  if (STATE.playing) setPlaying(false);
  STATE.playheadIdx = idx;
  rebuildDocumentTo(idx);
  renderEditor();
  renderPlayhead();
  renderTimeReadout();
  updateUnfocusedOverlay();
}

function visualPctToEventIdx(pct) {
  if (VISUAL.total <= 0 || !VISUAL.offsets.length) return -1;
  const target = (pct / 100) * VISUAL.total;
  let lo = 0, hi = VISUAL.offsets.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (VISUAL.offsets[mid] < target) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function scrubToClientX(clientX) {
  const track = $("timeline-track");
  const rect = track.getBoundingClientRect();
  const pct = Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100));
  const idx = visualPctToEventIdx(pct);
  STATE.playheadIdx = idx;
  rebuildDocumentTo(idx);
  renderEditor();
  renderPlayhead();
  renderTimeReadout();
  updateUnfocusedOverlay();
}

/* ---------- timeline tooltip ---------- */

function timelineHover(clientX) {
  const track = $("timeline-track");
  const tooltip = $("timeline-tooltip");
  const rect = track.getBoundingClientRect();
  const pct = Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100));
  const idx = visualPctToEventIdx(pct);
  if (idx < 0) { tooltip.classList.remove("show"); return; }
  const ev = STATE.events[idx];
  if (!ev) return;

  // classify
  let tag = "";
  for (const b of STATE.bursts) {
    if (idx >= b.start_idx && idx <= b.end_idx) { tag = `<span class="tt-tag burst">Burst</span>`; break; }
  }
  if (!tag) for (const fi of STATE.focusIntervals) {
    if (idx >= fi.blur_idx && idx < fi.focus_idx) { tag = `<span class="tt-tag unfocused">Unfocused</span>`; break; }
  }
  if (!tag && IDLE_AFTER_IDX.has(idx)) tag = `<span class="tt-tag idle">Idle</span>`;

  const elapsed = (Date.parse(ev.timestamp) - Date.parse(STATE.meta.start_time)) / 1000;
  tooltip.innerHTML = `+${fmtDuration(elapsed)}${tag}`;
  tooltip.style.left = pct + "%";
  tooltip.classList.add("show");
}

/* ---------- burst flash + unfocused overlay ---------- */

let _activeBurst = null;
let _flashTimer = null;

function maybeFlashBurst() {
  for (const burst of STATE.bursts) {
    if (STATE.playheadIdx >= burst.start_idx && STATE.playheadIdx <= burst.end_idx) {
      if (_activeBurst === burst) {
        $("burst-banner").classList.add("show");
        return;
      }
      _activeBurst = burst;
      const wrap = $("editor-wrap");
      wrap.classList.remove("burst-flash");
      // restart animation
      void wrap.offsetWidth;
      wrap.classList.add("burst-flash");
      $("burst-banner").classList.add("show");
      if (_flashTimer) clearTimeout(_flashTimer);
      _flashTimer = setTimeout(() => {
        wrap.classList.remove("burst-flash");
      }, 700);
      return;
    }
  }
  _activeBurst = null;
  $("burst-banner").classList.remove("show");
}

function updateUnfocusedOverlay() {
  const overlay = $("unfocused-overlay");
  const elapsedEl = $("unfocused-elapsed");

  const idx = STATE.playheadIdx;
  let active = null;
  for (const fi of STATE.focusIntervals) {
    if (idx >= fi.blur_idx && idx < fi.focus_idx) { active = fi; break; }
  }
  if (!active) { overlay.hidden = true; return; }

  overlay.hidden = false;
  const blurT = Date.parse(STATE.events[active.blur_idx].timestamp);
  const nowT = Date.parse(STATE.events[Math.max(0, idx)].timestamp);
  const elapsedSec = Math.max(0, Math.round((nowT - blurT) / 1000));
  const m = Math.floor(elapsedSec / 60);
  const s = elapsedSec % 60;
  elapsedEl.textContent = `${m}:${String(s).padStart(2, "0")}`;
}

/* ---------- theme ---------- */

function setTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem("playback-theme", t); } catch (e) {}
}

function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") || "light";
  setTheme(cur === "dark" ? "light" : "dark");
}

/* ---------- jump-to-flag ---------- */

function jumpToBurst(dir) {
  const cur = STATE.playheadIdx;
  if (dir > 0) {
    for (const b of STATE.bursts) {
      if (b.start_idx > cur) { scrubToIdx(b.start_idx); return; }
    }
  } else {
    for (let i = STATE.bursts.length - 1; i >= 0; i--) {
      if (STATE.bursts[i].start_idx < cur) { scrubToIdx(STATE.bursts[i].start_idx); return; }
    }
  }
}

function jumpToUnfocused(dir) {
  const cur = STATE.playheadIdx;
  if (dir > 0) {
    for (const fi of STATE.focusIntervals) {
      if (fi.blur_idx > cur) { scrubToIdx(fi.blur_idx); return; }
    }
  } else {
    for (let i = STATE.focusIntervals.length - 1; i >= 0; i--) {
      if (STATE.focusIntervals[i].blur_idx < cur) { scrubToIdx(STATE.focusIntervals[i].blur_idx); return; }
    }
  }
}

/* ---------- init ---------- */

rebuildDocumentTo(STATE.events.length - 1);
renderTopbar();
renderSummaryChips();
renderStatsSidebar();
renderEditor();

STATE.playheadIdx = -1;
rebuildDocumentTo(STATE.playheadIdx);
renderEditor();

renderTimelineMarkers();
renderPlayhead();
renderTimeReadout();

/* ---------- event listeners ---------- */

$("play-btn").addEventListener("click", () => {
  if (STATE.playheadIdx >= STATE.events.length - 1) {
    STATE.playheadIdx = -1;
    rebuildDocumentTo(-1);
    renderEditor();
    renderPlayhead();
    renderTimeReadout();
  }
  setPlaying(!STATE.playing);
});

$("speed").addEventListener("change", (e) => {
  STATE.speed = parseFloat(e.target.value);
});

$("skip-idle").addEventListener("change", (e) => {
  skipIdle = e.target.checked;
  VISUAL = computeVisual();
  renderTimelineMarkers();
  renderPlayhead();
  accumBudget = 0;
});

$("theme-btn").addEventListener("click", toggleTheme);

$("prev-burst").addEventListener("click", () => jumpToBurst(-1));
$("next-burst").addEventListener("click", () => jumpToBurst(1));
$("prev-unfocused").addEventListener("click", () => jumpToUnfocused(-1));
$("next-unfocused").addEventListener("click", () => jumpToUnfocused(1));

$("help-btn").addEventListener("click", () => {
  alert(
    "Keyboard shortcuts\n\n" +
    "Space          Play / Pause\n" +
    "←  →           Scrub one event\n" +
    "Shift + ← / →  Scrub 25 events\n" +
    "[  ]           Previous / next burst\n" +
    "Shift + [ / ]  Previous / next unfocused\n" +
    "Home / End     Jump to start / end\n" +
    "T              Toggle theme"
  );
});

/* timeline scrubbing + hover */
(() => {
  const track = $("timeline-track");
  const tooltip = $("timeline-tooltip");
  let dragging = false;

  track.addEventListener("mousedown", (e) => {
    dragging = true;
    if (STATE.playing) setPlaying(false);
    scrubToClientX(e.clientX);
  });
  window.addEventListener("mousemove", (e) => {
    if (dragging) scrubToClientX(e.clientX);
  });
  window.addEventListener("mouseup", () => { dragging = false; });

  track.addEventListener("mousemove", (e) => timelineHover(e.clientX));
  track.addEventListener("mouseleave", () => tooltip.classList.remove("show"));
})();

/* keyboard */
window.addEventListener("keydown", (e) => {
  if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
  const shift = e.shiftKey;
  switch (e.key) {
    case " ":
      e.preventDefault();
      if (STATE.playheadIdx >= STATE.events.length - 1) {
        STATE.playheadIdx = -1;
        rebuildDocumentTo(-1);
        renderEditor();
        renderPlayhead();
        renderTimeReadout();
      }
      setPlaying(!STATE.playing);
      break;
    case "ArrowLeft":
      e.preventDefault();
      scrubToIdx(STATE.playheadIdx - (shift ? 25 : 1));
      break;
    case "ArrowRight":
      e.preventDefault();
      scrubToIdx(STATE.playheadIdx + (shift ? 25 : 1));
      break;
    case "[":
      e.preventDefault();
      shift ? jumpToUnfocused(-1) : jumpToBurst(-1);
      break;
    case "]":
      e.preventDefault();
      shift ? jumpToUnfocused(1) : jumpToBurst(1);
      break;
    case "Home":
      e.preventDefault();
      scrubToIdx(0);
      break;
    case "End":
      e.preventDefault();
      scrubToIdx(STATE.events.length - 1);
      break;
    case "t":
    case "T":
      toggleTheme();
      break;
  }
});

/* Disable jump buttons if no flags */
if (!STATE.bursts.length) {
  $("prev-burst").disabled = true;
  $("next-burst").disabled = true;
}
if (!STATE.focusIntervals.length) {
  $("prev-unfocused").disabled = true;
  $("next-unfocused").disabled = true;
}

window.__STATE__ = STATE;
window.__rebuildDocumentTo = rebuildDocumentTo;
