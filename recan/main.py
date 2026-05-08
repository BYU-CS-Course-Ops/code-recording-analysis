import json

import jinja2
from pathlib import Path
from argparse import ArgumentParser
from typing import Literal, TypedDict
from datetime import datetime, timedelta

from recan.utils import (
    apply_edit,
    event_kind,
    format_duration,
    format_ts,
    is_generated_edit,
    language_from_extension,
    load_recording,
    parse_ts,
)


BURST_GROUP_WINDOW_MS = 100
IDLE_GAP_THRESHOLD_SECONDS = 5.0
SNAPSHOT_INTERVAL_EVENTS = 200


class Input(TypedDict):
    type: Literal['focusStatus', 'edit']
    editor: str
    recorderVersion: str
    timestamp: str


class EditInput(Input):
    document: str
    offset: int
    oldFragment: str
    newFragment: str


class FocusStatusInput(Input):
    focused: bool


def _collapse_ide_action_bursts(timeline: list[dict], window_ms: int) -> list[dict]:
    """Collapse runs of adjacent ``generated`` timeline entries whose
    timestamps are within ``window_ms`` of each other into a single entry,
    keeping the last entry in the run. Non-generated entries break the chain
    and pass through unchanged.

    The kept entry's ``kind`` is reclassified: a group of >1 tightly-packed
    events is an ``ide_action`` (e.g. PyCharm "Generate constructor" fires
    several edits per user action), while a singleton is a ``paste`` (one
    chunked insert with no neighbors — clipboard or AI completion).
    """
    out: list[dict] = []
    group: list[dict] = []
    window = timedelta(milliseconds=window_ms)

    def flush():
        if group:
            kept = group[-1]
            kept["kind"] = "ide_action" if len(group) > 1 else "paste"
            out.append(kept)
            group.clear()

    for entry in timeline:
        if entry["kind"] != "generated":
            flush()
            out.append(entry)
            continue

        if not group:
            group.append(entry)
            continue

        if entry["timestamp"] - group[-1]["timestamp"] <= window:
            group.append(entry)

        else:
            flush()
            group.append(entry)

    flush()
    return out


