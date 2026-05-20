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

IDLE_GAP_THRESHOLD_SECONDS = 5.0
SNAPSHOT_INTERVAL_EVENTS = 200

# Adjacent edits within this gap belong to the same IDE action / paste.
# IDE templates (PyCharm "create function", VS Code snippet expansion) fire a
# flurry of edits in the same millisecond; real typing pauses far longer.
# 250 ms sits comfortably between the two.
BURST_CLUSTER_WINDOW_MS = 250
BURST_CLUSTER_WINDOW = timedelta(milliseconds=BURST_CLUSTER_WINDOW_MS)


@dataclass
class _Cluster:
    """A run of adjacent edits that belong to one IDE action or paste."""

    entries: list[dict] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.entries

    @property
    def last_ts(self) -> datetime:
        return self.entries[-1]["ts"]

    @property
    def has_generated(self) -> bool:
        return any(e["is_generated"] for e in self.entries)


@dataclass
class _SessionState:
    events: list[dict] = field(default_factory=list)
    focus_intervals: list[FocusInterval] = field(default_factory=list)
    idle_gaps: list[IdleGap] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    bursts: list[Burst] = field(default_factory=list)
    timeline: list[TimelineEntry] = field(default_factory=list)
    cluster: _Cluster = field(default_factory=_Cluster)
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


def _apply_edit(event: dict, state: _SessionState) -> None:
    """
    Apply a single edit to state.document and record it in the session's bookkeeping.

    Updates:
        - state.document (splice in the new fragment so snapshots stay current)
        - the canonical events list and edit_count
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

    if state.edit_count % SNAPSHOT_INTERVAL_EVENTS == 0:
        state.snapshots.append({
            "after_idx": len(state.events) - 1,
            "document_text": state.document,
        })


def _extend_cluster(state: _SessionState, event: dict, ts: datetime, is_generated: bool) -> None:
    """
    Add the just-applied edit to the in-flight cluster.

    Only generated edits seed a cluster. Once a cluster is in flight, ANY edit
    within BURST_CLUSTER_WINDOW joins it — that's how trailing whitespace
    fixups emitted by an IDE template get folded into the burst's range,
    instead of leaving start_idx == end_idx and a highlight that fires before
    the IDE has finished spacing the inserted text.
    """
    if state.cluster.is_empty and not is_generated:
        return

    state.cluster.entries.append({
        "idx": len(state.events) - 1,
        "ts": ts,
        "event": event,
        "fragment": event.get("newFragment", ""),
        "is_generated": is_generated,
    })


def _classify_cluster(
        cluster: _Cluster,
        canonical: dict,
        matchers: Sequence[DocumentMatcher],
        approved_pastes: str | None,
) -> str:
    """
    Pick a burst kind for the whole cluster.

    IDE-action detection scans every entry — a stub-then-clean template often
    leaves the stub matching the regex while the longest fragment doesn't.
    The other rules consult the canonical (longest) fragment, which is the
    text the IDE actually inserted.

    Precedence:
        - "ide_action":       Any entry in the cluster matches an IDE-template pattern.
        - "approved paste":   Canonical fragment appears in the approved-fragments file.
        - "internal paste":   Canonical fragment lived in some earlier snapshot.
        - "unapproved paste": Everything else.
    """
    if any(_is_ide_action(e["event"]) for e in cluster.entries):
        return "ide_action"

    if _is_approved_paste(canonical["event"], approved_pastes):
        return "approved paste"

    if _is_internal_paste(canonical["fragment"], canonical["ts"], matchers):
        return "internal paste"

    return "unapproved paste"


def _flush_cluster(
        state: _SessionState,
        matchers: Sequence[DocumentMatcher],
        approved_pastes: str | None,
) -> None:
    """
    Emit one burst spanning the in-flight cluster, then clear it.

    The burst's range covers every event in the cluster (the IDE-inserted
    text plus any trailing whitespace fixups). The canonical fragment is the
    longest generated edit — that's the text a reviewer wants to see in the
    viewer's highlight.

    Clusters with no generated edits (just trailing typed events that never
    got seeded) are silently dropped.
    """
    cluster = state.cluster
    if not cluster.has_generated:
        cluster.entries.clear()
        return

    generated = [e for e in cluster.entries if e["is_generated"]]
    canonical = max(generated, key=lambda e: len(e["fragment"]))
    kind = _classify_cluster(cluster, canonical, matchers, approved_pastes)
    fragment = canonical["fragment"]
    line_count = fragment.count("\n") + 1
    char_count = len(fragment)

    state.bursts.append(Burst(
        kind=kind,
        timestamp=canonical["ts"],
        start_idx=cluster.entries[0]["idx"],
        end_idx=cluster.entries[-1]["idx"],
        line_count=line_count,
        char_count=char_count,
        fragment=fragment,
    ))
    state.timeline.append({
        "kind": kind,
        "timestamp": canonical["ts"],
        "line_count": line_count,
        "char_count": char_count,
        "fragment": fragment,
    })

    cluster.entries.clear()


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
        - Replay each event, tracking idle gaps and focus intervals.
        - Buffer adjacent edits (within BURST_CLUSTER_WINDOW of one another)
          into a cluster. A cluster seeds on the first generated edit and
          then absorbs every following edit — generated or not — until a
          larger gap, a focus change, or end of stream closes it.
        - On flush, emit one burst whose range spans the whole cluster and
          whose canonical fragment is the longest generated entry. The
          cluster is then classified by inspecting every entry, so a
          stub-then-clean IDE template still resolves to "ide_action".
        - Assemble the Session dict with totals, snapshots, and a sorted
          timeline.
    """
    state = _SessionState()

    for event in events:
        ts = parse_ts(event["timestamp"])
        _record_idle_gap(state, ts)
        kind = _event_kind(event)

        if kind == "focusStatus":
            _flush_cluster(state, matchers, approved_pastes)
            _handle_focus(event, ts, state)
            continue

        if kind != "edit":
            continue

        if not state.cluster.is_empty and (ts - state.cluster.last_ts) > BURST_CLUSTER_WINDOW:
            _flush_cluster(state, matchers, approved_pastes)

        _apply_edit(event, state)
        _extend_cluster(state, event, ts, _is_generated_edit(event))

    _flush_cluster(state, matchers, approved_pastes)
    _finalize_snapshots(state)
    state.timeline.sort(key=lambda e: e["timestamp"])

    return _build_session(state)


def load_sessions(recording_files: list[Path], problems: Path, approved_pastes_path: Path | None,
                  excluded_file_types: list[str]) -> list[Session]:
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

    recordings = load_recordings(recording_files, problems, excluded_file_types)

    matchers = [build_matcher(events) for _, events in recordings]

    return [
        analyze_events(events, matchers, approved_pastes)
        for _, events in recordings
    ]
