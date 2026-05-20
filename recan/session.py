from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence

from recan.algorithm import DocumentMatcher, build_matcher
from recan.structure import (
    Burst,
    FocusInterval,
    IdleGap,
    Session,
    Snapshot,
    TimelineEntry,
)
from recan.utils import (
    language_from_extension,
    load_recordings,
    normalize_newlines,
    parse_ts,
    splice,
)
from recan.structure import CREATE_FUNCTION_PATTERN, MAIN_BLOCK_PATTERN

BURST_GROUP_WINDOW_MS = 100
BURST_GROUP_WINDOW = timedelta(milliseconds=BURST_GROUP_WINDOW_MS)
IDLE_GAP_THRESHOLD_SECONDS = 5.0
SNAPSHOT_INTERVAL_EVENTS = 200


@dataclass
class _SessionState:
    events: list[dict] = field(default_factory=list)
    focus_intervals: list[FocusInterval] = field(default_factory=list)
    idle_gaps: list[IdleGap] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    bursts: list[Burst] = field(default_factory=list)
    generated_entries: list[dict] = field(default_factory=list)
    timeline: list[TimelineEntry] = field(default_factory=list)
    document: str = ""
    document_name: str = ""
    blur_idx: int | None = None
    blur_ts: datetime | None = None
    prev_ts: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    total_time_unfocused: float = 0.0
    edit_count: int = 0


def _event_kind(event: dict) -> str:
    """
    Older recordings omit "type" — infer from which fields are present.
    """

    explicit = event.get("type")

    if explicit:
        return explicit

    if "focused" in event:
        return "focusStatus"

    if "newFragment" in event:
        return "edit"

    return "unknown"


def _is_generated_edit(event: dict) -> bool:
    """
    Heuristic for "this insert wasn't typed character-by-character."

    Checks:
        - Is the inserted fragment longer than 1 character?
        - Is the inserted fragment pure whitespace?
        - Does the inserted fragment look like an IDE tab completion (e.g. "function_name(")?
        - Does the inserted fragment look like a commented-out line of code?
    """

    fragment = event.get("newFragment", "")

    # If a student is truly typing fragments should only be 1 char
    if len(fragment) == 1:
        return False

    # Pure whitespace
    if not fragment.strip():
        return False

    # IDE Tab completes
    if all(c in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in fragment.rstrip('()').lower()):
        return False

    # Commented out line
    # TODO: Make this more robust
    if fragment.startswith('#'):
        return False

    # If not one of the previous checks it is probably a "paste" event
    return True


def _is_internal_paste(
        fragment: str,
        ts: datetime,
        matchers: Sequence[DocumentMatcher],
) -> bool:
    """
    Has this fragment appeared in any matcher's history strictly before `ts`?

    Checks:
        - For each matcher (this recording's own and any overlapping recordings'),
          does the corpus contain `fragment` in a snapshot that predates `ts`?
        - Self-capture is prevented by the matcher's own bisect_left cutoff —
          a fragment generated at ts only matches if it also lived in some
          earlier snapshot.
    """
    return any(m.contains(fragment, ts) for m in matchers)


def _is_ide_action(event: dict) -> bool:
    """
    Heuristic for "this edit looks like an IDE auto-completion or refactor."

    Checks:
        - Does the event contain a fragment that looks like PyCharm's "create function" action?
        - Does the event contain a fragment that looks like PyCharm's main block generation when tab completing "if __name__ == "__main__""?
    """

    # TODO: Need a refactor/rename heuristic

    fragment = event.get("newFragment", "")

    if CREATE_FUNCTION_PATTERN.fullmatch(fragment):
        return True

    if MAIN_BLOCK_PATTERN.fullmatch(fragment):
        return True

    return False


def _is_approved_paste(event: dict, approved_pastes: str | None) -> bool:
    """
    Heuristic for "this generated edit matches a fragment in the approved-fragments file."

    Checks:
        - Does the event contain a fragment that appears in the approved-fragments file?
    """

    if not approved_pastes:
        return False

    fragment = event.get("newFragment", "")
    return normalize_newlines(fragment) in approved_pastes


def _load_approved_pastes(approved_pastes_path: Path | None) -> str | None:
    if not approved_pastes_path:
        return ''

    approved_pastes_path = Path(approved_pastes_path)
    if approved_pastes_path.is_file():
        return normalize_newlines(approved_pastes_path.read_text())

    return ''


def _record_idle_gap(state: _SessionState, ts: datetime) -> None:
    """
    Detect idle gaps and maintain time bookkeeping.
    """
    if state.start_time is None:
        state.start_time = ts

    state.end_time = ts

    if state.prev_ts is not None:
        gap_seconds = (ts - state.prev_ts).total_seconds()
        if gap_seconds > IDLE_GAP_THRESHOLD_SECONDS:
            idle_gap: IdleGap = {
                "after_idx": len(state.events) - 1,
                "duration": gap_seconds,
            }
            state.idle_gaps.append(idle_gap)

    state.prev_ts = ts


