from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence

from recan.algorithm import DocumentMatcher
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
    load_recording,
    normalize_newlines,
    parse_ts,
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
    matcher: DocumentMatcher = field(default_factory=DocumentMatcher)
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


def _build_suffix_array(events: list[dict]) -> DocumentMatcher:
    """
    Build a suffix array from the event stream for efficient substring search.

    Usage: Used to see if recordings contain "internal paste" events from a session they
    already worked on, which can help identify when a student is pasting in code they
    previously wrote (and thus "approving" that fragment).
    """

    matcher = DocumentMatcher()

    for event in events:
        if _event_kind(event) == "edit":
            matcher.apply_edit(
                offset=event["offset"],
                old_fragment=event["oldFragment"],
                new_fragment=event["newFragment"],
                is_generated=_is_generated_edit(event),
                ts=parse_ts(event["timestamp"]),
            )

    matcher.finalize()

    return matcher


def _load_approved_fragments(approved_fragments_path: Path | None) -> str | None:
    if not approved_fragments_path:
        return ''

    approved_fragments_path = Path(approved_fragments_path)
    if approved_fragments_path.is_file():
        return normalize_newlines(approved_fragments_path.read_text())

    return ''


def _resolve_additional(paths: Path | list[Path] | None, recording_file: Path) -> list[Path]:
    if not paths:
        return []

    candidates = []

    for path in paths:
        if path.is_dir():
            jsonl_gz_paths = [Path(p) for p in path.glob("*.jsonl.gz")]
            jsonl_paths = [Path(p) for p in path.glob("*.jsonl")]

            candidates.extend(jsonl_gz_paths + jsonl_paths)
        else:
            candidates.append(Path(path))

    recording_resolved = recording_file.resolve()

    if not candidates:
        return []

    return [p for p in candidates if p.resolve() != recording_resolved]


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
            gap_duration = gap_seconds
            idle_gap: IdleGap = {
                "after_idx": len(state.events) - 1,
                "duration": gap_duration,
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


def _apply_edit_bookkeeping(event: dict, ts: datetime, state: _SessionState, is_generated: bool) -> None:
    """
    Apply a single edit to the matcher and keep all per-session bookkeeping in sync.

    Updates:
        - matcher document state (so suffix-array queries stay current)
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

    state.matcher.apply_edit(offset, old_fragment, new_fragment, is_generated=is_generated, ts=ts)

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
        line_count = new_fragment.count("\n") + 1
        char_count = len(new_fragment)
        generated_entry = {
            "timestamp": ts,
            "event": event,
            "event_idx": len(state.events) - 1,
            "line_count": line_count,
            "char_count": char_count,
            "fragment": new_fragment,
        }
        state.generated_entries.append(generated_entry)

    if state.edit_count % SNAPSHOT_INTERVAL_EVENTS == 0:
        snapshot = {
            "after_idx": len(state.events) - 1,
            "document_text": state.matcher.document,
        }
        state.snapshots.append(snapshot)


def _flush_generated_group(pending: list[tuple[dict, datetime]], state: _SessionState) -> None:
    """
    Commit a group of buffered generated edits as a single burst, keeping the longest fragment.

    Usage: IDE completions often arrive as several near-simultaneous edits (e.g. a stub
    followed by its filled-in body). Collapsing them to the longest fragment avoids
    double-counting the same paste-like action.
    """
    if not pending:
        return

    kept_event, kept_ts = max(pending, key=lambda pair: len(pair[0].get("newFragment", "")))
    _apply_edit_bookkeeping(kept_event, kept_ts, state, is_generated=True)
    pending.clear()


def _buffer_generated(event: dict, ts: datetime, pending: list[tuple[dict, datetime]], state: _SessionState) -> None:
    """
    Buffer a generated edit, flushing the previous group first if the burst window has elapsed.

    Generated edits arriving within BURST_GROUP_WINDOW of each other are treated
    as one burst; a longer gap means a new burst has started.
    """
    if pending:
        gap = ts - pending[-1][1]
        if gap > BURST_GROUP_WINDOW:
            _flush_generated_group(pending, state)

    pending.append((event, ts))


def _handle_typed_edit(event: dict, ts: datetime, state: _SessionState) -> None:
    """
    Apply a normal character-by-character edit (not flagged as generated).
    """
    _apply_edit_bookkeeping(event, ts, state, is_generated=False)


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
            "document_text": state.matcher.document,
        }
        state.snapshots.append(snapshot)


def _classify_burst_kind(event: dict, is_internal: bool, approved_fragment_string: str | None) -> str:
    """
    Bucket a generated edit into one of four burst kinds.

    Precedence:
        - "ide_action":        Matches a known IDE-completion pattern (see `_is_ide_action`).
        - "approved paste":    Fragment appears in the approved-fragments file.
        - "internal paste":    Fragment was previously present in this or an additional recording.
        - "unapproved paste":  Everything else.
    """
    if _is_ide_action(event):
        return "ide_action"

    fragment = event.get("newFragment", "")
    if approved_fragment_string and normalize_newlines(fragment) in approved_fragment_string:
        return "approved paste"

    if is_internal:
        return "internal paste"

    return "unapproved paste"


def _build_bursts(state: _SessionState, internal_flags: Sequence[bool], approved_fragment_string: str | None) -> None:
    """
    Turn each buffered generated entry into a Burst plus a matching timeline entry.

    Runs after the matcher has resolved which fragments were "internal" (previously
    seen in some recording), so each burst can be classified with full context.
    """
    for entry, is_internal in zip(state.generated_entries, internal_flags):
        kind = _classify_burst_kind(entry["event"], is_internal, approved_fragment_string)
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


def _compute_burst_totals(bursts: list[Burst]) -> dict[str, int]:
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

    totals = _compute_burst_totals(state.bursts)

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


def analyze_inputs(
        inputs: list[dict],
        extras: Sequence[DocumentMatcher] = (),
        approved_fragment_string: str | None = None,
) -> Session:
    """
    Walk a sequence of recording events and return a fully-populated Session.

    Pipeline:
        - Replay each event, tracking idle gaps and focus intervals.
        - Buffer "generated" edits into burst groups, flushing on gap or typed-edit boundary.
        - Finalize the suffix-array matcher and resolve which generated fragments are "internal"
          (i.e. came from history — this recording or one of the extras).
        - Classify each generated burst (ide_action / approved / internal / unapproved paste).
        - Assemble the Session dict with totals, snapshots, and a sorted timeline.
    """
    state = _SessionState()
    pending: list[tuple[dict, datetime]] = []

    for event in inputs:
        ts = parse_ts(event["timestamp"])
        _record_idle_gap(state, ts)
        kind = _event_kind(event)

        if kind == "edit" and _is_generated_edit(event):
            _buffer_generated(event, ts, pending, state)
            continue

        _flush_generated_group(pending, state)

        if kind == "focusStatus":
            _handle_focus(event, ts, state)
        elif kind == "edit":
            _handle_typed_edit(event, ts, state)

    _flush_generated_group(pending, state)
    _finalize_snapshots(state)

    state.matcher.finalize()
    internal_flags = state.matcher.resolve(extras)
    _build_bursts(state, internal_flags, approved_fragment_string)
    state.timeline.sort(key=lambda e: e["timestamp"])

    return _build_session(state)


def load_session(recording_files: Path, approved_fragments_path: Path | None, excluded_file_types: list[str],
                 additional_recordings: Path | list[Path] | None) -> Session:
    """
    Analyze a recording with optional filters and additional context, returning a Session object with the results.

    Usage:
        - recording_file: Path to the main .jsonl or .jsonl.gz recording to analyze.
        - additional_recordings: Optional list of paths or a directory containing additional
          .jsonl.gz recordings to include in the analysis for more context. The main recording_file
          is automatically excluded if present.
    """
    approved_fragments = _load_approved_fragments(approved_fragments_path)
    inputs = load_recording(recording_files, excluded_file_types)

    additional_inputs = [
        load_recording(recording, excluded_file_types)
        for recording in _resolve_additional(additional_recordings, recording_files)
    ]

    additional_sessions = []
    for additional_input in additional_inputs:
        additional_sessions.append(_build_suffix_array(additional_input))

    return analyze_inputs(inputs, additional_sessions, approved_fragments)
