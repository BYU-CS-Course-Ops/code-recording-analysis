"use strict";

/* =========================================================================
   Code Playback — multi-session player runtime
   -------------------------------------------------------------------------
   Accepts three input shapes via window.__BUNDLE__:
     1. Legacy single-session bundle: { metadata, summary, events, bursts,
        idle_gaps, focus_intervals, snapshots }
     2. New list-of-sessions: [ { document, language, start_time, end_time,
        total_*, events, bursts, idle_gaps, focus_intervals, snapshots,
        timeline }, ... ]
     3. Wrapper: { sessions: [...] }   (same per-session shape as #2)

   The viewer renders each session as an IDE-style tab. Playback is a single
   continuous timeline across all sessions, sorted by absolute timestamp. When
   the playhead crosses into a different session, the active tab auto-
   switches and briefly flashes. Lulls between sessions are shown as
   inter-session idle gaps (compressible via "Skip idle").
   ========================================================================= */

const BUNDLE = window.__BUNDLE__;
const $ = (id) => document.getElementById(id);

/* =========================================================================
   Input normalization
   ========================================================================= */

function isSessionLike(o) {
  return o && typeof o === "object" && Array.isArray(o.events) && (
    Object.prototype.hasOwnProperty.call(o, "document") ||
    Object.prototype.hasOwnProperty.call(o, "language") ||
    Object.prototype.hasOwnProperty.call(o, "start_time")
  );
}

function normalizeBundleToSessions(bundle) {
  // Already a list of sessions
  if (Array.isArray(bundle)) return bundle.map(normalizeSession);
  // {sessions: [...]} wrapper
  if (bundle && Array.isArray(bundle.sessions)) return bundle.sessions.map(normalizeSession);
  // Legacy single-session (metadata + summary + events)
  if (bundle && bundle.metadata) return [normalizeLegacy(bundle)];
  // Already a single new-shape session
  if (isSessionLike(bundle)) return [normalizeSession(bundle)];
  // Fallback — render an empty player
  return [];
}

function normalizeLegacy(b) {
  const m = b.metadata || {};
  const s = b.summary || {};
  return normalizeSession({
    document: m.document,
    language: m.language,
    initial_document: b.initial_document || "",
    starts_with_starter_code: b.starts_with_starter_code,
    start_time: m.start_time,
    end_time: m.end_time,
    total_time: s.total_seconds,
    total_time_unfocused: s.unfocused_seconds,
    total_edits: s.edit_count,
    total_unapproved_pastes: s.unapproved_paste_count,
    total_approved_pastes: s.approved_paste_count,
    total_internal_pastes: s.internal_paste_count,
    total_ide_actions: s.ide_action_count,
    events: b.events || [],
    bursts: b.bursts || [],
    idle_gaps: b.idle_gaps || [],
    focus_intervals: b.focus_intervals || [],
    snapshots: b.snapshots || [],
  });
}

function normalizeSession(s) {
  const session = {
    document:               s.document               || "(unnamed)",
    language:               s.language               || "plaintext",
    initial_document:       s.initial_document       || "",
    // Preserve the starter-code tri-state: true, false, and unknown (null).
    starts_with_starter_code: s.starts_with_starter_code ?? null,
    start_time:             s.start_time,
    end_time:               s.end_time,
    total_time:             s.total_time             ?? 0,
    total_time_unfocused:   s.total_time_unfocused   ?? 0,
    total_edits:            s.total_edits            ?? (s.events || []).filter(e => e.type === "edit").length,
    total_unapproved_pastes:s.total_unapproved_pastes?? 0,
    total_approved_pastes:  s.total_approved_pastes  ?? 0,
    total_internal_pastes:  s.total_internal_pastes  ?? 0,
    total_ide_actions:      s.total_ide_actions      ?? 0,
    events:                 s.events                 || [],
    bursts:                 (s.bursts                || []).map(b => ({ ...b })),  // defensive copy — we mutate end_idx below
    idle_gaps:              s.idle_gaps              || [],
    focus_intervals:        s.focus_intervals        || [],
    snapshots:              s.snapshots              || [],
    // Live per-session reconstructed document, seeded from the recorder snapshot.
    doc: s.initial_document || "",
    docCursor: -1,    // largest local idx whose edit has been applied to `doc`
  };
  growBurstsByTimeProximity(session);
  return session;
}

/* Grow each burst's end_idx forward to absorb trailing rapid-fire events
   that belong to the same paste / IDE template but were excluded by the
   upstream Session analyzer.

   Why this exists: the newer recan Session class collapses each burst onto
   its largest contiguous fragment, which is great for naming the burst
   ("the text"). But the IDE template that fires the burst usually emits
   *additional* micro-edits in the same millisecond window — inserting
   surrounding "\n", spacing the def into its own block, retyping a cleaned
   form — and those events end up sitting outside the burst's start/end
   range. The visible result in the player is the live highlight firing
   when the inserted fragment is still smashed against the surrounding code
   (no spacing yet), then the spacing arrives a beat later.

   The fix: walk forward from each burst's end_idx and absorb any next
   event whose gap from the previous event is under `gapThresholdMs`. Human
   keystrokes are well above this floor; IDE templates fire 1-10ms apart.

   This is a client-side compensation. The proper fix lives in Session:
   when emitting a burst, set end_idx (and end_timestamp) to the LAST event
   in the rapid-fire group, not the event with the largest fragment. */
const GROW_BURST_GAP_MS = 250;

function growBurstsByTimeProximity(session) {
  const events = session.events;
  if (!events.length || !session.bursts.length) return;

  const sorted = session.bursts.slice().sort((a, b) => a.start_idx - b.start_idx);
  for (let i = 0; i < sorted.length; i++) {
    const b = sorted[i];
    const next = sorted[i + 1];
    let end = b.end_idx;
    while (end + 1 < events.length) {
      // Don't reach into the next burst's owned events.
      if (next && end + 1 >= next.start_idx) break;
      const cur = events[end];
      const nxt = events[end + 1];
      if (!cur?.timestamp || !nxt?.timestamp) break;
      const dt = Date.parse(nxt.timestamp) - Date.parse(cur.timestamp);
      if (dt <= GROW_BURST_GAP_MS) end++;
      else break;
    }
    b.end_idx = end;
  }
}

/* =========================================================================
   Build global, time-sorted event stream + cross-references
   ========================================================================= */

