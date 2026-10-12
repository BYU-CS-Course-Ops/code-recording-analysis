from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path, PureWindowsPath
from typing import Sequence

from recan.algorithm import DocumentMatcher, build_matcher
from recan.replay import validated_events
from recan.structure import (
    Annotation,
    Session,
    Snapshot,
)
from recan.utils import (
    language_from_extension,
    load_recordings,
    normalize_newlines,
    parse_ts,
    splice,
    to_utc_iso8601,
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

AUTO_CLOSE_FRAGMENTS = frozenset({"()", "[]", "{}", "''", '""', "'''", '"""'})

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
    entries: list[dict] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)
    cluster: _Cluster = field(default_factory=_Cluster)
    initial_document: str = ""
    document: str = ""
    document_name: str = ""
    document_filename: str = ""
    blur_idx: int | None = None
    blur_ts: datetime | None = None
    prev_ts: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    total_time_unfocused: float = 0.0
    total_time_typing: float = 0.0
    total_pasted_chars: int = 0
    edit_count: int = 0
    total_typing_chars: int = 0
    total_deleted_chars: int = 0
    starts_with_starter_code: bool | None = None
    last_typed_ts: datetime | None = None
    focused: bool = True


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


def _is_initial_snapshot(event: dict, event_idx: int) -> bool:
    """Whether an event is the recorder's non-edit initial document snapshot."""
    return (
        event_idx == 0
        and _event_kind(event) == "edit"
        and event.get("offset", 0) == 0
        and event.get("oldFragment") == event.get("newFragment")
    )


def _initialize_from_snapshot(event: dict, state: _SessionState) -> None:
    """Seed reconstruction from the recorder snapshot without counting an edit."""
    document = event.get("newFragment", "")
    state.initial_document = document
    state.document = document

    doc = event.get("document", "")
    if doc:
        document_name = PureWindowsPath(doc).name
        state.document_filename = document_name
        state.document_name = Path(document_name).stem


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

    if fragment in AUTO_CLOSE_FRAGMENTS and not event.get("oldFragment", ""):
        return True

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

    if fragment in AUTO_CLOSE_FRAGMENTS and not event.get("oldFragment", ""):
        return True

    if CREATE_FUNCTION_PATTERN.fullmatch(fragment):
        return True

    if MAIN_BLOCK_PATTERN.fullmatch(fragment):
        return True

    return False


def _is_approved_paste(fragment: str, approved_pastes: Sequence[str]) -> bool:
    """Whether a generated fragment appears in an approved-fragments source."""
    if not fragment or not approved_pastes:
        return False

    fragment = normalize_newlines(fragment)
    # Check each source independently: an approved match may not span files.
    return any(fragment in source for source in approved_pastes)


def _load_approved_pastes(approved_pastes_path: Path | None) -> list[str]:
    if not approved_pastes_path:
        return []
    path = Path(approved_pastes_path)
    if not path.is_file():
        return []
    return [normalize_newlines(path.read_text(encoding="utf-8"))]


def _load_starter_code(paths: Sequence[Path] | None) -> dict[str, str]:
    starters: dict[str, str] = {}
    for raw_path in paths or ():
        path = Path(raw_path)
        basename = PureWindowsPath(path.name).name
        if basename in starters:
            raise ValueError(f"Duplicate starter-code basename: {basename}")
        try:
            starters[basename] = normalize_newlines(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"Could not read starter-code file {path}: {exc}") from exc
    return starters


def _record_idle_gap(state: _SessionState, ts: datetime) -> None:
    """Record inactivity between the preceding and about-to-be-added entries."""
    if state.start_time is None:
        state.start_time = ts

    state.end_time = ts

    if state.prev_ts is not None:
        gap_seconds = (ts - state.prev_ts).total_seconds()
        if (
            gap_seconds > IDLE_GAP_THRESHOLD_SECONDS
            and state.entries
            and state.entries[-1].get("type") != "initialSnapshot"
        ):
            state.annotations.append({
                "kind": "idle_gap",
                "review_severity": None,
                "timestamp": state.prev_ts,
                "entry_start": len(state.entries) - 1,
                # The caller invokes this immediately before adding the next entry.
                "entry_end": len(state.entries),
                "end_timestamp": ts,
                "duration": gap_seconds,
            })

    state.prev_ts = ts


