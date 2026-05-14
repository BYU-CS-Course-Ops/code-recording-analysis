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
  playheadPct: 0,        // continuous visual position 0..100, decoupled from event idx
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
    const oldLen = ev.oldFragment?.length || 0;
    const newLen = ev.newFragment?.length || 0;
    transformLiveEdits(ev.offset, oldLen, newLen);
    STATE.document = applyEdit(STATE.document, ev.offset, ev.oldFragment, ev.newFragment);
    // Fire a single live highlight when we hit a burst's apex idx — the
    // moment when the burst's inserted content has the most chars alive in
    // the document. We pull the range from the precomputed apex map so an
    // IDE template that types/erases/retypes doesn't strobe; the user sees
    // ONE flash covering the canonical fragment region.
    const apex = BURST_APEX_BY_IDX.get(STATE.playheadIdx);
    if (apex) addLiveBurstHighlight(apex.burst.kind, apex.start, apex.end - apex.start);
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
  renderLineGutter();
}

function renderLineGutter() {
  const g = $("line-gutter");
  if (!g) return;
  const doc = STATE.document;
  // Count visual lines (always at least 1).
  let lines = 1;
  for (let i = 0; i < doc.length; i++) if (doc.charCodeAt(i) === 10) lines++;
  let out = "";
  for (let i = 1; i <= lines; i++) out += (i === 1 ? "" : "\n") + i;
  g.textContent = out;
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
  $("sum-edits").textContent       = STATE.summary.edit_count;
  $("sum-ide-actions").textContent = STATE.summary.ide_action_count ?? 0;
  $("sum-approved-pastes").textContent   = STATE.summary.approved_paste_count ?? 0;
  $("sum-unapproved-pastes").textContent = STATE.summary.unapproved_paste_count ?? 0;
  $("sum-unfocused").textContent   = fmtDuration(STATE.summary.unfocused_seconds);
  $("sum-idle").textContent        = String(STATE.idleGaps.length);
}

/* ---------- kind helpers ---------- */