function buildGlobalState(rawSessions) {
  // Sort by start_time ascending so tab order matches chronological order.
  const sessions = rawSessions.slice().sort((a, b) => {
    const ta = Date.parse(a.start_time || a.events?.[0]?.timestamp || 0);
    const tb = Date.parse(b.start_time || b.events?.[0]?.timestamp || 0);
    return ta - tb;
  });

  // Flatten every session's events into a single global stream, tagged with
  // which session they came from and their local index.
  const globalEvents = [];
  for (let si = 0; si < sessions.length; si++) {
    const s = sessions[si];
    s.events.forEach((ev, li) => {
      globalEvents.push({
        sessionIdx: si,
        localIdx: li,
        timestamp: ev.timestamp,
        ts: Date.parse(ev.timestamp),
        type: ev.type,
        documentText: ev.document_text,
        offset: ev.offset,
        oldFragment: ev.oldFragment,
        newFragment: ev.newFragment,
      });
    });
  }
  // Stable sort by absolute timestamp. Tie-breaker: earlier session, then
  // earlier local idx, so a session's own events stay in their original
  // order even when two events share the exact same timestamp.
  globalEvents.sort((a, b) =>
    (a.ts - b.ts) ||
    (a.sessionIdx - b.sessionIdx) ||
    (a.localIdx - b.localIdx)
  );

  // Build localToGlobal[sessionIdx][localIdx] → globalIdx so we can map each
  // session's burst/focus/idle records (which use LOCAL indices) onto the
  // global timeline.
  const localToGlobal = sessions.map(s => new Array(s.events.length).fill(-1));
  for (let gi = 0; gi < globalEvents.length; gi++) {
    const e = globalEvents[gi];
    localToGlobal[e.sessionIdx][e.localIdx] = gi;
  }

  // Bursts — translate local idx → global idx, keep sessionIdx for document
  // reconstruction (since offsets reference the session's text).
  const bursts = [];
  sessions.forEach((s, si) => {
    (s.bursts || []).forEach((b, bi) => {
      const gStart = localToGlobal[si][b.start_idx];
      const gEnd   = localToGlobal[si][b.end_idx];
      if (gStart < 0 || gEnd < 0) return;
      bursts.push({
        kind: b.kind || "unapproved paste",
        sessionIdx: si,
        localStart: b.start_idx,
        localEnd: b.end_idx,
        start_idx: gStart,           // keep old name for compatibility w/ existing scrubbing logic
        end_idx: gEnd,
        line_count: b.line_count,
        char_count: b.char_count,
        fragment: b.fragment,
        timestamp: b.timestamp,
        _ref: { sessionIdx: si, burstIdx: bi },
      });
    });
  });
  bursts.sort((a, b) => a.start_idx - b.start_idx);

  // Focus intervals — same idea.
  const focusIntervals = [];
  sessions.forEach((s, si) => {
    (s.focus_intervals || []).forEach(fi => {
      const gBlur  = localToGlobal[si][fi.blur_idx];
      const gFocus = localToGlobal[si][fi.focus_idx];
      if (gBlur < 0 || gFocus < 0) return;
      focusIntervals.push({
        sessionIdx: si,
        blur_idx: gBlur,
        focus_idx: gFocus,
      });
    });
  });
  focusIntervals.sort((a, b) => a.blur_idx - b.blur_idx);

  // Idle gaps — translate recorder-reported pauses. Inter-session lulls are
  // kept only as document boundaries, not emitted as idle events.
  const idleGaps = [];
  sessions.forEach((s, si) => {
    (s.idle_gaps || []).forEach(g => {
      const gIdx = localToGlobal[si][g.after_idx];
      if (!Number.isInteger(gIdx) || gIdx < 0) return;
      idleGaps.push({
        sessionIdx: si,
        after_idx: gIdx,
        duration: g.duration,
        kind: "idle",
      });
    });
  });

  // Inter-session lulls: walk globalEvents and mark document boundaries.
  // No synthetic idle event is emitted for the time between documents.
  const sessionBoundaries = [];
  let lastBoundaryFrom = -1;
  for (let gi = 1; gi < globalEvents.length; gi++) {
    const prev = globalEvents[gi - 1];
    const cur = globalEvents[gi];
    if (prev.sessionIdx !== cur.sessionIdx) {
      const dur = Math.max(0, (cur.ts - prev.ts) / 1000);
      sessionBoundaries.push({
        globalIdx: gi,
        fromIdx: prev.sessionIdx,
        toIdx: cur.sessionIdx,
        gapSeconds: dur,
      });
      lastBoundaryFrom = prev.sessionIdx;
    }
  }
  idleGaps.sort((a, b) => a.after_idx - b.after_idx);

  // Aggregate summary.
  const summary = sessions.reduce((acc, s) => {
    acc.edit_count             += s.total_edits;
    acc.ide_action_count       += s.total_ide_actions;
    acc.approved_paste_count   += s.total_approved_pastes;
    acc.internal_paste_count   += s.total_internal_pastes;
    acc.unapproved_paste_count += s.total_unapproved_pastes;
    acc.unfocused_seconds      += s.total_time_unfocused;
    acc.total_seconds          += s.total_time;
    return acc;
  }, {
    edit_count: 0, ide_action_count: 0, approved_paste_count: 0,
    internal_paste_count: 0, unapproved_paste_count: 0,
    unfocused_seconds: 0, total_seconds: 0,
  });

  // Overall meta includes initial snapshots that predate the first real edit.
  const sessionStarts = sessions.map(s => Date.parse(s.start_time)).filter(Number.isFinite);
  const sessionEnds = sessions.map(s => Date.parse(s.end_time)).filter(Number.isFinite);
  const meta = {
    start_time: sessionStarts.length
      ? new Date(Math.min(...sessionStarts)).toISOString()
      : globalEvents[0]?.timestamp,
    end_time: sessionEnds.length
      ? new Date(Math.max(...sessionEnds)).toISOString()
      : globalEvents[globalEvents.length - 1]?.timestamp,
    documents:  sessions.map(s => s.document),
  };

  return { sessions, globalEvents, localToGlobal, bursts, focusIntervals, idleGaps, sessionBoundaries, summary, meta };
}

/* =========================================================================
   STATE
   ========================================================================= */

const _normalizedSessions = normalizeBundleToSessions(BUNDLE);
const _g = buildGlobalState(_normalizedSessions);

const STATE = {
  sessions:        _g.sessions,
  globalEvents:    _g.globalEvents,
  localToGlobal:   _g.localToGlobal,
  bursts:          _g.bursts,
  focusIntervals:  _g.focusIntervals,
  idleGaps:        _g.idleGaps,
  sessionBoundaries: _g.sessionBoundaries,
  summary:         _g.summary,
  meta:            _g.meta,

  // Compatibility shims for code that still says STATE.events / STATE.document.
  // events: the global timeline. document: the active session's rendered text.
  get events() { return this.globalEvents; },
  get document() { return this.sessions[this.activeIdx]?.doc || ""; },

  activeIdx: 0,
  playheadIdx: -1,
  playheadPct: 0,
  playing: false,
  speed: 1,
};

// Convenience accessor for "the burst that contains global event idx" so we
// can light up the right tab/banner.
function burstAtGlobalIdx(idx) {
  for (const b of STATE.bursts) {
    if (idx >= b.start_idx && idx <= b.end_idx) return b;
  }
  return null;
}

/* =========================================================================
   Per-session document reconstruction
   ========================================================================= */

function applyEdit(text, offset, oldFragment, newFragment) {
  const end = offset + (oldFragment?.length || 0);
  return text.slice(0, offset) + (newFragment || "") + text.slice(end);
}

function findSnapshotAtOrBefore(session, localTargetIdx) {
  let candidate = null;
  for (const snap of session.snapshots) {
    if (snap.after_idx <= localTargetIdx) candidate = snap;
    else break;
  }
  return candidate;
}

// Rebuild a single session's doc state to the given LOCAL target idx
// (idx === -1 means "before any events of this session").
function rebuildSessionTo(session, localTargetIdx) {
  if (localTargetIdx < 0) { session.doc = session.initial_document; session.docCursor = -1; return; }
  const snap = findSnapshotAtOrBefore(session, localTargetIdx);
  let doc, cursor;
  if (snap) { doc = snap.document_text; cursor = snap.after_idx; }
  else      { doc = session.initial_document; cursor = -1; }
  for (let i = cursor + 1; i <= localTargetIdx; i++) {
    const ev = session.events[i];
    if (ev?.type === "edit") {
      doc = applyEdit(doc, ev.offset, ev.oldFragment, ev.newFragment);
    }
  }
  session.doc = doc;
  session.docCursor = localTargetIdx;
}

