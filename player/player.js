"use strict";

const BUNDLE = window.__BUNDLE__;

const $ = (id) => document.getElementById(id);

const STATE = {
  events: BUNDLE.events,
  bursts: BUNDLE.bursts,
  idleGaps: BUNDLE.idle_gaps,
  focusIntervals: BUNDLE.focus_intervals,
  snapshots: BUNDLE.snapshots,
  meta: BUNDLE.metadata,
  summary: BUNDLE.summary,

  playheadIdx: 0,
  playing: false,
  speed: 1,

  document: "",
};

function applyEdit(text, offset, oldFragment, newFragment) {
  const end = offset + oldFragment.length;
  return text.slice(0, offset) + newFragment + text.slice(end);
}

function findSnapshotAtOrBefore(targetIdx) {
  let candidate = null;
  for (const snap of STATE.snapshots) {
    if (snap.after_idx <= targetIdx) {
      candidate = snap;
    } else {
      break;
    }
  }
  return candidate;
}

function rebuildDocumentTo(targetIdx) {
  if (targetIdx < 0) {
    STATE.document = "";
    return;
  }
  const snap = findSnapshotAtOrBefore(targetIdx);
  let doc;
  let cursor;
  if (snap) {
    doc = snap.document_text;
    cursor = snap.after_idx;
  } else {
    doc = "";
    cursor = -1;
  }
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

window.__STATE__ = STATE;
window.__rebuildDocumentTo = rebuildDocumentTo;

function renderEditor() {
  const codeEl = $("editor-code");
  codeEl.textContent = STATE.document;
  codeEl.className = "language-" + (STATE.meta.language || "plaintext");
  if (window.hljs && window.hljs.highlightElement) {
    delete codeEl.dataset.highlighted;
    window.hljs.highlightElement(codeEl);
  }
}

function renderTopbar() {
  $("filename").textContent = STATE.meta.document || "(unknown)";
  $("lang-pill").textContent = STATE.meta.language || "plaintext";
}

function renderStats() {
  const fmt = (s) => {
    s = Math.max(0, Math.round(s));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
  };
  const focused = Math.max(0, STATE.summary.total_seconds - STATE.summary.unfocused_seconds);
  const html = [
    ["Total", fmt(STATE.summary.total_seconds)],
    ["Focused", fmt(focused)],
    ["Unfocused", fmt(STATE.summary.unfocused_seconds)],
    ["Bursts", String(STATE.summary.burst_count)],
    ["Edits", String(STATE.summary.edit_count)],
  ].map(([k, v]) =>
    `<div class="stat"><div class="stat-label">${k}</div><div class="stat-value">${v}</div></div>`
  ).join("");
  $("stats").innerHTML = html;
}

rebuildDocumentTo(STATE.events.length - 1);
renderTopbar();
renderStats();
renderEditor();
STATE.playheadIdx = -1;
rebuildDocumentTo(STATE.playheadIdx);
renderEditor();

const MAX_VISIBLE_GAP_SECONDS = 5.0;

const VISUAL = (() => {
  const offs = new Array(STATE.events.length);
  if (!STATE.events.length) return { offsets: offs, total: 0 };
  let acc = 0;
  offs[0] = 0;
  let prev = Date.parse(STATE.events[0].timestamp);
  for (let i = 1; i < STATE.events.length; i++) {
    const t = Date.parse(STATE.events[i].timestamp);
    const real = (t - prev) / 1000;
    acc += Math.min(real, MAX_VISIBLE_GAP_SECONDS);
    offs[i] = acc;
    prev = t;
  }
  return { offsets: offs, total: acc };
})();

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

function renderTimelineMarkers() {
  const host = $("timeline-markers");
  const parts = [];

  for (const gap of STATE.idleGaps) {
    const a = visualPctForIdx(gap.after_idx);
    const b = visualPctForIdxAfter(gap.after_idx);
    parts.push(`<div class="seg-idle" style="left:${a}%;width:${Math.max(0.5, b - a)}%"></div>`);
  }

  for (const fi of STATE.focusIntervals) {
    const a = visualPctForIdx(fi.blur_idx);
    const b = visualPctForIdx(fi.focus_idx);
    parts.push(`<div class="seg-unfocused" style="left:${a}%;width:${Math.max(0.5, b - a)}%"></div>`);
  }

  for (const burst of STATE.bursts) {
    const a = visualPctForIdx(burst.start_idx);
    parts.push(`<div class="tick-burst" style="left:${a}%"></div>`);
  }

  host.innerHTML = parts.join("");
}

function renderPlayhead() {
  const idx = Math.max(0, STATE.playheadIdx);
  $("playhead").style.left = visualPctForIdx(idx) + "%";
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

function formatDuration(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function renderTimeReadout() {
  const ev = STATE.playheadIdx >= 0 ? STATE.events[STATE.playheadIdx] : null;
  $("now-time").textContent = ev ? formatAbsoluteTime(ev.timestamp) : formatAbsoluteTime(STATE.meta.start_time);
  $("total-time").textContent = " / " + formatAbsoluteTime(STATE.meta.end_time);
}

renderTimelineMarkers();
renderPlayhead();
renderTimeReadout();

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

function setPlaying(p) {
  STATE.playing = p;
  $("play-btn").textContent = p ? "⏸" : "▶";
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

(() => {
  const track = $("timeline-track");
  let dragging = false;
  track.addEventListener("mousedown", (e) => {
    dragging = true;
    if (STATE.playing) setPlaying(false);
    scrubToClientX(e.clientX);
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    scrubToClientX(e.clientX);
  });
  window.addEventListener("mouseup", () => { dragging = false; });
})();

let _activeBurst = null;
let _flashTimer = null;

function maybeFlashBurst() {
  for (const burst of STATE.bursts) {
    if (STATE.playheadIdx >= burst.start_idx && STATE.playheadIdx <= burst.end_idx) {
      if (_activeBurst === burst) return;
      _activeBurst = burst;
      const codeEl = $("editor-code");
      codeEl.classList.add("burst-flash");
      if (_flashTimer) clearTimeout(_flashTimer);
      _flashTimer = setTimeout(() => {
        codeEl.classList.remove("burst-flash");
      }, 600);
      return;
    }
  }
  _activeBurst = null;
}

function updateUnfocusedOverlay() {
  const overlay = $("unfocused-overlay");
  const elapsedEl = $("unfocused-elapsed");

  const idx = STATE.playheadIdx;
  let active = null;
  for (const fi of STATE.focusIntervals) {
    if (idx >= fi.blur_idx && idx < fi.focus_idx) {
      active = fi;
      break;
    }
  }

  if (!active) {
    overlay.hidden = true;
    return;
  }

  overlay.hidden = false;
  const blurT = Date.parse(STATE.events[active.blur_idx].timestamp);
  const nowT = Date.parse(STATE.events[idx].timestamp);
  const elapsedSec = Math.max(0, Math.round((nowT - blurT) / 1000));
  const m = Math.floor(elapsedSec / 60);
  const s = elapsedSec % 60;
  elapsedEl.textContent = `${m}:${String(s).padStart(2, "0")}`;
}