// Burst kinds use spaces ("approved paste", "unapproved paste") as the data
// label, plus the legacy underscore form ("ide_action"). Normalize both to
// hyphenated CSS class names: "approved paste" → "approved-paste".
function kindClass(k) { return (k || "").replace(/[_\s]+/g, "-"); }
function kindLabel(k) {
  if (k === "ide_action")        return "IDE action";
  if (k === "approved paste")    return "Approved paste";
  if (k === "unapproved paste")  return "Unapproved paste";
  return "Event";
}
function kindIcon(k) {
  if (k === "ide_action")        return "A";
  if (k === "approved paste")    return "✓";
  if (k === "unapproved paste")  return "!";
  return "!";
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
          <span class="stat-label">IDE actions</span>
          <span class="stat-value">${STATE.summary.ide_action_count ?? 0}</span>
        </div>
        <div class="stat">
          <span class="stat-label">Approved pastes</span>
          <span class="stat-value">${STATE.summary.approved_paste_count ?? 0}</span>
        </div>
        <div class="stat">
          <span class="stat-label">Unapproved pastes</span>
          <span class="stat-value">${STATE.summary.unapproved_paste_count ?? 0}</span>
        </div>
      </div>
    </div>
    <div class="stats-section">
      <div class="stats-section-title">Key moments <span style="color:var(--fg-subtle);font-weight:500;letter-spacing:0">${STATE.bursts.length + STATE.focusIntervals.length}</span></div>
      <div class="flags" id="flags-list"></div>
    </div>
  `;
  $("stats").innerHTML = stats;
  renderFlagsList();
}

function renderFlagsList() {
  const host = $("flags-list");
  if (!host) return;

  // Bucket events by kind so we can render collapsible groups.
  const groups = {
    ide_action:         { kind: "ide_action",        cssKind: "ide-action",       label: "IDE actions",        icon: "A", items: [] },
    "approved paste":   { kind: "approved paste",   cssKind: "approved-paste",   label: "Approved pastes",   icon: "✓", items: [] },
    "unapproved paste": { kind: "unapproved paste", cssKind: "unapproved-paste", label: "Unapproved pastes", icon: "!", items: [] },
    unfocused:          { kind: "unfocused",         cssKind: "unfocused",        label: "Unfocused",          icon: "↗", items: [] },
  };

  const counters = { ide_action: 0, "approved paste": 0, "unapproved paste": 0 };
  STATE.bursts.forEach((b) => {
    const startT = STATE.events[b.start_idx]?.timestamp;
    const dur = b.end_idx > b.start_idx
      ? (Date.parse(STATE.events[b.end_idx].timestamp) - Date.parse(STATE.events[b.start_idx].timestamp)) / 1000
      : 0;
    const k = b.kind || "unapproved paste";
    counters[k] = (counters[k] || 0) + 1;
    const elapsed = (Date.parse(startT || STATE.meta.start_time) - Date.parse(STATE.meta.start_time)) / 1000;
    // char_count comes from the session summary — it's the canonical size of
    // the burst's inserted fragment, NOT the number of edit events. Showing
    // "5 edits" is misleading for IDE actions that type/delete/retype.
    // Different summary builds use different key names (char_count vs chars,
    // and may omit fragment), so fall back through several sources before
    // computing from the precomputed apex range as a last resort.
    let chars = b.char_count ?? b.chars;
    if (chars == null && b.fragment) chars = b.fragment.length;
    if (chars == null) {
      const apex = BURST_APEX_BY_IDX.get(b.end_idx)
                || [...BURST_APEX_BY_IDX.values()].find((a) => a.burst === b);
      if (apex) chars = apex.end - apex.start;
    }
    if (chars == null) chars = (b.end_idx - b.start_idx + 1);
    (groups[k] || groups["unapproved paste"]).items.push({
      title: `${kindLabel(k)} #${counters[k]}`,
      meta: `+${fmtDuration(elapsed)} · ${chars} char${chars === 1 ? "" : "s"}`,
      idx: b.end_idx,
      burstIdx: STATE.bursts.indexOf(b),
    });
  });

  STATE.focusIntervals.forEach((fi, i) => {
    const startT = STATE.events[fi.blur_idx]?.timestamp;
    const dur = fi.focus_idx > fi.blur_idx
      ? (Date.parse(STATE.events[fi.focus_idx].timestamp) - Date.parse(STATE.events[fi.blur_idx].timestamp)) / 1000
      : 0;
    const elapsed = (Date.parse(startT || STATE.meta.start_time) - Date.parse(STATE.meta.start_time)) / 1000;
    groups.unfocused.items.push({
      title: `Unfocused #${i + 1}`,
      meta: `+${fmtDuration(elapsed)} · ${fmtDuration(dur)} away`,
      idx: fi.blur_idx,
    });
  });

  const total = Object.values(groups).reduce((n, g) => n + g.items.length, 0);
  if (total === 0) {
    host.innerHTML = `<div class="flag-empty">No anomalies detected — looks like a clean session.</div>`;
    return;
  }

  // Auto-open small groups; collapse big ones to keep the sidebar tidy.
  const renderGroup = (g) => {
    if (!g.items.length) return "";
    const open = g.items.length <= 4 ? " open" : "";
    const flags = g.items.map((it) => `
      <button class="flag ${g.cssKind}" data-idx="${it.idx}"${it.burstIdx != null ? ` data-burst="${it.burstIdx}"` : ""}>
        <span class="flag-icon">${g.icon}</span>
        <span class="flag-body">
          <span class="flag-title">${it.title}</span>
          <span class="flag-meta">${it.meta}</span>
        </span>
      </button>`).join("");
    return `
      <details class="flag-group ${g.cssKind}"${open}>
        <summary>
          <span class="fg-chev" aria-hidden="true">▸</span>
          <span class="fg-label">${g.label}</span>
          <span class="fg-count">${g.items.length}</span>
        </summary>
        <div class="flag-group-body">${flags}</div>
      </details>`;
  };

  host.innerHTML = [groups.ide_action, groups["approved paste"], groups["unapproved paste"], groups.unfocused].map(renderGroup).join("");

  host.querySelectorAll(".flag").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.getAttribute("data-idx"), 10);
      const bAttr = btn.getAttribute("data-burst");
      if (bAttr != null) {
        const burst = STATE.bursts[parseInt(bAttr, 10)];
        if (burst) {
          // Scrub to the apex of the burst (where the most inserted chars
          // are alive at once) so placeholder text like `pass` is visible
          // before the burst's own cleanup events erase it.
          const apex = computeBurstApex(burst);
          scrubToIdx(apex ? apex.apexIdx : idx);
          highlightBurstFragment(burst, apex);
          return;
        }
      }
      scrubToIdx(idx);
    });
  });
}

