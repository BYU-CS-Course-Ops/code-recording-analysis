const $ = id => document.getElementById(id);
const bundle = window.__BUNDLE__;
const sessions = bundle.sessions;

const KIND_LABELS = {
  ide_action: "IDE action",
  approved_paste: "Approved paste",
  internal_paste: "Internal paste",
  unapproved_paste: "Unapproved paste",
  unfocused: "Unfocused",
  idle_gap: "Idle gap",
  starter_code_mismatch: "Starter-code mismatch",
};
const PLAY_ICON = '<path d="M4 2.5v11l9-5.5z"/>';
const PAUSE_ICON = '<path d="M4 2.5h3v11H4zM9 2.5h3v11H9z"/>';
const ANNOTATION_HIGHLIGHT_NAME = "annotation";
const IDLE_KINDS = new Set(["idle_gap", "unfocused"]);

const state = {
  activeSession: 0,
  playhead: -1,
  positionMs: 0,
  playing: false,
  speed: 1,
  animation: null,
  lastFrame: null,
};

const esc = value => String(value ?? "").replace(
  /[&<>"']/g,
  char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char],
);
const basename = value => String(value || "(unknown)").split(/[\\/]/).pop();
const formatDuration = seconds => {
  const total = Math.max(0, Math.round(seconds || 0));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
};

function buildPlaybackIndex(sessionList) {
  const entries = [];
  const localToGlobal = sessionList.map(() => []);

  sessionList.forEach((session, sessionIndex) => {
    session.entries.forEach((entry, entryIndex) => {
      entries.push({
        entry,
        entryIndex,
        sessionIndex,
        timestampMs: Date.parse(entry.timestamp),
      });
    });
  });
  entries.sort((left, right) => left.timestampMs - right.timestampMs
    || left.sessionIndex - right.sessionIndex
    || left.entryIndex - right.entryIndex);
  entries.forEach((item, globalIndex) => {
    item.globalIndex = globalIndex;
    localToGlobal[item.sessionIndex][item.entryIndex] = globalIndex;
  });
  return { entries, localToGlobal };
}

const playback = buildPlaybackIndex(sessions);
const sessionTimes = sessions.flatMap(session => [
  Date.parse(session.start_time),
  Date.parse(session.end_time),
]).filter(Number.isFinite);
const entryTimes = playback.entries.map(item => item.timestampMs).filter(Number.isFinite);
const allTimes = sessionTimes.concat(entryTimes);
const startMs = allTimes.length ? Math.min(...allTimes) : 0;
const endMs = allTimes.length ? Math.max(...allTimes) : startMs;
state.positionMs = startMs;

const annotations = sessions.flatMap((session, sessionIndex) => (
  session.annotations.map((annotation, localAnnotationIndex) => ({
    ...annotation,
    localAnnotationIndex,
    sessionIndex,
    timestampMs: Date.parse(annotation.timestamp),
    endTimestampMs: Date.parse(annotation.end_timestamp || annotation.timestamp),
    globalStart: annotation.entry_start == null
      ? -1
      : playback.localToGlobal[sessionIndex][annotation.entry_start],
    globalEnd: annotation.entry_end == null
      ? -1
      : playback.localToGlobal[sessionIndex][annotation.entry_end],
  }))
));

function mergedIdleIntervals() {
  const intervals = annotations
    .filter(annotation => IDLE_KINDS.has(annotation.kind)
      && Number.isFinite(annotation.timestampMs)
      && Number.isFinite(annotation.endTimestampMs)
      && annotation.endTimestampMs > annotation.timestampMs)
    .map(annotation => [
      Math.max(startMs, annotation.timestampMs),
      Math.min(endMs, annotation.endTimestampMs),
    ])
    .filter(([start, end]) => end > start)
    .sort((left, right) => left[0] - right[0]);
  const merged = [];
  intervals.forEach(interval => {
    const previous = merged.at(-1);
    if (previous && interval[0] <= previous[1]) previous[1] = Math.max(previous[1], interval[1]);
    else merged.push(interval);
  });
  return merged;
}