def _handle_focus(event: dict, ts: datetime, state: _SessionState) -> None:
    """
    Record a focusStatus entry and emit an unfocused annotation when focus returns.

    A blur (focused=False) is remembered until the next focused entry, which
    closes the inclusive annotation range covering the time away.
    """
    focused_event = {
        "timestamp": to_utc_iso8601(event["timestamp"]),
        "type": "focusStatus",
        "focused": bool(event.get("focused")),
    }
    state.entries.append(focused_event)

    focused = event.get("focused")

    if focused is False and state.blur_idx is None:
        state.blur_idx = len(state.entries) - 1
        state.blur_ts = ts
        state.focused = False
        state.last_typed_ts = None
    elif focused is True and state.blur_idx is not None:
        duration = (ts - state.blur_ts).total_seconds()
        state.total_time_unfocused += duration

        focus_idx = len(state.entries) - 1
        # A long blur/focus pair is both inactive and unfocused. Keep the richer
        # unfocused annotation, but do not suppress unrelated gaps near a blur.
        state.annotations = [
            annotation for annotation in state.annotations
            if not (
                annotation["kind"] == "idle_gap"
                and annotation["entry_start"] == state.blur_idx
                and annotation["entry_end"] == focus_idx
                and annotation.get("end_timestamp") == ts
            )
        ]
        state.annotations.append({
            "kind": "unfocused",
            "review_severity": "MEDIUM",
            "timestamp": state.blur_ts,
            "entry_start": state.blur_idx,
            "entry_end": focus_idx,
            "end_timestamp": ts,
            "duration": duration,
        })

        state.blur_idx = None
        state.blur_ts = None
        state.focused = True


def _accum_typing_time(state: _SessionState, ts: datetime, is_generated: bool) -> None:
    """
    Accumulate elapsed time between consecutive typed (non-generated) edits while focused.

    Resets the clock on generated edits or when the editor is blurred, so
    paste/IDE-action time and unfocused time are excluded.
    """
    if is_generated or not state.focused:
        state.last_typed_ts = None
        return

    if state.last_typed_ts is not None:
        elapsed = (ts - state.last_typed_ts).total_seconds()
        if 0 <= elapsed <= IDLE_GAP_THRESHOLD_SECONDS:
            state.total_time_typing += elapsed

    state.last_typed_ts = ts


def _apply_edit(event: dict, state: _SessionState, is_generated: bool) -> None:
    """
    Apply a single edit to state.document and record it in the session's bookkeeping.

    Updates:
        - state.document (splice in the new fragment so snapshots stay current)
        - the canonical entries list and edit_count
        - periodic snapshots (every SNAPSHOT_INTERVAL_EVENTS edits, for timeline scrubbing)

    Pasted characters are counted when the cluster flushes, after IDE fixups
    have been applied to its canonical generated fragment.
    """
    old_fragment = event.get("oldFragment", "")
    new_fragment = event.get("newFragment", "")
    offset = event.get("offset", 0)
    doc = event.get("document", "")

    if not state.document_name:
        # Get the stem of the document path as the document name, need to handle both windows and unix paths
        document_name = PureWindowsPath(doc).name
        state.document_filename = document_name
        document_name = Path(document_name).stem
        state.document_name = document_name


    state.document = splice(state.document, offset, old_fragment, new_fragment)

    edit_event = {
        "timestamp": to_utc_iso8601(event["timestamp"]),
        "type": "edit",
        "offset": offset,
        "oldFragment": old_fragment,
        "newFragment": new_fragment,
    }
    state.entries.append(edit_event)

    state.edit_count += 1

    if state.edit_count % SNAPSHOT_INTERVAL_EVENTS == 0:
        state.snapshots.append({
            "after_idx": len(state.entries) - 1,
            "document_text": state.document,
        })

    state.total_deleted_chars += len(old_fragment)

    if not is_generated:
        state.total_typing_chars += len(new_fragment)