/* ---------- fragment highlight (click-triggered, auto-clears on next scrub) ---------- */

function clearFragmentHighlight() {
  if (!window.CSS || !CSS.highlights) return;
  CSS.highlights.delete("burst-fragment-ide");
  CSS.highlights.delete("burst-fragment-approved-paste");
  CSS.highlights.delete("burst-fragment-unapproved-paste");
}

/* ---------- live edit highlights (playback-driven, fade after ~1s) ----------

   As the playhead crosses each edit event we push a {kind, start, end,
   createdAt} entry onto liveEdits[]. A RAF loop paints colored rectangles
   over the corresponding text range (via Range.getClientRects) into a sibling
   layer inside the <pre>, and fades each rect out over FADE_MS. Entries
   surviving subsequent edits get their offsets shifted; entries whose text
   was overwritten get dropped. */

const liveEdits = [];
const LIVE_HOLD_MS = 180;     // full opacity for first ~180ms (a noticeable pop)
const LIVE_FADE_MS = 900;     // then fade out over ~900ms

function kindForEventIdx(idx) {
  for (const b of STATE.bursts) {
    if (idx >= b.start_idx && idx <= b.end_idx) return b.kind || "unapproved paste";
  }
  return "edit";
}

function transformLiveEdits(offset, oldLen, newLen) {
  const editEnd = offset + oldLen;
  const delta = newLen - oldLen;
  for (let i = liveEdits.length - 1; i >= 0; i--) {
    const e = liveEdits[i];
    if (e.end <= offset) continue;             // entirely before — unaffected
    if (e.start >= editEnd) {                  // entirely after — shift both
      e.start += delta;
      e.end += delta;
      continue;
    }
    // Edit lands fully inside the highlight — grow/shrink rather than drop.
    // This is the common case when the IDE finishes a template by re-typing
    // the placeholder "pass" inside the just-highlighted def.
    if (e.start <= offset && editEnd <= e.end) {
      e.end += delta;
      if (e.end <= e.start) liveEdits.splice(i, 1);
      continue;
    }
    // Other partial overlaps — too messy to map cleanly. Drop.
    liveEdits.splice(i, 1);
  }
}

function addLiveBurstHighlight(kind, offset, length) {
  if (length <= 0) return;
  liveEdits.push({
    kind,
    start: offset,
    end: offset + length,
    createdAt: performance.now(),
  });
  ensurePaintLoop();
}

function addLiveEdit(idx, offset, length) {
  // Retained for back-compat (no current caller). Plain character edits no
  // longer flash — only burst end_idx events trigger a highlight, via
  // addLiveBurstHighlight() above.
  if (length <= 0) return;
  const kind = kindForEventIdx(idx);
  if (kind !== "approved paste" && kind !== "unapproved paste" && kind !== "ide_action") return;
  liveEdits.push({
    kind,
    start: offset,
    end: offset + length,
    createdAt: performance.now(),
  });
  ensurePaintLoop();
}

function clearLiveEdits() {
  liveEdits.length = 0;
  const layer = $("edit-highlight-layer");
  if (layer) layer.textContent = "";
}

let _paintRaf = 0;
function ensurePaintLoop() {
  if (_paintRaf) return;
  const loop = () => {
    paintLiveEdits();
    if (liveEdits.length) {
      _paintRaf = requestAnimationFrame(loop);
    } else {
      _paintRaf = 0;
    }
  };
  _paintRaf = requestAnimationFrame(loop);
}