const idleIntervals = mergedIdleIntervals();
const isSkippingIdle = () => $("skip-idle").checked;

function visualOffset(recordedMs) {
  if (!isSkippingIdle()) return Math.max(0, recordedMs - startMs);
  let result = Math.max(0, recordedMs - startMs);
  for (const [idleStart, idleEnd] of idleIntervals) {
    if (recordedMs <= idleStart) break;
    result -= Math.min(recordedMs, idleEnd) - idleStart;
  }
  return Math.max(0, result);
}

function visualDuration() {
  return Math.max(1, visualOffset(endMs));
}

function recordedTimeAtVisual(offsetMs) {
  if (!isSkippingIdle()) return Math.min(endMs, startMs + offsetMs);
  let recorded = startMs + offsetMs;
  for (const [idleStart, idleEnd] of idleIntervals) {
    if (recorded < idleStart) break;
    recorded += idleEnd - idleStart;
  }
  return Math.min(endMs, recorded);
}

function documentAt(session, entryIndex) {
  let text = "";
  let startIndex = 0;
  const initial = session.entries.find(entry => entry.type === "initialSnapshot");
  if (initial) text = initial.document_text;

  const checkpoint = session.snapshots
    .filter(snapshot => snapshot.after_idx <= entryIndex)
    .at(-1);
  if (checkpoint) {
    text = checkpoint.document_text;
    startIndex = checkpoint.after_idx + 1;
  }
  for (let index = startIndex; index <= entryIndex; index += 1) {
    const entry = session.entries[index];
    if (entry?.type === "edit") {
      text = text.slice(0, entry.offset)
        + entry.newFragment
        + text.slice(entry.offset + entry.oldFragment.length);
    }
  }
  return text;
}

