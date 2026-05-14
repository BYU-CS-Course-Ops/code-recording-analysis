from datetime import datetime, timedelta

from recan.structure import (
    Burst,
    FocusInterval,
    IdleGap,
    Session,
    Snapshot,
    TimelineEntry
)
from recan.utils import (
    apply_edit,
    event_kind,
    is_generated_edit,
    is_ide_action,
    language_from_extension,
    parse_ts,
)

BURST_GROUP_WINDOW_MS = 100
IDLE_GAP_THRESHOLD_SECONDS = 5.0
SNAPSHOT_INTERVAL_EVENTS = 200


def analyze_inputs(inputs: list[dict], approved_fragments: list[str] | None) -> Session:
    """Walk the (already filtered) event stream once and produce a Session.

    Single source of truth for both the markdown summary and the HTML player.
    Inputs are assumed to be chronological and pre-filtered by file type
    (filtering happens in `utils.load_recording`).

    Move detection: a generated insert whose fragment matches a previously
    deleted fragment (>1 char) is treated as a move and skipped. The previous
    paste-then-delete retroactive cancellation is intentionally not implemented.
    """
    events: list[dict] = []
    focus_intervals: list[FocusInterval] = []
    idle_gaps: list[IdleGap] = []
    snapshots: list[Snapshot] = []
    generated_entries: list[dict] = []
    timeline: list[TimelineEntry] = []

    document = ""
    document_name = ""
    blur_idx: int | None = None
    blur_ts: datetime | None = None
    prev_ts: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    total_time_unfocused = 0.0
    deleted_fragments: dict[str, int] = {}
    edit_count = 0

    for event in inputs:
        ts = parse_ts(event["timestamp"])
        if start_time is None:
            start_time = ts
        end_time = ts

        if prev_ts is not None:
            gap = (ts - prev_ts).total_seconds()
            if gap > IDLE_GAP_THRESHOLD_SECONDS:
                idle_gaps.append({
                    "after_idx": len(events) - 1,
                    "duration": gap,
                })
        prev_ts = ts

        kind = event_kind(event)

        if kind == "focusStatus":
            focused = event.get("focused")
            events.append({
                "timestamp": event["timestamp"],
                "type": "focusStatus",
                "focused": bool(focused),
            })
            if focused is False and blur_idx is None:
                blur_idx = len(events) - 1
                blur_ts = ts
            elif focused is True and blur_idx is not None and blur_ts is not None:
                duration = (ts - blur_ts).total_seconds()
                total_time_unfocused += duration
                focus_intervals.append({
                    "blur_idx": blur_idx,
                    "focus_idx": len(events) - 1,
                    "duration": duration,
                })
                timeline.append({
                    "kind": "focus",
                    "timestamp": ts,
                    "duration": duration,
                })
                blur_idx = None
                blur_ts = None

        elif kind == "edit":
            old_fragment = event.get("oldFragment", "")
            new_fragment = event.get("newFragment", "")
            offset = event.get("offset", 0)
            doc = event.get("document", "")
            if document_name == "" and doc:
                document_name = doc
            document = apply_edit(document, offset, old_fragment, new_fragment)

            events.append({
                "timestamp": event["timestamp"],
                "type": "edit",
                "offset": offset,
                "oldFragment": old_fragment,
                "newFragment": new_fragment,
            })
            edit_count += 1

            if len(old_fragment) > 1:
                deleted_fragments[old_fragment] = deleted_fragments.get(old_fragment, 0) + 1

            if is_generated_edit(event):
                if deleted_fragments.get(new_fragment, 0) > 0:
                    deleted_fragments[new_fragment] -= 1
                else:
                    generated_entries.append({
                        "timestamp": ts,
                        "event_idx": len(events) - 1,
                        "line_count": new_fragment.count("\n") + 1,
                        "char_count": len(new_fragment),
                        "fragment": new_fragment,
                    })

            if edit_count % SNAPSHOT_INTERVAL_EVENTS == 0:
                snapshots.append({
                    "after_idx": len(events) - 1,
                    "document_text": document,
                })

    if events:
        last = snapshots[-1] if snapshots else None
        if last is None or last["after_idx"] != len(events) - 1:
            snapshots.append({
                "after_idx": len(events) - 1,
                "document_text": document,
            })

    bursts: list[Burst] = []
    window = timedelta(milliseconds=BURST_GROUP_WINDOW_MS)
    group: list[dict] = []

    def flush_group():
        if not group:
            return

        """
        IDE-Action Heuristic: 
        
            - `create function` — Since pycharm generates multiple fragments when executing this action as long as one 
              of them matches the CREATE_FUNCTION_PATTERN, we label the whole burst as an ide_action
              
            - `main block` — Similarly, since pycharm generates multiple fragments when executing this action as long 
              as one of them matches the MAIN_BLOCK_PATTERN, we label the whole burst as an ide_action
        """

        if is_ide_action(group):
            kept = max(group, key=lambda e: e["char_count"])
            kind_label = "ide_action"
            line_count = kept["line_count"]
            char_count = kept["char_count"]

        else:
            kept = max(group, key=lambda e: e["char_count"])
            kind_label = "approved paste" if any(
                e["fragment"] in approved_fragments for e in group) else "unapproved paste"
            line_count = sum(e["line_count"] for e in group)
            char_count = sum(e["char_count"] for e in group)

        bursts.append(Burst(
            kind=kind_label,
            timestamp=kept["timestamp"],
            start_idx=group[0]["event_idx"],
            end_idx=group[-1]["event_idx"],
            line_count=line_count,
            char_count=char_count,
            fragment=kept["fragment"],
        ))
        group.clear()

    for entry in generated_entries:
        if not group:
            group.append(entry)
            continue
        if entry["timestamp"] - group[-1]["timestamp"] <= window:
            group.append(entry)
        else:
            flush_group()
            group.append(entry)
    flush_group()

    for burst in bursts:
        timeline.append({
            "kind": burst["kind"],
            "timestamp": burst["timestamp"],
            "line_count": burst["line_count"],
            "char_count": burst["char_count"],
            "fragment": burst["fragment"],
        })
    timeline.sort(key=lambda e: e["timestamp"])

    total_time = (end_time - start_time).total_seconds() if start_time and end_time else 0.0
    total_ide_actions = sum(1 for b in bursts if b["kind"] == "ide_action")
    total_unapproved_pastes = sum(1 for b in bursts if b["kind"] == "unapproved paste")
    total_approved_pastes = sum(1 for b in bursts if b["kind"] == "approved paste")

    return {
        "document": document_name,
        "language": language_from_extension(document_name),
        "start_time": start_time,
        "end_time": end_time,
        "total_time": total_time,
        "total_time_unfocused": total_time_unfocused,
        "total_edits": edit_count,
        "total_unapproved_pastes": total_unapproved_pastes,
        "total_approved_pastes": total_approved_pastes,
        "total_ide_actions": total_ide_actions,
        "total_generated_events": total_ide_actions + total_unapproved_pastes + total_approved_pastes,
        "events": events,
        "focus_intervals": focus_intervals,
        "idle_gaps": idle_gaps,
        "bursts": bursts,
        "snapshots": snapshots,
        "timeline": timeline,
    }