function paintLiveEdits() {
  const layer = $("edit-highlight-layer");
  if (!layer) return;
  const now = performance.now();

  // Prune expired
  for (let i = liveEdits.length - 1; i >= 0; i--) {
    if (now - liveEdits[i].createdAt > LIVE_HOLD_MS + LIVE_FADE_MS) {
      liveEdits.splice(i, 1);
    }
  }

  layer.textContent = "";
  if (!liveEdits.length) return;

  const editor = $("editor");
  const codeEl = $("editor-code");
  if (!editor || !codeEl) return;
  const editorRect = editor.getBoundingClientRect();
  const scrollLeft = editor.scrollLeft;
  const scrollTop  = editor.scrollTop;
  const docLen = STATE.document.length;

  for (const e of liveEdits) {
    const start = Math.max(0, Math.min(e.start, docLen));
    const end   = Math.max(start, Math.min(e.end, docLen));
    if (end <= start) continue;

    const age = now - e.createdAt;
    const opacity = age <= LIVE_HOLD_MS
      ? 1
      : Math.max(0, 1 - (age - LIVE_HOLD_MS) / LIVE_FADE_MS);

    // Build a DOM Range over [start, end) inside editor-code's text nodes.
    const range = document.createRange();
    let pos = 0, startedAt = false, finished = false;
    const walker = document.createTreeWalker(codeEl, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const len = node.textContent.length;
      if (!startedAt && pos + len > start) {
        range.setStart(node, start - pos);
        startedAt = true;
      }
      if (startedAt && pos + len >= end) {
        range.setEnd(node, end - pos);
        finished = true;
        break;
      }
      pos += len;
    }
    if (!startedAt) continue;
    if (!finished) range.setEndAfter(codeEl.lastChild || codeEl);

    const rects = range.getClientRects();
    // hljs splits the line into multiple inline spans, which makes
    // getClientRects return several rects per visual line. Drawing each one
    // stacks the 18%-alpha tint and produces a darker patch over heavily
    // tokenized substrings (e.g. `__main__`). Merge rects that share a line
    // into a single span so the highlight reads as one flat block.
    const merged = [];
    const byLine = new Map();
    for (let r = 0; r < rects.length; r++) {
      const rect = rects[r];
      if (rect.width <= 0 || rect.height <= 0) continue;
      const key = Math.round(rect.top) + ":" + Math.round(rect.bottom);
      const cur = byLine.get(key);
      if (cur) {
        cur.left = Math.min(cur.left, rect.left);
        cur.right = Math.max(cur.right, rect.right);
      } else {
        const entry = { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
        byLine.set(key, entry);
        merged.push(entry);
      }
    }
    const cls = "eh-rect eh-" + kindClass(e.kind);
    for (const m of merged) {
      const w = m.right - m.left;
      const h = m.bottom - m.top;
      if (w <= 0 || h <= 0) continue;
      const box = document.createElement("div");
      box.className = cls;
      box.style.left   = (m.left - editorRect.left + scrollLeft) + "px";
      box.style.top    = (m.top  - editorRect.top  + scrollTop ) + "px";
      box.style.width  = w + "px";
      box.style.height = h + "px";
      box.style.opacity = String(opacity);
      layer.appendChild(box);
    }
  }
}

// For a burst, find the event idx within [start_idx..end_idx] where the most
// burst-origin chars are simultaneously alive in the document, plus the
// contiguous range of those chars at that moment. This is what we want to
// scrub the playhead to + highlight on click — it shows the FULL inserted
// template (including placeholder text like `pass` that later gets deleted
// in the same burst), not just the chars that survive to end_idx.
function computeBurstApex(burst) {
  const first = STATE.events[burst.start_idx];
  if (!first) return null;

  // Replay everything up to (but not including) start_idx using a snapshot
  // when available so we know the pre-burst document state.
  let doc = "";
  const snap = findSnapshotAtOrBefore(burst.start_idx - 1);
  let cursor;
  if (snap) { doc = snap.document_text; cursor = snap.after_idx; }
  else      { doc = "";                  cursor = -1; }
  for (let i = cursor + 1; i < burst.start_idx; i++) {
    const ev = STATE.events[i];
    if (ev.type === "edit") doc = applyEdit(doc, ev.offset, ev.oldFragment, ev.newFragment);
  }

  let origin = new Array(doc.length).fill(false);
  let bestIdx = burst.start_idx;
  let bestRange = null;
  let bestCount = -1;

  const measure = (idx) => {
    let count = 0;
    let runStart = -1, runEnd = -1, runLen = 0;
    let i = 0;
    while (i < origin.length) {
      if (!origin[i]) { i++; continue; }
      const s = i;
      while (i < origin.length && origin[i]) i++;
      const len = i - s;
      count += len;
      if (len > runLen) { runLen = len; runStart = s; runEnd = i; }
    }
    if (count > bestCount && runLen > 0) {
      bestCount = count;
      bestIdx = idx;
      bestRange = { start: runStart, end: runEnd };
    }
  };

  for (let i = burst.start_idx; i <= burst.end_idx; i++) {
    const ev = STATE.events[i];
    if (ev.type !== "edit") continue;
    const oldLen = ev.oldFragment?.length || 0;
    const newLen = ev.newFragment?.length || 0;
    doc = applyEdit(doc, ev.offset, ev.oldFragment, ev.newFragment);
    const after = new Array(newLen).fill(true);
    origin = [
      ...origin.slice(0, ev.offset),
      ...after,
      ...origin.slice(ev.offset + oldLen),
    ];
    measure(i);
  }

  if (!bestRange) return null;
  return { apexIdx: bestIdx, start: bestRange.start, end: bestRange.end };
}

function computeBurstRange(burst) {
  // For a single-event burst (paste, or a single IDE template insertion) the
  // visible inserted region is exactly the newFragment — using the *net*
  // diff under-highlights when the event also deleted text (e.g. a paste
  // that replaces a selection).
  //
  // For a multi-event IDE burst we walk the events forward and track which
  // chars in the final document came from this burst. The earlier "net" math
  // happened to work when bursts were pure insertions, but breaks as soon as
  // the burst also deletes (very common for PyCharm live templates which
  // type a stub, then erase it, then re-insert the cleaned form).
  const first = STATE.events[burst.start_idx];
  if (!first) return null;

  if (burst.start_idx === burst.end_idx) {
    const ev = first;
    const newLen = ev.newFragment?.length || 0;
    if (newLen <= 0) return null;
    return { start: ev.offset, end: ev.offset + newLen };
  }

  // Simulate the burst on top of the pre-burst document so we know exactly
  // where the burst's surviving text lives at end_idx.
  let doc = "";
  const snap = findSnapshotAtOrBefore(burst.start_idx - 1);
  let cursor;
  if (snap) { doc = snap.document_text; cursor = snap.after_idx; }
  else      { doc = "";                  cursor = -1; }
  for (let i = cursor + 1; i < burst.start_idx; i++) {
    const ev = STATE.events[i];
    if (ev.type === "edit") doc = applyEdit(doc, ev.offset, ev.oldFragment, ev.newFragment);
  }

  // Track origin per char: an array of booleans the same length as doc,
  // true where the char came from this burst.
  let origin = new Array(doc.length).fill(false);
  for (let i = burst.start_idx; i <= burst.end_idx; i++) {
    const ev = STATE.events[i];
    if (ev.type !== "edit") continue;
    const oldLen = ev.oldFragment?.length || 0;
    const newLen = ev.newFragment?.length || 0;
    doc = applyEdit(doc, ev.offset, ev.oldFragment, ev.newFragment);
    const after = new Array(newLen).fill(true);   // freshly inserted by this burst
    origin = [
      ...origin.slice(0, ev.offset),
      ...after,
      ...origin.slice(ev.offset + oldLen),
    ];
  }

  // Find the contiguous run(s) of burst-origin chars and pick the largest.
  let bestStart = -1, bestEnd = -1, bestLen = 0;
  let i = 0;
  while (i < origin.length) {
    if (!origin[i]) { i++; continue; }
    const s = i;
    while (i < origin.length && origin[i]) i++;
    const len = i - s;
    if (len > bestLen) { bestLen = len; bestStart = s; bestEnd = i; }
  }
  if (bestLen <= 0) return null;
  return { start: bestStart, end: bestEnd };
}

function highlightBurstFragment(burst, apex) {
  if (!(window.CSS && CSS.highlights && window.Highlight)) return;
  clearFragmentHighlight();

  const r = apex ? { start: apex.start, end: apex.end } : computeBurstRange(burst);
  if (!r) return;
  const { start, end } = r;
  if (end <= start) return;

  // Defer to the next frame so we run after any pending renderEditor() /
  // hljs.highlightElement() inside scrubToIdx — otherwise the CSS Highlight
  // can be set against text nodes that hljs is about to swap out, and the
  // paint quietly drops.
  requestAnimationFrame(() => {
    const codeEl = $("editor-code");
    const range = new Range();
    let pos = 0, started = false;
    const walker = document.createTreeWalker(codeEl, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const len = node.textContent.length;
      if (!started && pos + len > start) {
        range.setStart(node, start - pos);
        started = true;
      }
      if (started && pos + len >= end) {
        range.setEnd(node, end - pos);
        const name = burst.kind === "ide_action"
          ? "burst-fragment-ide"
          : ("burst-fragment-" + kindClass(burst.kind));
        try {
          CSS.highlights.set(name, new Highlight(range));
        } catch (e) { /* noop */ }

        // Bring the highlighted region into view if it's offscreen. Done
        // separately so a scroll failure never kills the highlight.
        try {
          const rects = range.getClientRects();
          if (rects.length) {
            const editor = $("editor");
            const eb = editor.getBoundingClientRect();
            const top = rects[0].top - eb.top + editor.scrollTop;
            if (top < editor.scrollTop || top > editor.scrollTop + editor.clientHeight - 40) {
              editor.scrollTo({ top: Math.max(0, top - 80), behavior: "smooth" });
            }
          }
        } catch (e) { /* noop */ }
        return;
      }
      pos += len;
    }
  });
}

/* ---------- visual timeline math ---------- */

const IDLE_AFTER_IDX = new Set(STATE.idleGaps.map((g) => g.after_idx));

// Pre-compute, for each burst, the smallest event idx where the burst's
// canonical fragment (from session summary, or the first event's newFragment)
// is fully present in the document. That's where we fire the live highlight
// during playback — not the apex (which may sit inside an erase/retype mid-
// burst sequence and get clobbered by subsequent burst events). For pastes
// this is just the paste event itself. For IDE templates that type a stub,
// delete it, retype the cleaned form, the highlight waits until the cleaned
// form actually appears in the editor.
const BURST_APEX_BY_IDX = (() => {
  const map = new Map();
  const MAX_LOOKAHEAD = 40;
  for (const b of STATE.bursts) {
    const fragment = b.fragment || STATE.events[b.start_idx]?.newFragment;
    if (!fragment || fragment.length === 0) continue;

    // Replay the document up through end_idx so we can search it.
    let doc = "";
    const snap = findSnapshotAtOrBefore(b.end_idx);
    let cursor;
    if (snap) { doc = snap.document_text; cursor = snap.after_idx; }
    else      { doc = ""; cursor = -1; }
    for (let i = cursor + 1; i <= b.end_idx; i++) {
      const ev = STATE.events[i];
      if (ev?.type === "edit") doc = applyEdit(doc, ev.offset, ev.oldFragment, ev.newFragment);
    }

    let fireIdx = -1;
    let pos = doc.indexOf(fragment);
    if (pos >= 0) {
      fireIdx = b.end_idx;
    } else {
      const limit = Math.min(b.end_idx + MAX_LOOKAHEAD, STATE.events.length - 1);
      for (let i = b.end_idx + 1; i <= limit; i++) {
        const ev = STATE.events[i];
        if (ev?.type === "edit") doc = applyEdit(doc, ev.offset, ev.oldFragment, ev.newFragment);
        pos = doc.indexOf(fragment);
        if (pos >= 0) { fireIdx = i; break; }
      }
    }

    if (fireIdx >= 0) {
      map.set(fireIdx, {
        burst: b,
        apexIdx: fireIdx,
        kind: b.kind || "unapproved paste",
        start: pos,
        end: pos + fragment.length,
      });
    } else {
      // Fallback: best-effort apex range. Better SOMETHING than nothing.
      try {
        const apex = computeBurstApex(b);
        if (apex) {
          map.set(apex.apexIdx, {
            burst: b,
            apexIdx: apex.apexIdx,
            kind: b.kind || "unapproved paste",
            start: apex.start,
            end: apex.end,
          });
        }
      } catch (e) { /* swallow */ }
    }
  }
  return map;
})();

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
    parts.push(`<div class="tick-burst ${kindClass(burst.kind)}" style="left:${a}%"></div>`);
  }
  host.innerHTML = parts.join("");
}