function updateActiveState() {
  document.querySelectorAll(".tab").forEach((tab, index) => {
    const active = index === state.activeSession;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".session-row").forEach((row, index) => {
    row.classList.toggle("active", index === state.activeSession);
  });
}

function renderSession() {
  const session = sessions[state.activeSession];
  $("filename").textContent = basename(session?.document);
  $("filename").title = session?.document || "";
  const language = String(session?.language || "plaintext").toLowerCase();
  $("lang-pill").textContent = language;
  const text = session?.documentText || "";
  const code = $("editor-code");
  if (window.hljs?.getLanguage(language)) {
    try {
      code.innerHTML = window.hljs.highlight(text, {
        language,
        ignoreIllegals: true,
      }).value;
    } catch (_) {
      code.textContent = text;
    }
  } else {
    code.textContent = text;
  }
  code.className = `hljs language-${language}`;
  $("line-gutter").textContent = Array.from(
    { length: text.split("\n").length },
    (_, index) => index + 1,
  ).join("\n");
  updateActiveState();
}

function selectSession(index) {
  state.activeSession = index;
  window.CSS?.highlights?.delete(ANNOTATION_HIGHLIGHT_NAME);
  renderSession();
}

function renderTabs() {
  const host = $("tab-strip");
  sessions.forEach((session, index) => {
    const tab = document.createElement("button");
    tab.className = "tab";
    tab.setAttribute("role", "tab");
    tab.textContent = basename(session.document);
    tab.title = session.document;
    tab.onclick = () => selectSession(index);
    host.appendChild(tab);
  });
}

function renderSummary() {
  const counts = bundle.annotation_counts;
  $("sum-edits").textContent = sessions.reduce((total, session) => total + session.total_edits, 0);
  $("sum-ide-actions").textContent = counts.ide_action;
  $("sum-approved-pastes").textContent = counts.approved_paste;
  $("sum-internal-pastes").textContent = counts.internal_paste;
  $("sum-unapproved-pastes").textContent = counts.unapproved_paste;
  $("sum-unfocused").textContent = counts.unfocused;
  $("sum-idle").textContent = counts.idle_gap;
}

function annotationLabel(annotation) {
  const extra = annotation.fragment
    ? ` · ${annotation.char_count} chars`
    : annotation.duration
      ? ` · ${formatDuration(annotation.duration)} away`
      : "";
  return `${KIND_LABELS[annotation.kind] || annotation.kind}${extra}`;
}

function annotationClass(annotation) {
  return annotation.kind.replaceAll("_", "-");
}

function chronological(left, right) {
  return left.timestampMs - right.timestampMs
    || left.sessionIndex - right.sessionIndex
    || left.localAnnotationIndex - right.localAnnotationIndex;
}

function renderKeyMoments() {
  const host = $("flags-list");
  const groups = { HIGH: [], MEDIUM: [], LOW: [] };
  annotations.filter(annotation => annotation.review_severity).forEach(annotation => {
    groups[annotation.review_severity].push(annotation);
  });
  Object.values(groups).forEach(group => group.sort(chronological));

  host.innerHTML = Object.entries(groups).map(([severity, items]) => items.length ? `
    <details class="flag-group ${severity.toLowerCase()}" open>
      <summary>${severity}<span class="fg-count">${items.length}</span></summary>
      <div class="flag-group-body">${items.map(annotation => {
        const index = annotations.indexOf(annotation);
        return `<button class="flag ${annotationClass(annotation)}" data-index="${index}">
          <span class="flag-body">
            <span class="flag-title">${esc(annotationLabel(annotation))}</span>
            <span class="flag-meta">${esc(basename(sessions[annotation.sessionIndex].document))}</span>
          </span>
        </button>`;
      }).join("")}</div>
    </details>` : "").join("");
  host.querySelectorAll(".flag").forEach(button => {
    button.onclick = () => seekAnnotation(annotations[Number(button.dataset.index)]);
  });
  if (!host.innerHTML) host.innerHTML = '<div class="flag-empty">No review annotations.</div>';
}

function transformedFragmentRange(annotation) {
  if (!annotation.fragment || annotation.entry_start == null || annotation.entry_end == null) return null;
  const session = sessions[annotation.sessionIndex];
  const entries = session.entries;
  let sourceIndex = -1;
  for (let index = annotation.entry_start; index <= annotation.entry_end; index += 1) {
    if (entries[index]?.type === "edit" && entries[index].newFragment === annotation.fragment) {
      sourceIndex = index;
      break;
    }
  }
  if (sourceIndex < 0) return null;

  let start = entries[sourceIndex].offset;
  let end = start + entries[sourceIndex].newFragment.length;
  for (let index = sourceIndex + 1; index <= annotation.entry_end; index += 1) {
    const entry = entries[index];
    if (entry?.type !== "edit") continue;
    const editStart = entry.offset;
    const editEnd = editStart + entry.oldFragment.length;
    const delta = entry.newFragment.length - entry.oldFragment.length;
    if (editEnd <= start) {
      start += delta;
      end += delta;
    } else if (editStart < end) {
      end = Math.max(start, end + delta);
    }
  }
  return { start, end };
}

function textPosition(root, targetOffset) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let offset = 0;
  let node;
  while ((node = walker.nextNode())) {
    const nextOffset = offset + node.length;
    if (targetOffset <= nextOffset) {
      return { node, offset: targetOffset - offset };
    }
    offset = nextOffset;
  }
  return null;
}

function highlight(annotation) {
  if (!window.CSS?.highlights || !window.Highlight) return;
  CSS.highlights.delete(ANNOTATION_HIGHLIGHT_NAME);
  const offsets = transformedFragmentRange(annotation);
  const code = $("editor-code");
  if (!offsets || offsets.end <= offsets.start || offsets.end > code.textContent.length) return;
  const start = textPosition(code, offsets.start);
  const end = textPosition(code, offsets.end);
  if (!start || !end) return;
  const range = document.createRange();
  range.setStart(start.node, start.offset);
  range.setEnd(end.node, end.offset);
  CSS.highlights.set(ANNOTATION_HIGHLIGHT_NAME, new Highlight(range));
}