def _handle_focus(event: dict, ts: datetime, state: _SessionState) -> None:
    """
    Record a focusStatus event and close out a focus interval if the editor just regained focus.

    A blur (focused=False) is remembered as a pending blur_idx; the next focus
    (focused=True) emits a FocusInterval and a "focus" timeline entry covering
    the time the student was away.
    """
    focused_event = {
        "timestamp": event["timestamp"],
        "type": "focusStatus",
        "focused": bool(event.get("focused")),
    }
    state.events.append(focused_event)

    focused = event.get("focused")

    if focused is False and state.blur_idx is None:
        state.blur_idx = len(state.events) - 1
        state.blur_ts = ts
    elif focused is True and state.blur_idx is not None:
        duration = (ts - state.blur_ts).total_seconds()
        state.total_time_unfocused += duration

        focus_interval: FocusInterval = {
            "blur_idx": state.blur_idx,
            "focus_idx": len(state.events) - 1,
            "duration": duration,
        }
        state.focus_intervals.append(focus_interval)

        timeline_entry: TimelineEntry = {
            "kind": "focus",
            "timestamp": ts,
            "duration": duration,
        }
        state.timeline.append(timeline_entry)

        state.blur_idx = None
        state.blur_ts = None


def _apply_edit(event: dict, ts: datetime, state: _SessionState, is_generated: bool) -> None:
    """
    Apply a single edit to state.document and record it in the session's bookkeeping.

    Updates:
        - state.document (splice in the new fragment so snapshots stay current)
        - the canonical events list and edit_count
        - generated_entries (only when is_generated=True, used later for burst classification)
        - periodic snapshots (every SNAPSHOT_INTERVAL_EVENTS edits, for timeline scrubbing)
    """
    old_fragment = event.get("oldFragment", "")
    new_fragment = event.get("newFragment", "")
    offset = event.get("offset", 0)
    doc = event.get("document", "")

    if state.document_name == "" and doc:
        state.document_name = doc

    state.document = splice(state.document, offset, old_fragment, new_fragment)

    edit_event = {
        "timestamp": event["timestamp"],
        "type": "edit",
        "offset": offset,
        "oldFragment": old_fragment,
        "newFragment": new_fragment,
    }
    state.events.append(edit_event)

    state.edit_count += 1

    if is_generated:
        state.generated_entries.append({
            "timestamp": ts,
            "event": event,
            "event_idx": len(state.events) - 1,
            "line_count": new_fragment.count("\n") + 1,
            "char_count": len(new_fragment),
            "fragment": new_fragment,
        })

    if state.edit_count % SNAPSHOT_INTERVAL_EVENTS == 0:
        state.snapshots.append({
            "after_idx": len(state.events) - 1,
            "document_text": state.document,
        })


def _collapse_groups(entries: list[dict], window: timedelta) -> list[dict]:
    """
    Collapse adjacent generated entries whose timestamps fall within `window` into one.

    Walks `entries` in order. From one entry to the next: if the gap is within
    `window`, they're part of the same IDE burst — keep accumulating. Once the
    gap exceeds `window`, the current group is closed (keeping the entry with
    the longest fragment) and a new group starts.
    """
    flushed: list[dict] = []
    group: list[dict] = []

    for entry in entries:
        if group and (entry["timestamp"] - group[-1]["timestamp"]) > window:
            flushed.append(max(group, key=lambda e: len(e["fragment"])))
            group = []
        group.append(entry)

    if group:
        flushed.append(max(group, key=lambda e: len(e["fragment"])))

    return flushed


def _finalize_snapshots(state: _SessionState) -> None:
    """
    Ensure a snapshot exists at the final event index.

    The periodic snapshot only fires every SNAPSHOT_INTERVAL_EVENTS edits, so the
    final document state may be missed; this appends one if needed.
    """
    if not state.events:
        return

    last = state.snapshots[-1] if state.snapshots else None
    if last is None or last["after_idx"] != len(state.events) - 1:
        snapshot = {
            "after_idx": len(state.events) - 1,
            "document_text": state.document,
        }
        state.snapshots.append(snapshot)


def _classify_burst(
        event: dict,
        ts: datetime,
        matchers: Sequence[DocumentMatcher],
        approved_pastes: str | None,
) -> str:
    """
    Bucket a generated edit into one of four burst kinds.

    Precedence:
        - "ide_action":       Matches a known IDE-completion pattern (see `_is_ide_action`).
        - "approved paste":   Fragment appears in the approved-fragments file.
        - "internal paste":   Fragment was previously present in this or an overlapping recording.
        - "unapproved paste": Everything else.

    IDE actions are checked first so that an IDE-emitted stub like
    `def main():\\n    ` isn't misclassified as an internal paste just because
    it's a prefix of an earlier IDE emission like `def main():\\n    pass`.
    """
    fragment = event.get("newFragment", "")

    if _is_ide_action(event):
        return "ide_action"

    if _is_approved_paste(event, approved_pastes):
        return "approved paste"

    if _is_internal_paste(fragment, ts, matchers):
        return "internal paste"

    return "unapproved paste"