function renderPlayhead() {
  const pct = Math.max(0, Math.min(100, STATE.playheadPct));
  $("playhead").style.left = pct + "%";
  $("progress-fill").style.width = pct + "%";
}

function renderTimeReadout() {
  const idx = STATE.playheadIdx;
  const ev = idx >= 0 ? STATE.events[idx] : null;
  let t;
  if (!ev) {
    t = Date.parse(STATE.meta.start_time);
  } else if (idx < STATE.events.length - 1) {
    // Interpolate between this event and the next using the visual fraction
    // so the time readout glides smoothly through long idle / unfocused gaps.
    const cur = VISUAL.offsets[idx];
    const next = VISUAL.offsets[idx + 1];
    const target = (STATE.playheadPct / 100) * VISUAL.total;
    const frac = next > cur ? Math.max(0, Math.min(1, (target - cur) / (next - cur))) : 0;
    const t1 = Date.parse(ev.timestamp);
    const t2 = Date.parse(STATE.events[idx + 1].timestamp);
    t = t1 + (t2 - t1) * frac;
  } else {
    t = Date.parse(ev.timestamp);
  }
  $("now-time").textContent = formatAbsoluteTime(new Date(t).toISOString());
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

  // Advance the smooth visual position by the elapsed budget.
  const curBase = STATE.playheadIdx >= 0 ? VISUAL.offsets[STATE.playheadIdx] : 0;
  let pos = (STATE.playheadPct / 100) * VISUAL.total;
  if (pos < curBase) pos = curBase;
  pos += accumBudget;
  accumBudget = 0;
  if (pos > VISUAL.total) pos = VISUAL.total;

  // Apply any events we crossed.
  while (STATE.playheadIdx < STATE.events.length - 1) {
    const nextIdx = STATE.playheadIdx + 1;
    const next = VISUAL.offsets[nextIdx];
    if (next <= pos) {
      stepForward();
    } else {
      break;
    }
  }
  STATE.playheadPct = VISUAL.total > 0 ? (pos / VISUAL.total) * 100 : 0;

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
  const prev = STATE.playheadIdx;
  STATE.playheadIdx = idx;
  STATE.playheadPct = visualPctForIdx(idx);
  rebuildDocumentTo(idx);
  // Always clear on scrub. If we landed exactly on a burst's apex idx going
  // forward, fire the burst highlight from the precomputed apex map (so an
  // arrow-key step over a paste / IDE action still flashes the right region).
  clearLiveEdits();
  if (idx > prev) {
    const apex = BURST_APEX_BY_IDX.get(idx);
    if (apex) addLiveBurstHighlight(apex.burst.kind, apex.start, apex.end - apex.start);
  }
  renderEditor();
  renderPlayhead();
  renderTimeReadout();
  updateUnfocusedOverlay();
  _activeBurst = null;
  clearFragmentHighlight();
  maybeFlashBurst();
}