function seekAnnotation(annotation) {
  state.activeSession = annotation.sessionIndex;
  if (annotation.globalEnd >= 0) seekGlobal(annotation.globalEnd);
  else renderSession();
  highlight(annotation);
}

function renderTimeline() {
  const host = $("timeline-markers");
  const duration = visualDuration();
  host.innerHTML = annotations.filter(annotation => Number.isFinite(annotation.timestampMs)).map(annotation => {
    const left = visualOffset(annotation.timestampMs) / duration * 100;
    if (IDLE_KINDS.has(annotation.kind) && annotation.endTimestampMs > annotation.timestampMs) {
      const right = visualOffset(annotation.endTimestampMs) / duration * 100;
      return `<div class="tick-annotation interval ${annotationClass(annotation)}"
        title="${esc(KIND_LABELS[annotation.kind])}"
        style="left:${left}%;width:${Math.max(0.2, right - left)}%"></div>`;
    }
    return `<div class="tick-annotation ${annotationClass(annotation)}"
      title="${esc(KIND_LABELS[annotation.kind] || annotation.kind)}"
      style="left:${left}%"></div>`;
  }).join("");

  for (let index = 1; index < playback.entries.length; index += 1) {
    const previous = playback.entries[index - 1];
    const current = playback.entries[index];
    if (previous.sessionIndex !== current.sessionIndex) {
      const from = basename(sessions[previous.sessionIndex].document);
      const to = basename(sessions[current.sessionIndex].document);
      const left = visualOffset(current.timestampMs) / duration * 100;
      host.insertAdjacentHTML(
        "beforeend",
        `<div class="tick-session" title="${esc(`${from} → ${to}`)}" style="left:${left}%"></div>`,
      );
    }
  }
  renderPlayhead();
}

function rebuildDocuments(globalIndex) {
  sessions.forEach((session, sessionIndex) => {
    const localIndex = playback.localToGlobal[sessionIndex]
      .findLastIndex(index => index <= globalIndex);
    session.documentText = documentAt(session, localIndex);
  });
}

function renderPlayhead() {
  const percent = visualOffset(state.positionMs) / visualDuration() * 100;
  $("playhead").style.left = `${percent}%`;
  $("progress-fill").style.width = `${percent}%`;
  $("now-time").textContent = formatDuration((state.positionMs - startMs) / 1000);
}

function seekGlobal(globalIndex, positionMs = null) {
  const nextPlayhead = Math.max(-1, Math.min(globalIndex, playback.entries.length - 1));
  const documentStateMissing = sessions.some(session => session.documentText === undefined);
  if (nextPlayhead !== state.playhead || documentStateMissing) rebuildDocuments(nextPlayhead);
  state.playhead = nextPlayhead;
  const current = playback.entries[state.playhead];
  state.positionMs = positionMs == null
    ? (current?.timestampMs ?? startMs)
    : Math.max(startMs, Math.min(endMs, positionMs));
  if (current) state.activeSession = current.sessionIndex;
  renderSession();
  renderPlayhead();
  window.CSS?.highlights?.delete(ANNOTATION_HIGHLIGHT_NAME);
}

function seekTime(recordedMs) {
  let target = Math.max(startMs, Math.min(endMs, recordedMs));
  if (isSkippingIdle()) {
    const interval = idleIntervals.find(([start, end]) => target > start && target < end);
    if (interval) target = interval[1];
  }
  let low = 0;
  let high = playback.entries.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (playback.entries[middle].timestampMs <= target) low = middle + 1;
    else high = middle;
  }
  seekGlobal(low - 1, target);
}