def _extend_cluster(state: _SessionState, event: dict, ts: datetime, is_generated: bool) -> None:
    """
    Add the just-applied edit to the in-flight cluster.

    Only generated edits seed a cluster. Once a cluster is in flight, ANY edit
    within the clustering window joins it. This folds trailing whitespace
    fixups emitted by an IDE template into the generated edit group's range,
    instead of leaving start_idx == end_idx and a highlight that fires before
    the IDE has finished spacing the inserted text.
    """
    if state.cluster.is_empty and not is_generated:
        return

    state.cluster.entries.append({
        "idx": len(state.entries) - 1,
        "ts": ts,
        "event": event,
        "fragment": event.get("newFragment", ""),
        "is_generated": is_generated,
    })


def _final_cluster_fragment(cluster: _Cluster, canonical: dict, document: str) -> str:
    """Return the canonical insertion after later edits in its cluster.

    The recorder can emit a paste followed immediately by IDE indentation or
    template fixups. Track the inserted range through those later edits, then
    read its final text from the already-replayed document.
    """
    start = canonical["event"].get("offset", 0)
    end = start + len(canonical["fragment"])
    after_canonical = False

    for entry in cluster.entries:
        if entry is canonical:
            after_canonical = True
            continue
        if not after_canonical:
            continue

        event = entry["event"]
        edit_start = event.get("offset", 0)
        edit_end = edit_start + len(event.get("oldFragment", ""))
        replacement_end = edit_start + len(event.get("newFragment", ""))
        delta = replacement_end - edit_end

        if edit_end <= start:
            # The edit occurred before the tracked insertion. Insertions exactly
            # at its leading boundary are treated as preceding text.
            start += delta
            end += delta
        elif edit_start >= end:
            # Edits at or after the trailing boundary are outside the insertion.
            continue
        else:
            # The edit overlaps the tracked insertion. Include replacement text
            # and preserve any surviving tracked text on either side.
            start = min(start, edit_start)
            end = max(replacement_end, end + delta)

    start = max(0, min(start, len(document)))
    end = max(start, min(end, len(document)))
    return document[start:end]


def _classify_cluster(
        cluster: _Cluster,
        fragment: str,
        timestamp: datetime,
        matchers: Sequence[DocumentMatcher],
        approved_pastes: Sequence[str],
) -> str:
    """
    Pick the final annotation kind for the whole cluster.

    IDE-action detection scans every entry — a stub-then-clean template often
    leaves the stub matching the regex after its final form no longer does.
    The other rules inspect the canonical insertion after subsequent fixups.

    Precedence:
        - "ide_action":       Any entry in the cluster matches an IDE-template pattern.
        - "approved paste":   Final fragment appears in the approved-fragments file.
        - "internal paste":   Final fragment lived in some earlier snapshot.
        - "unapproved paste": Everything else.
    """
    # A tiny auto-close followed by a real paste must not bless the whole cluster.
    generated = [e for e in cluster.entries if e["is_generated"]]
    if all(_is_ide_action(e["event"]) for e in generated) or any(
        _is_ide_action(e["event"]) and e["fragment"] not in AUTO_CLOSE_FRAGMENTS
        for e in generated
    ):
        return "ide_action"

    if _is_approved_paste(fragment, approved_pastes):
        return "approved_paste"

    if _is_internal_paste(fragment, timestamp, matchers):
        return "internal_paste"

    return "unapproved_paste"


def _flush_cluster(
        state: _SessionState,
        matchers: Sequence[DocumentMatcher],
        approved_pastes: Sequence[str],
) -> None:
    """
    Emit one annotation spanning the in-flight cluster, then clear it.

    The annotation's range covers every entry in the cluster (the IDE-inserted
    text plus any trailing whitespace fixups). Its fragment is the longest
    generated insertion after later cluster edits have transformed it.

    Clusters with no generated edits (just trailing typed events that never
    got seeded) are silently dropped.
    """
    cluster = state.cluster
    if not cluster.has_generated:
        cluster.entries.clear()
        return

    generated = [e for e in cluster.entries if e["is_generated"]]
    canonical = max(generated, key=lambda e: len(e["fragment"]))
    fragment = _final_cluster_fragment(cluster, canonical, state.document)
    kind = _classify_cluster(
        cluster, fragment, canonical["ts"], matchers, approved_pastes)
    line_count = fragment.count("\n") + 1
    char_count = len(fragment)
    if kind != "ide_action":
        state.total_pasted_chars += char_count

    state.annotations.append({
        "kind": kind,
        "review_severity": "LOW" if kind != "unapproved_paste" else "HIGH",
        "timestamp": canonical["ts"],
        "entry_start": cluster.entries[0]["idx"], "entry_end": cluster.entries[-1]["idx"],
        "source_entry": canonical["idx"],
        "line_count": line_count, "char_count": char_count, "fragment": fragment,
    })

    cluster.entries.clear()