// Given a global playhead position, rebuild EVERY session's doc to reflect
// the latest event from that session that has fired so far. Before a
// session's first edit, its initial snapshot is shown.
function rebuildAllSessionsToGlobalIdx(globalIdx) {
  // For each session, find the largest localIdx whose globalIdx <= target.
  // localToGlobal[si] is monotonic-ish (a session's events appear in time
  // order globally; ties handled by sort), so we can walk backward.
  const targets = STATE.sessions.map(() => -1);
  for (let si = 0; si < STATE.sessions.length; si++) {
    const map = STATE.localToGlobal[si];
    // map[li] is the global idx of local event li. Find largest li with
    // map[li] <= globalIdx. Linear scan is fine for our sizes.
    let best = -1;
    for (let li = 0; li < map.length; li++) {
      if (map[li] <= globalIdx) best = li;
      else break;
    }
    targets[si] = best;
  }
  for (let si = 0; si < STATE.sessions.length; si++) {
    rebuildSessionTo(STATE.sessions[si], targets[si]);
  }
}

function stepForward() {
  if (STATE.playheadIdx >= STATE.globalEvents.length - 1) return false;
  STATE.playheadIdx += 1;
  const ev = STATE.globalEvents[STATE.playheadIdx];

  // Auto-switch tab if the upcoming event belongs to a different session.
  if (ev.sessionIdx !== STATE.activeIdx) {
    setActiveSession(ev.sessionIdx, { flash: true, scrub: false });
  }

  const session = STATE.sessions[ev.sessionIdx];
  if (ev.type === "edit") {
    const oldLen = ev.oldFragment?.length || 0;
    const newLen = ev.newFragment?.length || 0;
    session.doc = applyEdit(session.doc, ev.offset, ev.oldFragment, ev.newFragment);
    session.docCursor = ev.localIdx;

    const apex = BURST_APEX_BY_IDX.get(STATE.playheadIdx);
    if (apex) addLiveBurstHighlight(apex.burst.kind, apex.start, apex.end - apex.start);
  }
  return true;
}

/* =========================================================================
   Formatting helpers
   ========================================================================= */

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
  if (isNaN(d.getTime())) return "—";
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