def _build_bursts(
        state: _SessionState,
        matchers: Sequence[DocumentMatcher],
        approved_pastes: str | None,
) -> None:
    """
    Turn each buffered generated entry into a Burst plus a matching timeline entry.

    Runs after the session has walked through all the events in a recording, so each
    burst can be classified with full context — including cross-recording matcher lookups.
    """
    for entry in state.generated_entries:
        kind = _classify_burst(entry["event"], entry["timestamp"], matchers, approved_pastes)
        state.bursts.append(Burst(
            kind=kind,
            timestamp=entry["timestamp"],
            start_idx=entry["event_idx"],
            end_idx=entry["event_idx"],
            line_count=entry["line_count"],
            char_count=entry["char_count"],
            fragment=entry["fragment"],
        ))
        state.timeline.append({
            "kind": kind,
            "timestamp": entry["timestamp"],
            "line_count": entry["line_count"],
            "char_count": entry["char_count"],
            "fragment": entry["fragment"],
        })


def _count_bursts(bursts: list[Burst]) -> dict[str, int]:
    """
    Count bursts by kind, returning a dict keyed by every possible burst kind (zero-filled).
    """
    totals = {
        "ide_action": 0,
        "unapproved paste": 0,
        "approved paste": 0,
        "internal paste": 0,
    }
    for burst in bursts:
        totals[burst["kind"]] += 1
    return totals


def _build_session(state: _SessionState) -> Session:
    """
    Assemble the final Session dict from accumulated state — totals, timeline, snapshots, and events.
    """
    if state.start_time and state.end_time:
        total_time = (state.end_time - state.start_time).total_seconds()
    else:
        total_time = 0.0

    totals = _count_bursts(state.bursts)

    return {
        "document": state.document_name,
        "language": language_from_extension(state.document_name),
        "start_time": state.start_time,
        "end_time": state.end_time,
        "total_time": total_time,
        "total_time_unfocused": state.total_time_unfocused,
        "total_edits": state.edit_count,
        "total_unapproved_pastes": totals["unapproved paste"],
        "total_approved_pastes": totals["approved paste"],
        "total_internal_pastes": totals["internal paste"],
        "total_ide_actions": totals["ide_action"],
        "total_generated_events": sum(totals.values()),
        "events": state.events,
        "focus_intervals": state.focus_intervals,
        "idle_gaps": state.idle_gaps,
        "bursts": state.bursts,
        "snapshots": state.snapshots,
        "timeline": state.timeline,
    }


def analyze_events(
        events: list[dict],
        matchers: Sequence[DocumentMatcher] = (),
        approved_pastes: str | None = None,
) -> Session:
    """
    Walk a sequence of recording events and return a fully-populated Session.

    Pipeline:
        - Replay each event, tracking idle gaps and focus intervals, and recording
          every generated edit into state.generated_entries.
        - Collapse adjacent generated entries within BURST_GROUP_WINDOW into single
          bursts (so PyCharm-style multi-edit completions register once, not N times).
        - Classify each burst (ide_action / approved / internal / unapproved paste),
          querying every matcher in `matchers` for cross-recording internal-paste detection.
        - Assemble the Session dict with totals, snapshots, and a sorted timeline.
    """
    state = _SessionState()

    for event in events:
        ts = parse_ts(event["timestamp"])
        _record_idle_gap(state, ts)
        kind = _event_kind(event)

        if kind == "focusStatus":
            _handle_focus(event, ts, state)
        elif kind == "edit":
            _apply_edit(event, ts, state, _is_generated_edit(event))

    _finalize_snapshots(state)
    state.generated_entries = _collapse_groups(state.generated_entries, BURST_GROUP_WINDOW)

    _build_bursts(state, matchers, approved_pastes)
    state.timeline.sort(key=lambda e: e["timestamp"])

    return _build_session(state)


def load_sessions(
        recording_files: list[Path],
        approved_pastes_path: Path | None,
        excluded_file_types: list[str],
) -> list[Session]:
    """
    Load and analyze every recording matched by `recording_files`, one Session each.

    Pipeline:
        - Load the approved-pastes file (if any).
        - Expand the input paths (files and/or globs) into (path, events) pairs.
        - Build one DocumentMatcher per recording up front, so each session's
          internal-paste check can search across the whole batch.
        - Analyze each recording, passing the full matcher list in.
    """
    approved_pastes = _load_approved_pastes(approved_pastes_path)

    recordings = load_recordings(recording_files, excluded_file_types)

    matchers = [build_matcher(events) for _, events in recordings]

    return [
        analyze_events(events, matchers, approved_pastes)
        for _, events in recordings
    ]