def _finalize_snapshots(state: _SessionState) -> None:
    """
    Ensure a snapshot exists at the final event index.

    The periodic snapshot only fires every SNAPSHOT_INTERVAL_EVENTS edits, so the
    final document state may be missed; this appends one if needed.
    """
    if not state.entries:
        return

    last = state.snapshots[-1] if state.snapshots else None
    if last is None or last["after_idx"] != len(state.entries) - 1:
        snapshot = {
            "after_idx": len(state.entries) - 1,
            "document_text": state.document,
        }
        state.snapshots.append(snapshot)


def annotation_counts(session: Session) -> dict[str, int]:
    """Return zero-filled reporting counts derived solely from annotations."""
    annotations = session["annotations"]
    counts = {"ide_action": 0, "approved_paste": 0, "internal_paste": 0,
              "unapproved_paste": 0, "unfocused": 0, "idle_gap": 0}
    for annotation in annotations:
        if annotation["kind"] in counts:
            counts[annotation["kind"]] += 1
    return counts


def max_review_severity(annotations: Sequence[Annotation]) -> str | None:
    """Return the highest review severity present in annotations."""
    rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
    return max((a["review_severity"] for a in annotations if a["review_severity"]),
               key=rank.get, default=None)


def _build_session(state: _SessionState) -> Session:
    """
    Assemble the final Session dict from metrics, entries, annotations, and snapshots.
    """
    if state.start_time and state.end_time:
        total_time = (state.end_time - state.start_time).total_seconds()
    else:
        total_time = 0.0

    review_severity = max_review_severity(state.annotations)

    return {
        "document": state.document_name,
        "language": language_from_extension(state.document_filename, state.document),
        "start_time": state.start_time,
        "end_time": state.end_time,
        "total_time": total_time,
        "total_time_unfocused": state.total_time_unfocused,
        "total_time_typing": state.total_time_typing,
        "total_edits": state.edit_count,
        "total_chars": len(state.document),
        "total_typed_chars": state.total_typing_chars,
        "total_pasted_chars": state.total_pasted_chars,
        "total_deleted_chars": state.total_deleted_chars,
        "starts_with_starter_code": state.starts_with_starter_code,
        "review_severity": review_severity,
        "entries": state.entries,
        "annotations": sorted(state.annotations, key=lambda a: a["timestamp"]),
        "snapshots": state.snapshots,
    }