def _build_playback_bundle(inputs: list[dict]) -> dict:
    """Build the JSON bundle the HTML player consumes.

    Walks raw events once, keeping every edit (including move-detected re-pastes
    that ``analyze_inputs`` filters from the burst count). Computes:
      - focus_intervals: paired blur -> next focus, with duration
      - idle_gaps: gaps between adjacent events longer than IDLE_GAP_THRESHOLD_SECONDS
      - bursts: via ``_collapse_ide_action_bursts`` over generated edits
      - snapshots: full document text every SNAPSHOT_INTERVAL_EVENTS edits
      - summary: totals
    """
    events: list[dict] = []
    focus_intervals: list[dict] = []
    idle_gaps: list[dict] = []
    snapshots: list[dict] = []
    generated_timeline: list[dict | None] = []

    document = ""
    document_name = ""
    blur_idx: int | None = None
    blur_ts: datetime | None = None
    prev_ts: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    total_unfocused = 0.0
    deleted_fragments: dict[str, int] = {}
    pasted_fragments: dict[str, list[int]] = {}
    edit_count = 0

    for event in inputs:
        doc = event.get("document", "")

        ts = parse_ts(event["timestamp"])
        if start_time is None:
            start_time = ts
        end_time = ts

        kind = event_kind(event)

        if prev_ts is not None:
            gap = (ts - prev_ts).total_seconds()
            if gap > IDLE_GAP_THRESHOLD_SECONDS:
                idle_gaps.append({
                    "after_idx": len(events) - 1,
                    "duration_seconds": gap,
                })
        prev_ts = ts

        if kind == "focusStatus":
            focused = event.get("focused")
            entry = {
                "timestamp": event["timestamp"],
                "type": "focusStatus",
                "focused": bool(focused),
            }
            events.append(entry)
            if focused is False and blur_idx is None:
                blur_idx = len(events) - 1
                blur_ts = ts
            elif focused is True and blur_idx is not None and blur_ts is not None:
                duration = (ts - blur_ts).total_seconds()
                total_unfocused += duration
                focus_intervals.append({
                    "blur_idx": blur_idx,
                    "focus_idx": len(events) - 1,
                    "duration_seconds": duration,
                })
                blur_idx = None
                blur_ts = None

        elif kind == "edit":
            old_fragment = event.get("oldFragment", "")
            new_fragment = event.get("newFragment", "")
            offset = event.get("offset", 0)
            if document_name == "" and doc:
                document_name = doc
            document = apply_edit(document, offset, old_fragment, new_fragment)

            entry = {
                "timestamp": event["timestamp"],
                "type": "edit",
                "offset": offset,
                "oldFragment": old_fragment,
                "newFragment": new_fragment,
            }
            events.append(entry)
            edit_count += 1

            # mirror analyze_inputs's deleted-fragment + generated-edit bookkeeping,
            # including the retroactive "paste then deleted = discarded" cancel
            cancelled_paste = False
            if old_fragment and pasted_fragments.get(old_fragment):
                generated_timeline[pasted_fragments[old_fragment].pop(0)] = None
                cancelled_paste = True

            if not cancelled_paste and len(old_fragment) > 1:
                deleted_fragments[old_fragment] = deleted_fragments.get(old_fragment, 0) + 1

            if is_generated_edit(event):
                if deleted_fragments.get(new_fragment, 0) > 0:
                    deleted_fragments[new_fragment] -= 1
                else:
                    generated_timeline.append({
                        "kind": "generated",
                        "timestamp": ts,
                        "event_idx": len(events) - 1,
                        "line_count": new_fragment.count("\n") + 1,
                        "char_count": len(new_fragment),
                    })
                    pasted_fragments.setdefault(new_fragment, []).append(len(generated_timeline) - 1)

            if edit_count % SNAPSHOT_INTERVAL_EVENTS == 0:
                snapshots.append({
                    "after_idx": len(events) - 1,
                    "document_text": document,
                })

    # always include a final snapshot so scrubbing to the end is fast
    if events:
        last = snapshots[-1] if snapshots else None
        if last is None or last["after_idx"] != len(events) - 1:
            snapshots.append({
                "after_idx": len(events) - 1,
                "document_text": document,
            })

    generated_timeline = [e for e in generated_timeline if e is not None]

    bursts: list[dict] = []
    window = timedelta(milliseconds=BURST_GROUP_WINDOW_MS)
    group: list[dict] = []

    def _flush_group():
        if not group:
            return
        bursts.append({
            "kind": "ide_action" if len(group) > 1 else "paste",
            "start_idx": group[0]["event_idx"],
            "end_idx": group[-1]["event_idx"],
            "lines": sum(e["line_count"] for e in group),
            "chars": sum(e["char_count"] for e in group),
        })
        group.clear()

    for entry in generated_timeline:
        if not group:
            group.append(entry)
            continue
        if entry["timestamp"] - group[-1]["timestamp"] <= window:
            group.append(entry)
        else:
            _flush_group()
            group.append(entry)
    _flush_group()

    duration = (end_time - start_time).total_seconds() if start_time and end_time else 0.0

    return {
        "metadata": {
            "document": document_name,
            "language": language_from_extension(document_name),
            "start_time": start_time.isoformat().replace("+00:00", "Z") if start_time else "",
            "end_time": end_time.isoformat().replace("+00:00", "Z") if end_time else "",
            "duration_seconds": duration,
        },
        "events": events,
        "bursts": bursts,
        "idle_gaps": idle_gaps,
        "focus_intervals": focus_intervals,
        "snapshots": snapshots,
        "summary": {
            "total_seconds": duration,
            "unfocused_seconds": total_unfocused,
            "burst_count": len(bursts),
            "edit_count": edit_count,
            "generated_count": len(bursts),
            "ide_action_count": sum(1 for b in bursts if b["kind"] == "ide_action"),
            "paste_count": sum(1 for b in bursts if b["kind"] == "paste"),
        },
    }


PLAYER_DIR = Path(__file__).resolve().parent / "player"


def _embed_safe_json(bundle: dict) -> str:
    """JSON-encode a bundle for safe embedding in a <script> tag.

    Two specific dangers when embedding JSON in HTML inside a <script> block:
    1. The substring ``</script`` ends the script element early. Escape ``</`` to ``<\\/``.
    2. U+2028 / U+2029 are valid in JSON but illegal in JS string literals.
    """
    text = json.dumps(bundle, ensure_ascii=False, default=str)
    text = text.replace("</", "<\\/")
    text = text.replace(" ", "\\u2028").replace(" ", "\\u2029")
    return text


def _render_player_html(bundle: dict) -> str:
    """Render the static HTML player by inlining the bundle and assets via Jinja2."""
    template_text = (PLAYER_DIR / "template.html").read_text(encoding="utf-8")
    css = (PLAYER_DIR / "player.css").read_text(encoding="utf-8")
    js = (PLAYER_DIR / "player.js").read_text(encoding="utf-8")
    highlight = (PLAYER_DIR / "highlight.min.js").read_text(encoding="utf-8")

    env = jinja2.Environment(autoescape=False, keep_trailing_newline=True)
    template = env.from_string(template_text)
    return template.render(
        document_name=bundle["metadata"].get("document", "recording"),
        css=css,
        js=js,
        highlight=highlight,
        bundle=_embed_safe_json(bundle),
    )