// Use at HTML interpolation sites, for both text and quoted attributes.
// Keep the original names intact for textContent, paths, and reconstruction.
function escapeHtml(value) {
  const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  return String(value).replace(/[&<>"']/g, ch => entities[ch]);
}

function langTabDotClass(lang) {
  const k = (lang || "").toLowerCase();
  if (k === "py") return "lang-python";
  return "lang-" + k;
}

/* =========================================================================
   Language resolution
   -------------------------------------------------------------------------
   The bundle carries a `language` per session, derived upstream from the
   document's file extension. Recordings whose document name has NO extension
   (e.g. "analyze_logs") fall through to "plaintext", which kills syntax
   highlighting even when the contents are obviously code.

   Here we recover gracefully: if the declared language is missing, "plaintext",
   or not a grammar highlight.js actually has, we auto-detect once from the
   richest text sample we have and cache the result on the session. Detection
   runs a single time per session (not every playback frame).
   ========================================================================= */

// Languages we let auto-detect choose from — the grammars bundled in
// highlight.min.js. Constraining the set makes detection faster and avoids
// spurious matches against exotic grammars.
const AUTODETECT_LANGS = [
  "python", "javascript", "typescript", "java", "cpp", "c", "go", "rust",
  "json", "xml", "css", "bash", "markdown",
];

function bestSampleText(session) {
  // Snapshots hold full document text at various points; the longest is the
  // most representative. Fall back to the live doc if there are no snapshots.
  let best = session.doc || "";
  for (const sn of (session.snapshots || [])) {
    const t = sn && sn.document_text;
    if (t && t.length > best.length) best = t;
  }
  return best;
}

function resolveSessionLanguage(session) {
  if (session._resolvedLang) return session._resolvedLang;

  let lang = session.language || "plaintext";
  const hl = window.hljs;
  const known = hl && hl.getLanguage && hl.getLanguage(lang);

  if (hl && hl.highlightAuto && (lang === "plaintext" || !known)) {
    const sample = bestSampleText(session);
    if (sample && sample.trim()) {
      try {
        const r = hl.highlightAuto(sample, AUTODETECT_LANGS);
        if (r && r.language && r.relevance > 0) lang = r.language;
      } catch (e) { /* keep declared lang */ }
    }
  }

  session._resolvedLang = lang;
  return lang;
}

/* =========================================================================
   Editor + topbar render
   ========================================================================= */

function renderEditor() {
  const session = STATE.sessions[STATE.activeIdx];
  const codeEl = $("editor-code");
  const text = session?.doc || "";
  const lang = session ? resolveSessionLanguage(session) : "plaintext";

  // Highlight by writing tokenized HTML directly (rather than
  // hljs.highlightElement, which first stomps textContent and tracks a
  // `data-highlighted` guard that we'd have to keep clearing every frame).
  // We always know the language at this point — either from the bundle or
  // auto-detected once in resolveSessionLanguage — so use hljs.highlight.
  if (window.hljs && window.hljs.getLanguage && window.hljs.getLanguage(lang)) {
    try {
      codeEl.innerHTML = window.hljs.highlight(text, { language: lang, ignoreIllegals: true }).value;
    } catch (e) {
      codeEl.textContent = text;
    }
  } else {
    codeEl.textContent = text;
  }
  codeEl.className = "hljs language-" + lang;
  renderLineGutter();
}

function renderLineGutter() {
  const g = $("line-gutter");
  if (!g) return;
  const doc = STATE.document;
  let lines = 1;
  for (let i = 0; i < doc.length; i++) if (doc.charCodeAt(i) === 10) lines++;
  let out = "";
  for (let i = 1; i <= lines; i++) out += (i === 1 ? "" : "\n") + i;
  g.textContent = out;
}

function renderTopbar() {
  const session = STATE.sessions[STATE.activeIdx];
  const full = session?.document || "(unknown)";
  const f = $("filename");
  f.textContent = basename(full);
  f.title = full;
  $("lang-pill").textContent = session ? resolveSessionLanguage(session) : "plaintext";
}

/* =========================================================================
   Tab strip
   ========================================================================= */

function renderTabStrip() {
  const strip = $("tab-strip");
  if (!strip) return;
  strip.innerHTML = "";
  if (STATE.sessions.length <= 1) {
    strip.classList.add("single-session");
    return;
  }
  strip.classList.remove("single-session");

  STATE.sessions.forEach((s, i) => {
    const tab = document.createElement("button");
    tab.className = "tab" + (i === STATE.activeIdx ? " active" : "");
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", String(i === STATE.activeIdx));
    tab.dataset.sessionIdx = String(i);
    tab.title = s.document;

    const dot = document.createElement("span");
    dot.className = "tab-dot " + langTabDotClass(resolveSessionLanguage(s));
    tab.appendChild(dot);

    const name = document.createElement("span");
    name.className = "tab-name";
    name.textContent = basename(s.document);
    tab.appendChild(name);

    // Pip row — small dots for any flagged events in this session, so a
    // reviewer scanning the strip can see "this tab has 3 unapproved pastes"
    // at a glance.
    const pipKinds = [];
    if (s.total_ide_actions > 0)        pipKinds.push("ide-action");
    if (s.total_approved_pastes > 0)    pipKinds.push("approved-paste");
    if (s.total_internal_pastes > 0)    pipKinds.push("internal-paste");
    if (s.total_unapproved_pastes > 0)  pipKinds.push("unapproved-paste");
    if (pipKinds.length) {
      const pips = document.createElement("span");
      pips.className = "tab-pip-row";
      pipKinds.forEach(k => {
        const p = document.createElement("span");
        p.className = "tab-pip " + k;
        pips.appendChild(p);
      });
      tab.appendChild(pips);
    }

    // Tab time — duration of this session's activity. Helps reviewers
    // calibrate which doc had the most engagement.
    const meta = document.createElement("span");
    meta.className = "tab-meta";
    meta.textContent = fmtDuration(s.total_time || 0);
    tab.appendChild(meta);

    tab.addEventListener("click", () => {
      // Clicking a tab jumps the playhead to that session's first event
      // (most useful behavior for review — start of that doc's activity).
      const firstLocalIdx = 0;
      const gIdx = STATE.localToGlobal[i][firstLocalIdx];
      if (gIdx >= 0) {
        scrubToIdx(gIdx);
      } else {
        setActiveSession(i, { flash: false, scrub: false });
        renderEditor();
      }
    });

    strip.appendChild(tab);
  });
}

function setActiveSession(idx, { flash = false, scrub = false } = {}) {
  if (idx === STATE.activeIdx && !flash) return;
  STATE.activeIdx = idx;

  // Update tab DOM in-place (cheaper + preserves scroll position vs full re-
  // render of the strip).
  const tabs = $("tab-strip").querySelectorAll(".tab");
  tabs.forEach((t, i) => {
    const a = i === idx;
    t.classList.toggle("active", a);
    t.setAttribute("aria-selected", String(a));
    if (a && flash) {
      t.classList.remove("switch-flash");
      // Force reflow so the animation restarts cleanly even if the same tab
      // re-activates twice in a row.
      void t.offsetWidth;
      t.classList.add("switch-flash");
      // Bring into view in case the strip has scrolled horizontally.
      try { t.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" }); } catch (e) {}
    }
  });

  // Live edits are per-active-session — clear them when we swap.
  clearLiveEdits();

  renderTopbar();
  renderEditor();
}

/* =========================================================================
   Summary chips + stats sidebar
   ========================================================================= */

function renderSummaryChips() {
  const sum = STATE.summary;
  $("sum-edits").textContent             = sum.edit_count;
  $("sum-ide-actions").textContent       = sum.ide_action_count;
  $("sum-approved-pastes").textContent   = sum.approved_paste_count;
  $("sum-internal-pastes").textContent   = sum.internal_paste_count;
  $("sum-unapproved-pastes").textContent = sum.unapproved_paste_count;
  $("sum-unfocused").textContent         = fmtDuration(sum.unfocused_seconds);
  $("sum-idle").textContent              = String(STATE.idleGaps.filter(g => g.kind === "idle").length);
}

/* ---------- kind helpers ---------- */

function kindClass(k) { return (k || "").replace(/[_\s]+/g, "-"); }
function kindLabel(k) {
  if (k === "ide_action")        return "IDE action";
  if (k === "approved paste")    return "Approved paste";
  if (k === "internal paste")    return "Internal paste";
  if (k === "unapproved paste")  return "Unapproved paste";
  return "Event";
}
function kindIcon(k) {
  if (k === "ide_action")        return "A";
  if (k === "approved paste")    return "✓";
  if (k === "internal paste")    return "↻";
  if (k === "unapproved paste")  return "!";
  return "!";
}

/* ---------- stats sidebar ---------- */

function renderStatsSidebar() {
  const sum = STATE.summary;
  const focused = Math.max(0, sum.total_seconds - sum.unfocused_seconds);
  const pctFocused = sum.total_seconds
    ? Math.round((focused / sum.total_seconds) * 100)
    : 100;

  const startedAt = formatAbsoluteTime(STATE.meta.start_time);
  const endedAt   = formatAbsoluteTime(STATE.meta.end_time);

  const sessionList = STATE.sessions.map((s, i) => {
    const n = basename(s.document);
    const flags = s.total_ide_actions + s.total_approved_pastes
                + s.total_internal_pastes + s.total_unapproved_pastes
                + (s.starts_with_starter_code === false ? 1 : 0);
    return `
      <button class="session-row" data-session="${i}" title="${escapeHtml(s.document)}">
        <span class="session-row-dot tab-dot ${escapeHtml(langTabDotClass(resolveSessionLanguage(s)))}"></span>
        <span class="session-row-name">${escapeHtml(n)}</span>
        <span class="session-row-meta">${fmtDuration(s.total_time || 0)}${flags ? ` · ${flags} flag${flags === 1 ? "" : "s"}` : ""}</span>
      </button>`;
  }).join("");

  const stats = `
    <div class="stats-section">
      <div class="stats-section-title">Overall <span style="color:var(--fg-subtle);font-weight:500;letter-spacing:0">${STATE.sessions.length} session${STATE.sessions.length === 1 ? "" : "s"}</span></div>
      <div class="stat-grid">
        <div class="stat full">
          <span class="stat-label">Total time</span>
          <span class="stat-value">${fmtDuration(sum.total_seconds)}</span>
          <span class="delta">${startedAt} → ${endedAt}</span>
        </div>
        <div class="stat">
          <span class="stat-label">Focused</span>
          <span class="stat-value">${fmtDuration(focused)}</span>
          <span class="delta">${pctFocused}% of total</span>
        </div>
        <div class="stat">
          <span class="stat-label">Unfocused</span>
          <span class="stat-value">${fmtDuration(sum.unfocused_seconds)}</span>
          <span class="delta">${100 - pctFocused}% of total</span>
        </div>
        <div class="stat">
          <span class="stat-label">Edits</span>
          <span class="stat-value">${sum.edit_count}</span>
        </div>
        <div class="stat">
          <span class="stat-label">IDE actions</span>
          <span class="stat-value">${sum.ide_action_count}</span>
        </div>
        <div class="stat">
          <span class="stat-label">Approved pastes</span>
          <span class="stat-value">${sum.approved_paste_count}</span>
        </div>
        <div class="stat">
          <span class="stat-label">Internal pastes</span>
          <span class="stat-value">${sum.internal_paste_count}</span>
        </div>
        <div class="stat">
          <span class="stat-label">Unapproved pastes</span>
          <span class="stat-value">${sum.unapproved_paste_count}</span>
        </div>
      </div>
    </div>
    ${STATE.sessions.length > 1 ? `
    <div class="stats-section">
      <div class="stats-section-title">Documents <span style="color:var(--fg-subtle);font-weight:500;letter-spacing:0">${STATE.sessions.length}</span></div>
      <div class="session-list">${sessionList}</div>
    </div>` : ""}
    <div class="stats-section">
      <div class="stats-section-title">Key moments <span style="color:var(--fg-subtle);font-weight:500;letter-spacing:0">${STATE.bursts.length + STATE.focusIntervals.length + STATE.sessions.filter(s => s.starts_with_starter_code === false).length}</span></div>
      <div class="flags" id="flags-list"></div>
    </div>
  `;
  $("stats").innerHTML = stats;
  renderFlagsList();

  $("stats").querySelectorAll(".session-row").forEach(row => {
    row.addEventListener("click", () => {
      const i = parseInt(row.getAttribute("data-session"), 10);
      const gIdx = STATE.localToGlobal[i][0];
      if (gIdx >= 0) scrubToIdx(gIdx);
    });
  });
}

function renderFlagsList() {
  const host = $("flags-list");
  if (!host) return;

  const groups = {
    ide_action:         { kind: "ide_action",        cssKind: "ide-action",       label: "IDE actions",        icon: "A", items: [] },
    "approved paste":   { kind: "approved paste",    cssKind: "approved-paste",   label: "Approved pastes",   icon: "✓", items: [] },
    "internal paste":   { kind: "internal paste",    cssKind: "internal-paste",   label: "Internal pastes",   icon: "↻", items: [] },
    "unapproved paste": { kind: "unapproved paste",  cssKind: "unapproved-paste", label: "Unapproved pastes", icon: "!", items: [] },
    unfocused:          { kind: "unfocused",         cssKind: "unfocused",        label: "Unfocused",          icon: "↗", items: [] },
    starter_mismatch:   { kind: "starter_mismatch", cssKind: "starter-mismatch", label: "Starter-code mismatch", icon: "!", items: [] },
  };

  const counters = { ide_action: 0, "approved paste": 0, "internal paste": 0, "unapproved paste": 0 };
  STATE.bursts.forEach((b) => {
    const startT = STATE.globalEvents[b.start_idx]?.timestamp;
    const k = b.kind || "unapproved paste";
    counters[k] = (counters[k] || 0) + 1;
    const elapsed = (Date.parse(startT || STATE.meta.start_time) - Date.parse(STATE.meta.start_time)) / 1000;
    let chars = b.char_count;
    if (chars == null && b.fragment) chars = b.fragment.length;
    if (chars == null) {
      const apex = BURST_APEX_BY_IDX.get(b.end_idx)
                || [...BURST_APEX_BY_IDX.values()].find((a) => a.burst === b);
      if (apex) chars = apex.end - apex.start;
    }
    if (chars == null) chars = (b.end_idx - b.start_idx + 1);
    const docName = basename(STATE.sessions[b.sessionIdx].document);
    const docSuffix = STATE.sessions.length > 1 ? ` · ${docName}` : "";
    (groups[k] || groups["unapproved paste"]).items.push({
      title: `${kindLabel(k)} #${counters[k]}`,
      meta: `+${fmtDuration(elapsed)} · ${chars} char${chars === 1 ? "" : "s"}${docSuffix}`,
      idx: b.end_idx,
      burstIdx: STATE.bursts.indexOf(b),
    });
  });

  STATE.sessions.forEach((s, i) => {
    if (s.starts_with_starter_code !== false) return;
    const idx = STATE.localToGlobal[i]?.[0] ?? -1;
    const startT = s.start_time;
    const elapsed = (Date.parse(startT || STATE.meta.start_time) - Date.parse(STATE.meta.start_time)) / 1000;
    const docName = basename(s.document);
    const docSuffix = STATE.sessions.length > 1 ? ` · ${docName}` : "";
    groups.starter_mismatch.items.push({
      title: "Does not start with starter code",
      meta: `+${fmtDuration(elapsed)} · ${s.initial_document.length} initial character${s.initial_document.length === 1 ? "" : "s"}${docSuffix}`,
      idx,
      initialSessionIdx: i,
    });
  });

  STATE.focusIntervals.forEach((fi, i) => {
    const startT = STATE.globalEvents[fi.blur_idx]?.timestamp;
    const dur = fi.focus_idx > fi.blur_idx
      ? (Date.parse(STATE.globalEvents[fi.focus_idx].timestamp) - Date.parse(STATE.globalEvents[fi.blur_idx].timestamp)) / 1000
      : 0;
    const elapsed = (Date.parse(startT || STATE.meta.start_time) - Date.parse(STATE.meta.start_time)) / 1000;
    const docName = basename(STATE.sessions[fi.sessionIdx].document);
    const docSuffix = STATE.sessions.length > 1 ? ` · ${docName}` : "";
    groups.unfocused.items.push({
      title: `Unfocused #${i + 1}`,
      meta: `+${fmtDuration(elapsed)} · ${fmtDuration(dur)} away${docSuffix}`,
      idx: fi.blur_idx,
    });
  });

  const total = Object.values(groups).reduce((n, g) => n + g.items.length, 0);
  if (total === 0) {
    host.innerHTML = `<div class="flag-empty">No anomalies detected — looks like a clean session.</div>`;
    return;
  }

  const renderGroup = (g) => {
    if (!g.items.length) return "";
    const open = g.items.length <= 4 ? " open" : "";
    const flags = g.items.map((it) => `
      <button class="flag ${g.cssKind}" data-idx="${it.idx}"${it.burstIdx != null ? ` data-burst="${it.burstIdx}"` : ""}${it.initialSessionIdx != null ? ` data-initial-session="${it.initialSessionIdx}"` : ""}>
        <span class="flag-icon">${g.icon}</span>
        <span class="flag-body">
          <span class="flag-title">${escapeHtml(it.title)}</span>
          <span class="flag-meta">${escapeHtml(it.meta)}</span>
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

  host.innerHTML = [groups.ide_action, groups.starter_mismatch, groups["approved paste"], groups["internal paste"], groups["unapproved paste"], groups.unfocused].map(renderGroup).join("");

  host.querySelectorAll(".flag").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.getAttribute("data-idx"), 10);
      const initialSessionAttr = btn.getAttribute("data-initial-session");
      if (initialSessionAttr != null) {
        const sessionIdx = parseInt(initialSessionAttr, 10);
        scrubToIdx(idx);
        if (STATE.activeIdx !== sessionIdx) setActiveSession(sessionIdx, { flash: false, scrub: false });
        highlightInitialDocument(STATE.sessions[sessionIdx]);
        return;
      }
      const bAttr = btn.getAttribute("data-burst");
      if (bAttr != null) {
        const burst = STATE.bursts[parseInt(bAttr, 10)];
        if (burst) {
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

/* =========================================================================
   Fragment highlight (click-triggered) — operates against the active
   session's editor-code DOM.
   ========================================================================= */

function clearFragmentHighlight() {
  if (!window.CSS || !CSS.highlights) return;
  CSS.highlights.delete("burst-fragment-ide");
  CSS.highlights.delete("burst-fragment-approved-paste");
  CSS.highlights.delete("burst-fragment-internal-paste");
  CSS.highlights.delete("burst-fragment-unapproved-paste");
}

function highlightInitialDocument(session) {
  if (!(window.CSS && CSS.highlights && window.Highlight)) return;
  clearFragmentHighlight();
  const end = session.initial_document.length;
  if (end <= 0) return;

  requestAnimationFrame(() => {
    const codeEl = $("editor-code");
    if (!codeEl) return;
    const range = new Range();
    let pos = 0, started = false;
    const walker = document.createTreeWalker(codeEl, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const len = node.textContent.length;
      if (!started && pos + len > 0) {
        range.setStart(node, 0);
        started = true;
      }
      if (started && pos + len >= end) {
        range.setEnd(node, end - pos);
        try { CSS.highlights.set("burst-fragment-unapproved-paste", new Highlight(range)); } catch (e) {}
        try {
          const rects = range.getClientRects();
          if (rects.length) {
            const editor = $("editor");
            const eb = editor.getBoundingClientRect();
            const top = rects[0].top - eb.top + editor.scrollTop;
            editor.scrollTo({ top: Math.max(0, top - 80), behavior: "smooth" });
          }
        } catch (e) {}
        return;
      }
      pos += len;
    }
  });
}

/* =========================================================================
   Live edit highlights (playback-driven, fade after ~1s)
   ========================================================================= */

/* =========================================================================
   Live edit highlights (playback-driven).

   These use the same CSS Highlights API as the click-triggered fragment
   highlights so the two visually match (text-background tint + underline
   on the actual characters, not an overlay rect). Because both live and
   click use the same `burst-fragment-*` highlight slots, setting one
   replaces the other — they can't visually overlap.

   A live highlight is set when playback crosses a burst's apex idx, and
   self-clears after LIVE_HOLD_MS so the highlight feels momentary rather
   than pinned.
   ========================================================================= */

const LIVE_HOLD_MS = 1500;
let _liveClearTimer = 0;

function addLiveBurstHighlight(kind, offset, length) {
  if (length <= 0) return;
  if (!(window.CSS && CSS.highlights && window.Highlight)) return;

  // Defer to the next frame so we run after the in-flight renderEditor() /
  // hljs.highlightElement() — otherwise the Range would be set against text
  // nodes the highlighter is about to swap out, and the paint silently drops.
  // This is the same trick highlightBurstFragment() uses.
  requestAnimationFrame(() => {
    const codeEl = $("editor-code");
    if (!codeEl) return;
    const start = offset;
    const end = offset + length;
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
        const name = kind === "ide_action"
          ? "burst-fragment-ide"
          : ("burst-fragment-" + kindClass(kind));
        try { CSS.highlights.set(name, new Highlight(range)); } catch (e) { return; }

        if (_liveClearTimer) clearTimeout(_liveClearTimer);
        _liveClearTimer = setTimeout(() => {
          _liveClearTimer = 0;
          clearFragmentHighlight();
        }, LIVE_HOLD_MS);
        return;
      }
      pos += len;
    }
  });
}

function clearLiveEdits() {
  if (_liveClearTimer) { clearTimeout(_liveClearTimer); _liveClearTimer = 0; }
  clearFragmentHighlight();
}

/* =========================================================================
   Burst apex / range — operates on a burst's owning session document
   ========================================================================= */

function computeBurstApex(burst) {
  const session = STATE.sessions[burst.sessionIdx];
  const first = session.events[burst.localStart];
  if (!first) return null;

  let doc = session.initial_document;
  const snap = findSnapshotAtOrBefore(session, burst.localStart - 1);
  let cursor;
  if (snap) { doc = snap.document_text; cursor = snap.after_idx; }
  else      { doc = session.initial_document; cursor = -1; }
  for (let i = cursor + 1; i < burst.localStart; i++) {
    const ev = session.events[i];
    if (ev.type === "edit") doc = applyEdit(doc, ev.offset, ev.oldFragment, ev.newFragment);
  }

  let origin = new Array(doc.length).fill(false);
  let bestIdx = burst.start_idx; // global
  let bestRange = null;
  let bestCount = -1;

  for (let li = burst.localStart; li <= burst.localEnd; li++) {
    const ev = session.events[li];
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
    // measure
    let count = 0, runStart = -1, runEnd = -1, runLen = 0;
    let i = 0;
    while (i < origin.length) {
      if (!origin[i]) { i++; continue; }
      const s = i;
      while (i < origin.length && origin[i]) i++;
      const len = i - s; count += len;
      if (len > runLen) { runLen = len; runStart = s; runEnd = i; }
    }
    if (count > bestCount && runLen > 0) {
      bestCount = count;
      bestIdx = STATE.localToGlobal[burst.sessionIdx][li];
      bestRange = { start: runStart, end: runEnd };
    }
  }

  if (!bestRange) return null;
  return { apexIdx: bestIdx, start: bestRange.start, end: bestRange.end };
}

function computeBurstRange(burst) {
  const session = STATE.sessions[burst.sessionIdx];
  const first = session.events[burst.localStart];
  if (!first) return null;
  if (burst.localStart === burst.localEnd) {
    const newLen = first.newFragment?.length || 0;
    if (newLen <= 0) return null;
    return { start: first.offset, end: first.offset + newLen };
  }
  let doc = session.initial_document;
  const snap = findSnapshotAtOrBefore(session, burst.localStart - 1);
  let cursor;
  if (snap) { doc = snap.document_text; cursor = snap.after_idx; }
  else      { doc = session.initial_document; cursor = -1; }
  for (let i = cursor + 1; i < burst.localStart; i++) {
    const ev = session.events[i];
    if (ev.type === "edit") doc = applyEdit(doc, ev.offset, ev.oldFragment, ev.newFragment);
  }
  let origin = new Array(doc.length).fill(false);
  for (let li = burst.localStart; li <= burst.localEnd; li++) {
    const ev = session.events[li];
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
  }
  let bestStart = -1, bestEnd = -1, bestLen = 0, i = 0;
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
  requestAnimationFrame(() => {
    const codeEl = $("editor-code");
    const range = new Range();
    let pos = 0, started = false;
    const walker = document.createTreeWalker(codeEl, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const len = node.textContent.length;
      if (!started && pos + len > start) { range.setStart(node, start - pos); started = true; }
      if (started && pos + len >= end) {
        range.setEnd(node, end - pos);

        // Cancel any pending live-highlight auto-clear and wipe any stale
        // highlight a racing live rAF may have set just before us. Both
        // are done HERE inside our rAF (not in the synchronous click
        // handler) so the cancel actually catches the live timer that the
        // live rAF schedules from inside its own rAF.
        if (_liveClearTimer) { clearTimeout(_liveClearTimer); _liveClearTimer = 0; }
        clearFragmentHighlight();

        const name = burst.kind === "ide_action"
          ? "burst-fragment-ide"
          : ("burst-fragment-" + kindClass(burst.kind));
        try { CSS.highlights.set(name, new Highlight(range)); } catch (e) {}

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
        } catch (e) {}
        return;
      }
      pos += len;
    }
  });
}

/* =========================================================================
   Visual timeline math (global)
   ========================================================================= */

const IDLE_AFTER_IDX = new Set(STATE.idleGaps.map((g) => g.after_idx));

// Precompute burst apex map keyed by global event idx where the burst's
// canonical fragment first appears in full.
const BURST_APEX_BY_IDX = (() => {
  const map = new Map();
  const MAX_LOOKAHEAD = 40;
  for (const b of STATE.bursts) {
    const session = STATE.sessions[b.sessionIdx];
    const fragment = b.fragment || session.events[b.localStart]?.newFragment;
    if (!fragment || fragment.length === 0) continue;

    // Replay session document through localEnd.
    let doc = session.initial_document;
    const snap = findSnapshotAtOrBefore(session, b.localEnd);
    let cursor;
    if (snap) { doc = snap.document_text; cursor = snap.after_idx; }
    else      { doc = session.initial_document; cursor = -1; }
    for (let i = cursor + 1; i <= b.localEnd; i++) {
      const ev = session.events[i];
      if (ev?.type === "edit") doc = applyEdit(doc, ev.offset, ev.oldFragment, ev.newFragment);
    }

    let fireGlobalIdx = -1;
    let pos = doc.indexOf(fragment);
    if (pos >= 0) {
      fireGlobalIdx = b.end_idx;
    } else {
      const limit = Math.min(b.localEnd + MAX_LOOKAHEAD, session.events.length - 1);
      for (let i = b.localEnd + 1; i <= limit; i++) {
        const ev = session.events[i];
        if (ev?.type === "edit") doc = applyEdit(doc, ev.offset, ev.oldFragment, ev.newFragment);
        pos = doc.indexOf(fragment);
        if (pos >= 0) { fireGlobalIdx = STATE.localToGlobal[b.sessionIdx][i]; break; }
      }
    }

    if (fireGlobalIdx >= 0) {
      map.set(fireGlobalIdx, {
        burst: b, apexIdx: fireGlobalIdx,
        kind: b.kind || "unapproved paste",
        start: pos, end: pos + fragment.length,
      });
    } else {
      try {
        const apex = computeBurstApex(b);
        if (apex) {
          map.set(apex.apexIdx, {
            burst: b, apexIdx: apex.apexIdx,
            kind: b.kind || "unapproved paste",
            start: apex.start, end: apex.end,
          });
        }
      } catch (e) {}
    }
  }
  return map;
})();

let skipIdle = false;
let VISUAL = computeVisual();

function computeVisual() {
  const evs = STATE.globalEvents;
  const offs = new Array(evs.length);
  if (!evs.length) return { offsets: offs, total: 0 };
  let acc = Math.max(0, (evs[0].ts - Date.parse(STATE.meta.start_time)) / 1000);
  offs[0] = acc;
  let prev = evs[0].ts;
  for (let i = 1; i < evs.length; i++) {
    const t = evs[i].ts;
    const real = (t - prev) / 1000;
    // Idle gaps + inter-session lulls both compress to 0 when skipping idle.
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
    const cls = gap.kind === "session-lull" ? "seg-session-lull" : "seg-idle";
    parts.push(`<div class="${cls}" style="left:${a}%;width:${Math.max(0.4, b - a)}%"></div>`);
  }
  for (const fi of STATE.focusIntervals) {
    const a = visualPctForIdx(fi.blur_idx);
    const b = visualPctForIdx(fi.focus_idx);
    parts.push(`<div class="seg-unfocused" style="left:${a}%;width:${Math.max(0.6, b - a)}%"></div>`);
  }
  for (const burst of STATE.bursts) {
    const a = visualPctForIdx(burst.start_idx);
    parts.push(`<div class="tick-burst ${escapeHtml(kindClass(burst.kind))}" style="left:${a}%"></div>`);
  }
  for (const sb of STATE.sessionBoundaries) {
    const a = visualPctForIdx(sb.globalIdx);
    const fromName = basename(STATE.sessions[sb.fromIdx].document);
    const toName = basename(STATE.sessions[sb.toIdx].document);
    parts.push(`<div class="tick-session" data-from="${sb.fromIdx}" data-to="${sb.toIdx}" title="${escapeHtml(fromName)} → ${escapeHtml(toName)}" style="left:${a}%"></div>`);
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
  const evs = STATE.globalEvents;
  const ev = idx >= 0 ? evs[idx] : null;
  let t;
  if (!ev) {
    const target = (STATE.playheadPct / 100) * VISUAL.total;
    t = Date.parse(STATE.meta.start_time) + target * 1000;
  } else if (idx < evs.length - 1) {
    const cur = VISUAL.offsets[idx];
    const next = VISUAL.offsets[idx + 1];
    const target = (STATE.playheadPct / 100) * VISUAL.total;
    const frac = next > cur ? Math.max(0, Math.min(1, (target - cur) / (next - cur))) : 0;
    const t1 = Date.parse(ev.timestamp);
    const t2 = Date.parse(evs[idx + 1].timestamp);
    t = t1 + (t2 - t1) * frac;
  } else {
    t = Date.parse(ev.timestamp);
  }
  $("now-time").textContent = formatAbsoluteTime(new Date(t).toISOString());
  $("total-time").textContent = " / " + formatAbsoluteTime(STATE.meta.end_time);
}

/* =========================================================================
   Playback loop
   ========================================================================= */

let lastTick = 0;
let rafId = null;
let accumBudget = 0;

function tick(now) {
  if (!STATE.playing) return;
  if (lastTick === 0) lastTick = now;
  const dtMs = now - lastTick;
  lastTick = now;
  accumBudget += (dtMs / 1000) * STATE.speed;

  const curBase = STATE.playheadIdx >= 0 ? VISUAL.offsets[STATE.playheadIdx] : 0;
  let pos = (STATE.playheadPct / 100) * VISUAL.total;
  if (pos < curBase) pos = curBase;
  pos += accumBudget;
  accumBudget = 0;
  if (pos > VISUAL.total) pos = VISUAL.total;

  while (STATE.playheadIdx < STATE.globalEvents.length - 1) {
    const nextIdx = STATE.playheadIdx + 1;
    const next = VISUAL.offsets[nextIdx];
    if (next <= pos) stepForward();
    else break;
  }
  STATE.playheadPct = VISUAL.total > 0 ? (pos / VISUAL.total) * 100 : 0;

  renderEditor();
  renderPlayhead();
  renderTimeReadout();
  updateUnfocusedOverlay();
  updateSessionLullOverlay();
  maybeFlashBurst();

  if (STATE.playheadIdx >= STATE.globalEvents.length - 1) {
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
  if (p) { lastTick = 0; accumBudget = 0; rafId = requestAnimationFrame(tick); }
  else if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
}

/* =========================================================================
   Scrubbing
   ========================================================================= */

function activeSessionForGlobalIdx(idx) {
  if (idx < 0) return STATE.activeIdx;
  // The active session at any point is the session of the LAST event that
  // has fired at or before idx. (For idx === -1, fall back to first session.)
  if (idx >= STATE.globalEvents.length) idx = STATE.globalEvents.length - 1;
  return STATE.globalEvents[idx]?.sessionIdx ?? STATE.activeIdx;
}

function scrubToIdx(idx) {
  idx = Math.max(-1, Math.min(idx, STATE.globalEvents.length - 1));
  if (STATE.playing) setPlaying(false);
  const prev = STATE.playheadIdx;
  STATE.playheadIdx = idx;
  STATE.playheadPct = idx >= 0 ? visualPctForIdx(idx) : 0;

  rebuildAllSessionsToGlobalIdx(idx);

  // Snap active session to whoever owns the playhead's last fired event.
  const newActive = idx >= 0 ? activeSessionForGlobalIdx(idx) : 0;
  if (newActive !== STATE.activeIdx) setActiveSession(newActive, { flash: false, scrub: false });

  clearLiveEdits();
  if (idx > prev) {
    const apex = BURST_APEX_BY_IDX.get(idx);
    if (apex && STATE.sessions[apex.burst.sessionIdx] === STATE.sessions[STATE.activeIdx]) {
      addLiveBurstHighlight(apex.burst.kind, apex.start, apex.end - apex.start);
    }
  }

  renderEditor();
  renderPlayhead();
  renderTimeReadout();
  updateUnfocusedOverlay();
  updateSessionLullOverlay();
  _activeBurst = null;
  clearFragmentHighlight();
  maybeFlashBurst();
}

function visualPctToEventIdx(pct) {
  if (VISUAL.total <= 0 || !VISUAL.offsets.length) return -1;
  const target = (pct / 100) * VISUAL.total;
  if (target < VISUAL.offsets[0]) return -1;
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
  STATE.playheadPct = pct;
  rebuildAllSessionsToGlobalIdx(idx);

  const newActive = idx >= 0 ? activeSessionForGlobalIdx(idx) : STATE.activeIdx;
  if (newActive !== STATE.activeIdx) setActiveSession(newActive, { flash: false, scrub: false });

  clearLiveEdits();
  renderEditor();
  renderPlayhead();
  renderTimeReadout();
  updateUnfocusedOverlay();
  updateSessionLullOverlay();
  _activeBurst = null;
  clearFragmentHighlight();
  maybeFlashBurst();
}

/* =========================================================================
   Timeline hover tooltip
   ========================================================================= */

function timelineHover(clientX) {
  const track = $("timeline-track");
  const tooltip = $("timeline-tooltip");
  const rect = track.getBoundingClientRect();
  const pct = Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100));
  const idx = visualPctToEventIdx(pct);
  if (idx < 0) { tooltip.classList.remove("show"); return; }
  const ev = STATE.globalEvents[idx];
  if (!ev) return;

  let tag = "";
  for (const b of STATE.bursts) {
    if (idx >= b.start_idx && idx <= b.end_idx) {
      tag = `<span class="tt-tag ${escapeHtml(kindClass(b.kind))}">${kindLabel(b.kind)}</span>`;
      break;
    }
  }
  if (!tag) for (const fi of STATE.focusIntervals) {
    if (idx >= fi.blur_idx && idx < fi.focus_idx) { tag = `<span class="tt-tag unfocused">Unfocused</span>`; break; }
  }
  if (!tag) for (const g of STATE.idleGaps) {
    if (g.after_idx === idx && g.kind === "session-lull") {
      tag = `<span class="tt-tag idle">Switching docs</span>`; break;
    }
  }
  if (!tag && IDLE_AFTER_IDX.has(idx)) tag = `<span class="tt-tag idle">Idle</span>`;

  const sessName = STATE.sessions.length > 1
    ? ` · ${basename(STATE.sessions[ev.sessionIdx].document)}`
    : "";
  const elapsed = (ev.ts - Date.parse(STATE.meta.start_time)) / 1000;
  tooltip.innerHTML = `+${fmtDuration(elapsed)}${tag}<span class="tt-doc">${escapeHtml(sessName)}</span>`;
  tooltip.style.left = pct + "%";
  tooltip.classList.add("show");
}

/* =========================================================================
   Burst flash + overlays
   ========================================================================= */

let _activeBurst = null;
let _flashTimer = null;

const BURST_KIND_CLASSES = ["ide-action", "approved-paste", "internal-paste", "unapproved-paste"];

function setBannerKind(banner, kind) {
  banner.classList.remove(...BURST_KIND_CLASSES);
  banner.classList.add(kindClass(kind));
  const txt = $("burst-banner-text");
  if (txt) txt.textContent = kindLabel(kind);
}

function setWrapFlashKind(wrap, kind) {
  wrap.classList.remove(...BURST_KIND_CLASSES);
  wrap.classList.remove("burst-flash");
  void wrap.offsetWidth;
  wrap.classList.add("burst-flash", kindClass(kind));
}

function maybeFlashBurst() {
  for (const burst of STATE.bursts) {
    if (STATE.playheadIdx >= burst.start_idx && STATE.playheadIdx <= burst.end_idx) {
      const banner = $("burst-banner");
      if (_activeBurst === burst) { banner.classList.add("show"); return; }
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

  const blurT = Date.parse(STATE.globalEvents[active.blur_idx].timestamp);
  let nowT;
  const ev = STATE.globalEvents[Math.max(0, idx)];
  if (idx >= 0 && idx < STATE.globalEvents.length - 1) {
    const cur = VISUAL.offsets[idx];
    const next = VISUAL.offsets[idx + 1];
    const target = (STATE.playheadPct / 100) * VISUAL.total;
    const frac = next > cur ? Math.max(0, Math.min(1, (target - cur) / (next - cur))) : 0;
    const t1 = Date.parse(ev.timestamp);
    const t2 = Date.parse(STATE.globalEvents[idx + 1].timestamp);
    nowT = t1 + (t2 - t1) * frac;
  } else {
    nowT = Date.parse(ev.timestamp);
  }
  const elapsedSec = Math.max(0, Math.floor((nowT - blurT) / 1000));
  const m = Math.floor(elapsedSec / 60), s = elapsedSec % 60;
  elapsedEl.textContent = `${m}:${String(s).padStart(2, "0")}`;
}

// Shown when the playhead sits inside an inter-session lull and the user
// hasn't reached the target session yet. We only show this when the lull is
// "long" (≥ 4s) so micro-switches don't flash an overlay.
function updateSessionLullOverlay() {
  const overlay = $("session-switch-overlay");
  if (!overlay || STATE.sessions.length <= 1) { if (overlay) overlay.hidden = true; return; }
  const idx = STATE.playheadIdx;

  let active = null;
  for (const g of STATE.idleGaps) {
    if (g.kind !== "session-lull") continue;
    if (g.after_idx === idx && g.duration >= 4) { active = g; break; }
  }
  // Also show when we're "between" — pct strictly between this lull's two
  // anchor events, AND we haven't crossed to the next session yet. This is
  // already handled by the after_idx match above + the interpolation
  // happening in the playhead pct; just check that pct hasn't reached the
  // boundary.
  if (!active) { overlay.hidden = true; return; }

  const target = (STATE.playheadPct / 100) * VISUAL.total;
  const cur = VISUAL.offsets[idx];
  const next = VISUAL.offsets[idx + 1] ?? cur;
  if (skipIdle || target >= next - 0.01) { overlay.hidden = true; return; }

  overlay.hidden = false;
  $("ss-from").textContent = basename(STATE.sessions[active.sessionIdx].document);
  $("ss-to").textContent   = basename(STATE.sessions[active.toSessionIdx].document);

  const fromT = STATE.globalEvents[idx].ts;
  const frac = next > cur ? Math.max(0, Math.min(1, (target - cur) / (next - cur))) : 0;
  const t1 = fromT;
  const t2 = STATE.globalEvents[idx + 1].ts;
  const nowT = t1 + (t2 - t1) * frac;
  const elapsedSec = Math.max(0, Math.floor((nowT - fromT) / 1000));
  const m = Math.floor(elapsedSec / 60), s = elapsedSec % 60;
  $("ss-elapsed").textContent = `${m}:${String(s).padStart(2, "0")}`;
}

/* =========================================================================
   Theme
   ========================================================================= */

function setTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem("playback-theme", t); } catch (e) {}
}
function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") || "light";
  setTheme(cur === "dark" ? "light" : "dark");
}

/* =========================================================================
   Jump-to-flag + jump-to-tab
   ========================================================================= */

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
function jumpToSessionBoundary(dir) {
  const cur = STATE.playheadIdx;
  if (dir > 0) {
    for (const sb of STATE.sessionBoundaries) {
      if (sb.globalIdx > cur) { scrubToIdx(sb.globalIdx); return; }
    }
  } else {
    for (let i = STATE.sessionBoundaries.length - 1; i >= 0; i--) {
      if (STATE.sessionBoundaries[i].globalIdx < cur) { scrubToIdx(STATE.sessionBoundaries[i].globalIdx); return; }
    }
  }
}

/* =========================================================================
   Init
   ========================================================================= */

// Wire active session up-front: first session by chronological order.
STATE.activeIdx = 0;

renderTabStrip();
renderTopbar();
renderSummaryChips();
renderStatsSidebar();

// Build all sessions to "end" first to warm any side-effects, then rewind.
rebuildAllSessionsToGlobalIdx(STATE.globalEvents.length - 1);

STATE.playheadIdx = -1;
STATE.playheadPct = 0;
rebuildAllSessionsToGlobalIdx(-1);
renderEditor();

renderTimelineMarkers();
renderPlayhead();
renderTimeReadout();

/* =========================================================================
   Event listeners
   ========================================================================= */

$("play-btn").addEventListener("click", () => {
  if (STATE.playheadIdx >= STATE.globalEvents.length - 1) {
    STATE.playheadIdx = -1;
    STATE.playheadPct = 0;
    rebuildAllSessionsToGlobalIdx(-1);
    setActiveSession(0, { flash: false });
    clearLiveEdits();
    renderEditor();
    renderPlayhead();
    renderTimeReadout();
  }
  setPlaying(!STATE.playing);
});

$("speed").addEventListener("change", (e) => { STATE.speed = parseFloat(e.target.value); });

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
    "{  }           Previous / next document boundary\n" +
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
      if (STATE.playheadIdx >= STATE.globalEvents.length - 1) {
        STATE.playheadIdx = -1;
        STATE.playheadPct = 0;
        rebuildAllSessionsToGlobalIdx(-1);
        setActiveSession(0, { flash: false });
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
    case "{":
      e.preventDefault();
      jumpToSessionBoundary(-1);
      break;
    case "}":
      e.preventDefault();
      jumpToSessionBoundary(1);
      break;
    case "Home":
      e.preventDefault();
      scrubToIdx(0);
      break;
    case "End":
      e.preventDefault();
      scrubToIdx(STATE.globalEvents.length - 1);
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
window.__rebuildAllSessionsToGlobalIdx = rebuildAllSessionsToGlobalIdx;