function tick(frameTime) {
  if (!state.playing) return;
  if (state.lastFrame == null) state.lastFrame = frameTime;
  const delta = (frameTime - state.lastFrame) * state.speed;
  state.lastFrame = frameTime;
  const nextVisual = visualOffset(state.positionMs) + delta;
  seekTime(recordedTimeAtVisual(nextVisual));
  if (state.positionMs >= endMs) {
    setPlaying(false);
    return;
  }
  state.animation = requestAnimationFrame(tick);
}

function setPlaying(playing) {
  state.playing = playing;
  state.lastFrame = null;
  if (state.animation != null) cancelAnimationFrame(state.animation);
  state.animation = null;
  $("play-icon").innerHTML = playing ? PAUSE_ICON : PLAY_ICON;
  $("play-btn").setAttribute("aria-label", playing ? "Pause" : "Play");
  if (playing) {
    if (state.positionMs >= endMs) seekGlobal(-1, startMs);
    state.animation = requestAnimationFrame(tick);
  }
}

function renderStats() {
  $("stats").innerHTML = `
    <div class="stats-section">
      <div class="stats-section-title">Overall</div>
      <div class="stat-grid">
        <div class="stat"><span class="stat-label">Edits</span><span class="stat-value">${sessions.reduce((total, session) => total + session.total_edits, 0)}</span></div>
        <div class="stat"><span class="stat-label">Review severity</span><span class="stat-value">${bundle.review_severity || "None"}</span></div>
      </div>
    </div>
    <div class="stats-section">
      <div class="stats-section-title">Documents</div>
      <div class="session-list">${sessions.map((session, index) => `
        <button class="session-row" data-session="${index}" title="${esc(session.document)}">
          <span class="session-row-name">${esc(basename(session.document))}</span>
          <span class="session-row-meta">${formatDuration(session.total_time)}</span>
        </button>`).join("")}
      </div>
    </div>
    <div class="stats-section">
      <div class="stats-section-title">Key moments</div>
      <div class="flags" id="flags-list"></div>
    </div>`;
  $("stats").querySelectorAll(".session-row").forEach(row => {
    row.onclick = () => selectSession(Number(row.dataset.session));
  });
  renderKeyMoments();
}

function bindControls() {
  $("play-btn").onclick = () => setPlaying(!state.playing);
  $("speed").onchange = event => { state.speed = Number(event.target.value); };
  $("skip-idle").onchange = () => {
    if (isSkippingIdle()) seekTime(state.positionMs);
    renderTimeline();
  };
  $("theme-btn").onclick = () => {
    const root = document.documentElement;
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    try { localStorage.setItem("playback-theme", root.dataset.theme); } catch (_) { /* ignore */ }
  };
  $("help-btn").onclick = () => window.alert(
    "Keyboard shortcuts\n\n"
    + "Space       Play / Pause\n"
    + "←  →        Scrub one entry\n"
    + "Home / End  Jump to start / end\n"
    + "T           Toggle theme",
  );
  $("timeline-track").onclick = event => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const fraction = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
    seekTime(recordedTimeAtVisual(fraction * visualDuration()));
  };
  window.addEventListener("keydown", event => {
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(event.target?.tagName)) return;
    if (event.key === " ") {
      event.preventDefault();
      setPlaying(!state.playing);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      seekGlobal(state.playhead - 1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      seekGlobal(state.playhead + 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      seekGlobal(-1, startMs);
    } else if (event.key === "End") {
      event.preventDefault();
      seekGlobal(playback.entries.length - 1, endMs);
    } else if (event.key.toLowerCase() === "t") {
      $("theme-btn").click();
    }
  });
}

renderTabs();
renderSummary();
renderStats();
renderTimeline();
bindControls();
$("total-time").textContent = ` / ${formatDuration((endMs - startMs) / 1000)}`;
seekGlobal(-1, startMs);

window.__PLAYER__ = {
  annotations,
  playback,
  seekAnnotation,
  state,
  transformedFragmentRange,
  visualOffset,
};