// Returns the FLOOR event idx for a visual percentage — the largest idx whose
// offset is <= target. This lets the playhead sit *inside* idle / unfocused
// gaps instead of snapping forward to the gap's end event.
function visualPctToEventIdx(pct) {
  if (VISUAL.total <= 0 || !VISUAL.offsets.length) return -1;
  const target = (pct / 100) * VISUAL.total;
  let lo = 0, hi = VISUAL.offsets.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (VISUAL.offsets[mid] <= target) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

function scrubToClientX(clientX) {
  const track = $("timeline-track");
  const rect = track.getBoundingClientRect();
  const pct = Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100));
  const idx = visualPctToEventIdx(pct);
  STATE.playheadIdx = idx;
  STATE.playheadPct = pct;          // exact pointer position — no snap to event
  rebuildDocumentTo(idx);
  clearLiveEdits();
  renderEditor();
  renderPlayhead();
  renderTimeReadout();
  updateUnfocusedOverlay();
  _activeBurst = null;
  clearFragmentHighlight();
  maybeFlashBurst();
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
    if (idx >= b.start_idx && idx <= b.end_idx) {
      tag = `<span class="tt-tag ${kindClass(b.kind)}">${kindLabel(b.kind)}</span>`;
      break;
    }
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

const BURST_KIND_CLASSES = ["ide-action", "approved-paste", "unapproved-paste"];