def analyze_inputs(inputs: list[dict]) -> dict:
    """Single pass: walk events in order, building the timeline as we go.

    Events arrive chronologically, so timeline entries are already sorted —
    no second pass needed. Focus duration is computed on the fly by pairing
    each `focused: false` with the next `focused: true`.

    Deleted fragments longer than 1 char are tracked as a multiset so that a
    later "generated" burst matching one is treated as a move/re-paste and
    excluded from the generated count.
    """
    timeline: list[dict | None] = []
    total_time_unfocused = 0.0
    total_edits = 0
    unfocused_since: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    deleted_fragments: dict[str, int] = {}
    pasted_fragments: dict[str, list[int]] = {}

    for event in inputs:
        ts = parse_ts(event["timestamp"])
        if start_time is None:
            start_time = ts
        end_time = ts

        kind = event_kind(event)
        if kind == "focusStatus":
            if event.get("focused") is False and unfocused_since is None:
                unfocused_since = ts
            elif event.get("focused") is True and unfocused_since is not None:
                duration = (ts - unfocused_since).total_seconds()
                total_time_unfocused += duration
                timeline.append({
                    "kind": "focus",
                    "timestamp": ts,
                    "duration": duration,
                })
                unfocused_since = None

        elif kind == "edit":
            total_edits += 1
            old_fragment = event.get("oldFragment", "")

            cancelled_paste = False
            if old_fragment and pasted_fragments.get(old_fragment):
                timeline[pasted_fragments[old_fragment].pop(0)] = None
                cancelled_paste = True

            if not cancelled_paste and len(old_fragment) > 1:
                deleted_fragments[old_fragment] = deleted_fragments.get(old_fragment, 0) + 1

            if is_generated_edit(event):
                fragment = event["newFragment"]
                if deleted_fragments.get(fragment, 0) > 0:
                    deleted_fragments[fragment] -= 1
                    continue

                timeline.append({
                    "kind": "generated",
                    "timestamp": ts,
                    "line_count": fragment.count("\n") + 1,
                    "char_count": len(fragment),
                    "fragment": fragment,
                })
                pasted_fragments.setdefault(fragment, []).append(len(timeline) - 1)

    timeline = [e for e in timeline if e is not None]
    timeline = _collapse_ide_action_bursts(timeline, BURST_GROUP_WINDOW_MS)
    total_ide_actions = sum(1 for entry in timeline if entry["kind"] == "ide_action")
    total_pastes = sum(1 for entry in timeline if entry["kind"] == "paste")

    total_time = (end_time - start_time).total_seconds() if start_time and end_time else 0.0

    return {
        "start_time": start_time,
        "total_time": total_time,
        "total_time_unfocused": total_time_unfocused,
        "total_ide_actions": total_ide_actions,
        "total_pastes": total_pastes,
        "total_generated_events": total_ide_actions + total_pastes,
        "total_edits": total_edits,
        "timeline": timeline,
    }


def _format_entry(entry: dict) -> str:
    stamp = format_ts(entry["timestamp"])
    if entry["kind"] == "focus":
        return f"  [{stamp}] Lost focus for {format_duration(entry['duration'])}"
    label = "IDE Action" if entry["kind"] == "ide_action" else "Paste"
    body = "\n".join(f"      | {line}" for line in entry["fragment"].splitlines() or [""])
    return (
        f"  [{stamp}] {label} "
        f"({entry['line_count']} lines, {entry['char_count']} chars):\n{body}"
    )


def print_timeline(results: dict) -> str:
    total = results["total_time"]
    unfocused = results["total_time_unfocused"]
    entries = "\n".join(_format_entry(e) for e in results["timeline"])
    return(
        f"Total Time:        {format_duration(total)}\n"
        f"Time Focused:      {format_duration(total - unfocused)}\n"
        f"Time Unfocused:    {format_duration(unfocused)}\n"
        f"IDE Actions:       {results['total_ide_actions']}\n"
        f"Pastes:            {results['total_pastes']}\n"
        f"\n"
        f"Timeline of Key Moments:\n"
        f"  [{format_ts(results['start_time'])}] Recording Started"
        f"{('\n' + entries) if entries else ''}"
    )


def main(recording_file: Path, excluded_file_types: list[str], output:Path = None, _json: bool = False, view: bool = False):
    inputs = load_recording(recording_file, excluded_file_types)

    results = analyze_inputs(inputs)

    if _json:
        results = json.dumps(results, default=str, indent=4)
    else:
        results = print_timeline(results)

    if output:
        with open(output, "w") as f:
            f.write(results)
    else:
        print(results)

    if view:
        bundle = _build_playback_bundle(inputs)
        html = _render_player_html(bundle)
        stem = recording_file.with_suffix("") if recording_file.suffix == ".gz" else recording_file
        html_path = stem.with_suffix(stem.suffix + ".html") if stem.suffix else stem.with_suffix(".html")
        html_path.write_text(html, encoding="utf-8")
        print(f"Wrote playback HTML to {html_path}")



def entry():
    parser = ArgumentParser(description="Analyze IDE recording files.")
    parser.add_argument("recording_file", type=Path, help="Path to the recording file (JSONL or gzipped JSONL).")
    parser.add_argument("--exclude", nargs="*", default=[], help="List of file extensions to exclude (e.g. .html .md).")
    parser.add_argument("--output", type=Path, help="Path to write the analysis results (defaults to stdout).")
    parser.add_argument("--json", action="store_true", help="Output the analysis results as JSON instead of human-readable text.")
    parser.add_argument("--view", action="store_true", help="Generate a visualization of the timeline.")

    args = parser.parse_args()

    main(args.recording_file, args.exclude, args.output, args.json, args.view)

if __name__ == "__main__":
    entry()