def analyze_events(
        events: list[dict],
        matchers: Sequence[DocumentMatcher] = (),
        approved_pastes: Sequence[str] | str | None = None,
        starter_code: dict[str, str] | None = None,
) -> Session:
    """
    Walk a sequence of recording events and return a fully-populated Session.

    Pipeline:
        - Replay each input event, tracking idle gaps and focus annotations.
        - Buffer adjacent edits within the clustering window into a generated
          edit cluster. A cluster seeds on the first generated edit and then
          absorbs every following edit — generated or not — until a larger
          gap, a focus change, or end of stream closes it.
        - On flush, emit one annotation whose range spans the whole cluster and
          whose canonical fragment is the longest generated entry. The
          cluster is then classified by inspecting every entry, so a
          stub-then-clean IDE template still resolves to "ide_action".
        - Assemble the Session dict with metrics, snapshots, entries, and annotations.
    """
    state = _SessionState()
    # Normalize the legacy single-string form once. Keep a sequence through
    # classification so sources remain independent and strings are not
    # accidentally iterated character by character.
    approved_sources: Sequence[str] = (
        (approved_pastes,) if isinstance(approved_pastes, str)
        else tuple(approved_pastes or ())
    )

    for event_idx, event in enumerate(validated_events(events)):
        kind = _event_kind(event)
        is_initial_snapshot = _is_initial_snapshot(event, event_idx)

        if kind in {"recordingIssue", "documentSnapshot", "unreliableEdit"}:
            _flush_cluster(state, matchers, approved_sources)
            ts = parse_ts(event["timestamp"])
            _record_idle_gap(state, ts)
            anchor = len(state.entries)
            if kind == "documentSnapshot":
                _initialize_from_snapshot({**event, "newFragment": event["document_text"]}, state)
                state.entries.append({
                    "type": kind, "timestamp": to_utc_iso8601(ts),
                    "document_text": state.document,
                })
                state.snapshots.append({"after_idx": anchor, "document_text": state.document})
                if state.annotations and state.annotations[-1].get("action") == "resynchronized":
                    state.annotations[-1]["entry_end"] = anchor
            elif kind == "recordingIssue":
                state.entries.append({"type": kind, "timestamp": to_utc_iso8601(ts)})
                state.annotations.append({
                    "kind": "recording_issue", "review_severity": "MEDIUM",
                    "timestamp": ts, "entry_start": anchor, "entry_end": anchor,
                    **{key: event[key] for key in ("event_index", "action", "message", "skipped_edits")},
                })
            else:
                state.entries.append({"type": kind, "timestamp": to_utc_iso8601(ts)})
            continue

        # Recorder refreshes can emit a whole-document replacement whose old
        # and new text are identical. It is not an edit and must not affect
        # timing, metrics, replay entries, or generated-edit classification.
        if (
            kind == "edit"
            and not is_initial_snapshot
            and event.get("oldFragment", "") == event.get("newFragment", "")
        ):
            continue

        ts = parse_ts(event["timestamp"])

        # Only operations that become entries can anchor an idle interval.
        if is_initial_snapshot or kind in {"focusStatus", "edit"}:
            _record_idle_gap(state, ts)

        if is_initial_snapshot:
            _initialize_from_snapshot(event, state)
            if starter_code is not None:
                starter = starter_code.get(state.document_filename)
                state.starts_with_starter_code = (
                    normalize_newlines(state.initial_document).startswith(normalize_newlines(starter))
                    if starter is not None else None
                )
            state.entries.append({
                "timestamp": to_utc_iso8601(event["timestamp"]),
                "type": "initialSnapshot",
                "document_text": state.initial_document,
            })
            continue

        if kind == "focusStatus":
            _flush_cluster(state, matchers, approved_sources)
            _handle_focus(event, ts, state)
            continue

        if kind != "edit":
            continue

        if not state.cluster.is_empty and (ts - state.cluster.last_ts) > BURST_CLUSTER_WINDOW:
            _flush_cluster(state, matchers, approved_sources)

        is_generated = _is_generated_edit(event)
        _accum_typing_time(state, ts, is_generated)
        _apply_edit(event, state, is_generated)
        _extend_cluster(state, event, ts, is_generated)

    _flush_cluster(state, matchers, approved_sources)
    _finalize_snapshots(state)
    if starter_code is not None and state.starts_with_starter_code is None:
        state.starts_with_starter_code = False if state.document_filename in starter_code else None
    if state.starts_with_starter_code is False:
        anchor = 0 if state.entries else None
        timestamp = parse_ts(state.entries[0]["timestamp"]) if state.entries else state.start_time
        if timestamp is not None:
            state.annotations.append({
                "kind": "starter_code_mismatch", "review_severity": "HIGH",
                "timestamp": timestamp, "entry_start": anchor, "entry_end": anchor,
            })

    return _build_session(state)


def load_sessions(recording_files: list[Path], problems: Path, approved_pastes_path: Path | None,
                  excluded_file_types: list[str], starter_code_paths: Sequence[Path] | None = None) -> list[Session]:
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
    starters = _load_starter_code(starter_code_paths)
    # Every starter is approved, even when no recording uses its basename.
    approved_pastes.extend(starters.values())

    recordings = load_recordings(recording_files, problems, excluded_file_types)

    matchers = [build_matcher(events) for _, events in recordings]

    return [
        analyze_events(events, matchers, approved_pastes, starters)
        for _, events in recordings
    ]