function setBannerKind(banner, kind) {
  banner.classList.remove(...BURST_KIND_CLASSES);
  banner.classList.add(kindClass(kind));
  const txt = $("burst-banner-text");
  if (txt) txt.textContent = kindLabel(kind);
}

function setWrapFlashKind(wrap, kind) {
  wrap.classList.remove(...BURST_KIND_CLASSES);
  wrap.classList.remove("burst-flash");
  // restart animation
  void wrap.offsetWidth;
  wrap.classList.add("burst-flash", kindClass(kind));
}

function maybeFlashBurst() {
  for (const burst of STATE.bursts) {
    if (STATE.playheadIdx >= burst.start_idx && STATE.playheadIdx <= burst.end_idx) {
      const banner = $("burst-banner");
      if (_activeBurst === burst) {
        banner.classList.add("show");
        return;
      }
      _activeBurst = burst;
      const wrap = $("editor-wrap");
      setWrapFlashKind(wrap, burst.kind);
      setBannerKind(banner, burst.kind);
      banner.classList.add("show");
      if (_flashTimer) clearTimeout(_flashTimer);
      _flashTimer = setTimeout(() => {
        wrap.classList.remove("burst-flash", ...BURST_KIND_CLASSES);
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

  // Interpolate the current playhead time the same way renderTimeReadout()
  // does, so this elapsed counter advances smoothly through the unfocused
  // gap (no edits fire while unfocused, so without interpolation the readout
  // would freeze at the blur event's timestamp = 0:00).
  let nowT;
  const ev = STATE.events[Math.max(0, idx)];
  if (idx >= 0 && idx < STATE.events.length - 1) {
    const cur = VISUAL.offsets[idx];
    const next = VISUAL.offsets[idx + 1];
    const target = (STATE.playheadPct / 100) * VISUAL.total;
    const frac = next > cur ? Math.max(0, Math.min(1, (target - cur) / (next - cur))) : 0;
    const t1 = Date.parse(ev.timestamp);
    const t2 = Date.parse(STATE.events[idx + 1].timestamp);
    nowT = t1 + (t2 - t1) * frac;
  } else {
    nowT = Date.parse(ev.timestamp);
  }

  const elapsedSec = Math.max(0, Math.floor((nowT - blurT) / 1000));
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
STATE.playheadPct = 0;
rebuildDocumentTo(STATE.playheadIdx);
renderEditor();

renderTimelineMarkers();
renderPlayhead();
renderTimeReadout();

/* ---------- event listeners ---------- */

$("play-btn").addEventListener("click", () => {
  if (STATE.playheadIdx >= STATE.events.length - 1) {
    STATE.playheadIdx = -1;
    STATE.playheadPct = 0;
    rebuildDocumentTo(-1);
    clearLiveEdits();
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
  let pendingX = 0, rafScrub = 0;
  const flush = () => { rafScrub = 0; scrubToClientX(pendingX); };

  track.addEventListener("pointerdown", (e) => {
    dragging = true;
    track.setPointerCapture(e.pointerId);
    if (STATE.playing) setPlaying(false);
    pendingX = e.clientX;
    scrubToClientX(e.clientX);
  });
  track.addEventListener("pointermove", (e) => {
    if (dragging) {
      pendingX = e.clientX;
      if (!rafScrub) rafScrub = requestAnimationFrame(flush);
    }
    timelineHover(e.clientX);
  });
  const endDrag = (e) => {
    dragging = false;
    if (rafScrub) { cancelAnimationFrame(rafScrub); rafScrub = 0; flush(); }
    try { track.releasePointerCapture(e.pointerId); } catch {}
  };
  track.addEventListener("pointerup", endDrag);
  track.addEventListener("pointercancel", endDrag);

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
        STATE.playheadPct = 0;
        rebuildDocumentTo(-1);
        clearLiveEdits();
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
