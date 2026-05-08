# `recan` Package Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `recan/main.py` into focused modules (`utils`, `structure`, `session`, `formaters`, `viewer`) with one unified `Session` walker, while keeping `main.py` to ~50 lines of CLI + orchestration.

**Architecture:** Single-pass walker `session.analyze_inputs(inputs) -> Session` produces a TypedDict superset that both the Markdown summary (`formaters.py`) and the HTML player (`viewer.py`) derive from. File-type filtering moves to `utils.load_recording`. Two intentional behavior changes: default text output upgrades to a Markdown summary rendered via Jinja, and the paste-then-delete retroactive cancellation is dropped (delete-then-paste move detection is kept).

**Tech Stack:** Python 3.13+, Jinja2, Poetry. No new dependencies.

**Verification model:** This is a refactor — no new unit tests are added. Each task verifies behavior by running the CLI on a captured baseline sample (`samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz`) and diffing outputs against snapshots taken in Task 0. Expected differences are called out per task.

**Spec:** `docs/superpowers/specs/2026-05-08-recan-package-refactor-design.md`

---

## File Structure

```
recan/
├── __init__.py             # unchanged
├── VERSION                 # unchanged
├── main.py                 # SHRINKS: CLI parsing + orchestration only
├── session.py              # NEW: analyze_inputs walker → Session
├── structure.py            # FILLED IN: existing input TypedDicts + Session
├── utils.py                # FILLED IN: pure helpers + load_recording
├── formaters.py            # FILLED IN: render_markdown + render_json
├── timeline.md.jinja       # FILLED IN: Markdown template
├── viewer.py               # FILLED IN: build_player_html, write_player_html
├── player/                 # untouched
└── analyze_assignment.py   # MINIMAL: import paths + use load_recording
```

Each module's responsibility, full Session shape, and dependency graph are in the spec. Refer to it for design rationale; this plan only covers the mechanics of moving code.

---

## Conventions used throughout this plan

- **Sample recording for verification:** `SAMPLE=samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz`
- **Baselines directory:** `/tmp/recan-baseline/` (machine-local, not committed). All baseline files captured in Task 0.
- **Run mode:** `poetry run recan ...` for the CLI. `poetry run python -m recan.analyze_assignment ...` for the assignment script.
- **Diff command:** `diff <baseline> <current>` — empty output means parity.
- **Commit format:** `refactor: <one-line summary>` per Task. One commit per task unless a task explicitly says otherwise.

---

## Task 0: Capture pre-refactor baselines

**Files:**
- Create: `/tmp/recan-baseline/text.out`
- Create: `/tmp/recan-baseline/json.out`
- Create: `/tmp/recan-baseline/player.html`
- Create: `/tmp/recan-baseline/assignment-csvs/` (directory)

- [ ] **Step 1: Create the baseline directory**

```bash
mkdir -p /tmp/recan-baseline/assignment-csvs
```

- [ ] **Step 2: Capture text output**

```bash
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html > /tmp/recan-baseline/text.out
```

Expected: file is ~50 lines starting with `Total Time:`, ending with the timeline of focus losses + bursts.

- [ ] **Step 3: Capture JSON output**

```bash
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html --json > /tmp/recan-baseline/json.out
```

Expected: valid JSON with keys `start_time`, `total_time`, `total_time_unfocused`, `total_ide_actions`, `total_pastes`, `total_generated_events`, `total_edits`, `timeline`. Validate with:

```bash
python -c "import json; json.load(open('/tmp/recan-baseline/json.out'))"
```

- [ ] **Step 4: Capture HTML player output**

```bash
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html --view
cp samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.html /tmp/recan-baseline/player.html
```

Expected: HTML file ~hundreds of KB containing inlined JSON bundle, CSS, JS, and highlight.js.

- [ ] **Step 5: Capture assignment CSV baseline**

The `analyze_assignment.py` script has a broken import (`from main import` instead of `from recan.main import`). It also uses `Path.cwd()` for output. Capture the baseline by running it from the repo root with PYTHONPATH set:

```bash
PYTHONPATH=recan poetry run python recan/analyze_assignment.py --folder samples/homework-1b-functions --out-dir /tmp/recan-baseline/assignment-csvs
```

Expected: `/tmp/recan-baseline/assignment-csvs/` contains one or more `<problem>_analysis.csv` files (e.g. `boxes_analysis.csv`, `one_tree_analysis.csv`).

- [ ] **Step 6: List captured baselines for sanity**

```bash
ls -la /tmp/recan-baseline/ /tmp/recan-baseline/assignment-csvs/
```

Expected: all four files (`text.out`, `json.out`, `player.html`) plus the CSV directory exist and are non-empty.

- [ ] **Step 7: No commit**

Task 0 produces no repo changes — baselines live in `/tmp` only.

---

## Task 1: Move pure helpers and add `load_recording` to `utils.py`

**Files:**
- Modify: `recan/utils.py` (currently empty)
- Modify: `recan/main.py` (replace helper definitions with imports)

This task moves all stateless helpers out of `main.py` and adds a new `load_recording` function that owns both file I/O and excluded-file-type filtering. Constants used only by these helpers move with them.

- [ ] **Step 1: Write the new `utils.py` content**

Replace the empty `recan/utils.py` with:

```python
import gzip
import json

from datetime import datetime
from pathlib import Path


GENERATED_MIN_CHARS_SINGLE_LINE = 20


def parse_ts(value: str) -> datetime:
    """Tolerant ISO timestamp parser.

    Python's fromisoformat tolerates "Z" only from 3.11+, and chokes on
    nanosecond precision (e.g. "...916473800Z"). Trim to microseconds.
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    head, sep, tz = value.partition("+")
    if "." in head:
        whole, frac = head.split(".")
        head = f"{whole}.{frac[:6]}"
    return datetime.fromisoformat(head + sep + tz)


def format_duration(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_ts(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def event_kind(event: dict) -> str:
    """Older recordings omit "type" — infer from which fields are present."""
    explicit = event.get("type")
    if explicit:
        return explicit
    if "focused" in event:
        return "focusStatus"
    if "newFragment" in event:
        return "edit"
    return "unknown"


def is_generated_edit(event: dict) -> bool:
    """Heuristic for "this insert wasn't typed character-by-character."

    Filters out IDE word-completion (e.g. PyCharm emits ``"n "`` when a
    completion fires after typing ``n`` + space) by requiring the fragment
    to be either multi-line or substantively long on a single line.
    """
    fragment = event.get("newFragment", "")
    if not fragment.strip():
        return False
    if not (any(c == ' ' for c in fragment) and any(c != ' ' for c in fragment)):
        return False
    if "\n" in fragment:
        return True
    return len(fragment) >= GENERATED_MIN_CHARS_SINGLE_LINE


def apply_edit(document: str, offset: int, old_fragment: str, new_fragment: str) -> str:
    """Apply a single edit to the in-memory document string."""
    end = offset + len(old_fragment)
    return document[:offset] + new_fragment + document[end:]


def language_from_extension(document: str) -> str:
    """Map document filename to a highlight.js language name."""
    ext = Path(document).suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".c": "c",
        ".h": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".hpp": "cpp",
        ".java": "java",
        ".go": "go",
        ".rs": "rust",
        ".json": "json",
        ".md": "markdown",
        ".sh": "bash",
        ".bash": "bash",
        ".html": "xml",
        ".xml": "xml",
        ".css": "css",
    }.get(ext, "plaintext")


def load_recording(path: Path, excluded_file_types: list[str]) -> list[dict]:
    """Load a .jsonl or .jsonl.gz recording and drop events for excluded file types.

    Events whose ``document`` field ends in any of ``excluded_file_types``
    (e.g. ``.html``, ``.md``) are filtered out. The resulting list is the
    chronological event stream the analyzer consumes.
    """
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as f:
        events = [json.loads(line) for line in f]
    if not excluded_file_types:
        return events
    return [
        e for e in events
        if not any(e.get("document", "").endswith(ext) for ext in excluded_file_types)
    ]
```

- [ ] **Step 2: Update `recan/main.py` to import from `utils`**

In `recan/main.py`:

1. Remove the constant `GENERATED_MIN_CHARS_SINGLE_LINE` definition (line 14).
2. Remove the helper function definitions: `parse_ts` (lines 17-28), `_event_kind` (31-40), `_is_generated_edit` (43-57), `format_duration` (60-65), `_language_from_extension` (111-136), `_apply_edit` (139-142), `_format_ts` (458-459).
3. Add at the top of `main.py` (after the existing `import` block, before `BURST_GROUP_WINDOW_MS`):

```python
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
```

4. Replace remaining call sites in `main.py` so they use the public (un-prefixed) names:
   - `_event_kind(` → `event_kind(`
   - `_is_generated_edit(` → `is_generated_edit(`
   - `_apply_edit(` → `apply_edit(`
   - `_language_from_extension(` → `language_from_extension(`
   - `_format_ts(` → `format_ts(`
   - `_format_duration(` → `format_duration(`

5. In `main()` (currently lines 492-516), replace the gzip/json loading block:

```python
opener = gzip.open if recording_file.suffix == ".gz" else open
with opener(recording_file, "rt") as f:
    inputs = [json.loads(line) for line in f]
```

with:

```python
inputs = load_recording(recording_file, excluded_file_types)
```

6. Move the excluded-file filter out of `analyze_inputs` (lines 387-389) and `_build_playback_bundle` (lines 175-177): delete those `if any(...): continue` lines. Both functions now trust their input is pre-filtered.

7. Update the `analyze_inputs` and `_build_playback_bundle` signatures to drop `excluded_file_types`:
   - `def analyze_inputs(inputs: list[dict]) -> dict:` (was `inputs, excluded_file_types`)
   - `def _build_playback_bundle(inputs: list[dict]) -> dict:` (was `inputs, excluded_file_types`)
   - `_build_playback_bundle(inputs, excluded_file_types)` call site → `_build_playback_bundle(inputs)`
   - `analyze_inputs(inputs, excluded_file_types)` call site → `analyze_inputs(inputs)`

8. Remove the `import gzip` line at the top of `main.py` — `main.py` no longer touches gzip directly.

- [ ] **Step 3: Verify text output unchanged**

```bash
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html > /tmp/check.out
diff /tmp/recan-baseline/text.out /tmp/check.out
```

Expected: empty diff (no output). If anything differs, the helper move broke parity — investigate before continuing.

- [ ] **Step 4: Verify JSON output unchanged**

```bash
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html --json > /tmp/check.json
diff /tmp/recan-baseline/json.out /tmp/check.json
```

Expected: empty diff.

- [ ] **Step 5: Verify HTML player unchanged**

```bash
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html --view
diff /tmp/recan-baseline/player.html samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.html
```

Expected: empty diff.

- [ ] **Step 6: Commit**

```bash
git add recan/utils.py recan/main.py
git commit -m "refactor: extract pure helpers and load_recording into utils.py"
```

---

## Task 2: Fill in `structure.py` with the `Session` types

**Files:**
- Modify: `recan/structure.py` (currently has only Input TypedDicts and is missing imports)

`structure.py` currently defines `Input`, `EditInput`, `FocusStatusInput` but is missing the `Literal`, `TypedDict` imports. This task fixes that and adds the new types the walker will produce.

- [ ] **Step 1: Replace `recan/structure.py` with**

```python
from datetime import datetime
from typing import Literal, TypedDict


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


class FocusInterval(TypedDict):
    blur_idx: int           # index into Session["events"]
    focus_idx: int
    duration: float


class IdleGap(TypedDict):
    after_idx: int
    duration: float


class Burst(TypedDict):
    kind: Literal['paste', 'ide_action']
    timestamp: datetime
    start_idx: int          # index into Session["events"]
    end_idx: int
    line_count: int
    char_count: int
    fragment: str           # text inserted (used by markdown summary)


class Snapshot(TypedDict):
    after_idx: int
    document_text: str


class TimelineEntry(TypedDict, total=False):
    # Unified entry for the human-readable timeline.
    # kind ∈ {"focus", "paste", "ide_action"}
    kind: str
    timestamp: datetime
    duration: float          # focus only
    line_count: int          # bursts only
    char_count: int
    fragment: str


class Session(TypedDict):
    # metadata
    document: str
    language: str
    start_time: datetime
    end_time: datetime
    total_time: float
    total_time_unfocused: float

    # summary counters (back-compat with old analyze_inputs return shape)
    total_edits: int
    total_pastes: int
    total_ide_actions: int
    total_generated_events: int

    # raw streams (HTML viewer reads these)
    events: list[dict]
    focus_intervals: list[FocusInterval]
    idle_gaps: list[IdleGap]
    bursts: list[Burst]
    snapshots: list[Snapshot]

    # derived view (markdown summary reads this)
    timeline: list[TimelineEntry]
```

`TimelineEntry` uses `total=False` because focus entries lack burst fields and burst entries lack `duration` — partial dicts are intentional.

- [ ] **Step 2: Verify the module imports cleanly**

```bash
poetry run python -c "from recan.structure import Session, Burst, FocusInterval, IdleGap, Snapshot, TimelineEntry, Input, EditInput, FocusStatusInput; print('ok')"
```

Expected: prints `ok`. (TypedDicts have no runtime behavior, so this is just an import check.)

- [ ] **Step 3: Commit**

```bash
git add recan/structure.py
git commit -m "refactor: add Session and supporting TypedDicts to structure.py"
```

---

## Task 3: Create `session.py` with the unified walker

**Files:**
- Create: `recan/session.py`
- Modify: `recan/main.py` (replace `analyze_inputs` + `_build_playback_bundle` + `_collapse_ide_action_bursts` with imports from `session`)

This is the heart of the refactor. One walker produces a Session that both consumers derive from. The paste-then-delete cancellation (`pasted_fragments`, `cancelled_paste`) is **intentionally dropped** here.

- [ ] **Step 1: Write `recan/session.py`**

```python
from datetime import datetime, timedelta

from recan.structure import (
    Burst,
    FocusInterval,
    IdleGap,
    Session,
    Snapshot,
    TimelineEntry,
)
from recan.utils import (
    apply_edit,
    event_kind,
    is_generated_edit,
    language_from_extension,
    parse_ts,
)


BURST_GROUP_WINDOW_MS = 100
IDLE_GAP_THRESHOLD_SECONDS = 5.0
SNAPSHOT_INTERVAL_EVENTS = 200


def analyze_inputs(inputs: list[dict]) -> Session:
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
    generated_entries: list[dict] = []     # raw generated edits, for burst grouping
    timeline: list[TimelineEntry] = []     # human-readable rollup (focus + bursts)

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
                    # delete-then-paste = move; skip
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

    # Final snapshot so scrubbing to the end of the player is fast.
    if events:
        last = snapshots[-1] if snapshots else None
        if last is None or last["after_idx"] != len(events) - 1:
            snapshots.append({
                "after_idx": len(events) - 1,
                "document_text": document,
            })

    # Collapse adjacent generated edits within BURST_GROUP_WINDOW_MS into bursts.
    bursts: list[Burst] = []
    window = timedelta(milliseconds=BURST_GROUP_WINDOW_MS)
    group: list[dict] = []

    def flush_group():
        if not group:
            return
        kind_label = "ide_action" if len(group) > 1 else "paste"
        kept = group[-1]
        bursts.append({
            "kind": kind_label,
            "timestamp": kept["timestamp"],
            "start_idx": group[0]["event_idx"],
            "end_idx": kept["event_idx"],
            "line_count": sum(e["line_count"] for e in group),
            "char_count": sum(e["char_count"] for e in group),
            "fragment": kept["fragment"],
        })
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

    # Merge bursts into the timeline (focus losses already present).
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
    total_pastes = sum(1 for b in bursts if b["kind"] == "paste")

    return {
        "document": document_name,
        "language": language_from_extension(document_name),
        "start_time": start_time,
        "end_time": end_time,
        "total_time": total_time,
        "total_time_unfocused": total_time_unfocused,
        "total_edits": edit_count,
        "total_pastes": total_pastes,
        "total_ide_actions": total_ide_actions,
        "total_generated_events": total_ide_actions + total_pastes,
        "events": events,
        "focus_intervals": focus_intervals,
        "idle_gaps": idle_gaps,
        "bursts": bursts,
        "snapshots": snapshots,
        "timeline": timeline,
    }
```

- [ ] **Step 2: Update `recan/main.py` to use `session.analyze_inputs` and remove the old code**

In `recan/main.py`:

1. Delete the constants now living in `session.py` (lines 11-14: `BURST_GROUP_WINDOW_MS`, `IDLE_GAP_THRESHOLD_SECONDS`, `SNAPSHOT_INTERVAL_EVENTS`). `GENERATED_MIN_CHARS_SINGLE_LINE` was already removed in Task 1.
2. Delete `_collapse_ide_action_bursts` (lines 68-108).
3. Delete `_build_playback_bundle` entirely (lines 145-330).
4. Delete the old `analyze_inputs` (lines 367-455).
5. Add to the imports at the top of the file:

```python
from recan.session import analyze_inputs
```

6. The `view` branch in `main()` previously called `_build_playback_bundle(inputs)`. Replace it with a session-based bundle. For now, build the bundle inline in `main.py` (Task 5 will move this to `viewer.py`):

```python
if view:
    bundle = {
        "metadata": {
            "document": session["document"],
            "language": session["language"],
            "start_time": session["start_time"].isoformat().replace("+00:00", "Z") if session["start_time"] else "",
            "end_time": session["end_time"].isoformat().replace("+00:00", "Z") if session["end_time"] else "",
            "duration_seconds": session["total_time"],
        },
        "events": session["events"],
        "bursts": [
            {k: v for k, v in b.items() if k != "timestamp" and k != "fragment"}
            for b in session["bursts"]
        ],
        "idle_gaps": [{"after_idx": g["after_idx"], "duration_seconds": g["duration"]} for g in session["idle_gaps"]],
        "focus_intervals": [
            {"blur_idx": fi["blur_idx"], "focus_idx": fi["focus_idx"], "duration_seconds": fi["duration"]}
            for fi in session["focus_intervals"]
        ],
        "snapshots": session["snapshots"],
        "summary": {
            "total_seconds": session["total_time"],
            "unfocused_seconds": session["total_time_unfocused"],
            "burst_count": len(session["bursts"]),
            "edit_count": session["total_edits"],
            "generated_count": len(session["bursts"]),
            "ide_action_count": session["total_ide_actions"],
            "paste_count": session["total_pastes"],
        },
    }
    html = _render_player_html(bundle)
    ...
```

The shape above matches the current player bundle exactly so the existing `_render_player_html` path keeps working.

7. Update the `main()` function to call the new walker. The flow becomes:

```python
def main(recording_file, excluded_file_types, output=None, _json=False, view=False):
    inputs = load_recording(recording_file, excluded_file_types)
    session = analyze_inputs(inputs)

    if _json:
        results = json.dumps(session, default=str, indent=4)
    else:
        results = print_timeline(session)

    if output:
        with open(output, "w") as f:
            f.write(results)
    else:
        print(results)

    if view:
        # bundle assembly as in step 6 above
        bundle = {...}
        html = _render_player_html(bundle)
        stem = recording_file.with_suffix("") if recording_file.suffix == ".gz" else recording_file
        html_path = stem.with_suffix(stem.suffix + ".html") if stem.suffix else stem.with_suffix(".html")
        html_path.write_text(html, encoding="utf-8")
        print(f"Wrote playback HTML to {html_path}")
```

8. The existing `print_timeline(results)` and `_format_entry(entry)` functions consume the old shape. The new Session uses the same key names for the fields they read (`total_time`, `total_time_unfocused`, `start_time`, `total_ide_actions`, `total_pastes`, `timeline`). The `timeline` entries use the same kinds (`focus`, `paste`, `ide_action`) and the same field names (`timestamp`, `duration`, `line_count`, `char_count`, `fragment`). `print_timeline` continues to work unchanged in this task — it'll be replaced by `formaters.render_markdown` in Task 4.

- [ ] **Step 3: Verify text output**

```bash
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html > /tmp/check.out
diff /tmp/recan-baseline/text.out /tmp/check.out
```

**Expected:** there *may* be differences in burst counts and the timeline if this recording contains paste-then-delete sequences (this is the intentional behavior change). If there are differences, examine them to confirm they only show as:

- Lower totals where the old code cancelled a paste retroactively.
- Extra timeline entries for those previously-cancelled pastes.

Save this diff for review:

```bash
diff /tmp/recan-baseline/text.out /tmp/check.out > /tmp/recan-text-diff.out
```

If the diff shows changes to *focus times*, *edit totals*, or *unrelated bursts*, the walker has a real bug — investigate before continuing.

- [ ] **Step 4: Verify JSON output**

```bash
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html --json > /tmp/check.json
python -c "import json; json.load(open('/tmp/check.json')); print('valid')"
```

Expected: prints `valid`. The output is now a *superset* of the old shape — every old key is present, plus `events`, `focus_intervals`, `idle_gaps`, `bursts`, `snapshots`, `document`, `language`, `end_time`. Confirm the legacy keys are present:

```bash
python -c "
import json
d = json.load(open('/tmp/check.json'))
need = {'total_time','total_time_unfocused','total_ide_actions','total_pastes','total_generated_events','total_edits','start_time','timeline'}
missing = need - set(d.keys())
print('missing keys:', missing if missing else 'none')
"
```

Expected: prints `missing keys: none`.

- [ ] **Step 5: Verify HTML player still opens**

```bash
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html --view
ls -la samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.html
```

Expected: file exists and is similar size (within ~1%) to `/tmp/recan-baseline/player.html`. Open it in a browser and confirm the player loads, scrubs, and shows the document. Burst counts may differ (intentional behavior change).

- [ ] **Step 6: Commit**

```bash
git add recan/session.py recan/main.py
git commit -m "refactor: collapse analyze_inputs and _build_playback_bundle into session.py walker"
```

---

## Task 4: Move text rendering to `formaters.py` + `timeline.md.jinja`

**Files:**
- Modify: `recan/formaters.py` (currently `2`, single byte)
- Modify: `recan/timeline.md.jinja` (currently empty)
- Modify: `recan/main.py` (replace `print_timeline` with `render_markdown`/`render_json`)

This task replaces the old `print_timeline` string-builder with a Jinja-rendered Markdown summary. The `--json` path also moves into `formaters.py`.

- [ ] **Step 1: Write `recan/timeline.md.jinja`**

```jinja
# Recording Summary

**Document:** `{{ session.document or "(unknown)" }}`
**Recording Started:** {{ format_ts(session.start_time) }}

## Totals

| Metric            | Value                                                |
| ----------------- | ---------------------------------------------------- |
| Total Time        | {{ format_duration(session.total_time) }}            |
| Time Focused      | {{ format_duration(session.total_time - session.total_time_unfocused) }} |
| Time Unfocused    | {{ format_duration(session.total_time_unfocused) }}  |
| Edits             | {{ session.total_edits }}                            |
| IDE Actions       | {{ session.total_ide_actions }}                      |
| Pastes            | {{ session.total_pastes }}                           |

## Timeline

{% if session.timeline -%}
{% for entry in session.timeline -%}
{% if entry.kind == "focus" -%}
- **{{ format_ts(entry.timestamp) }}** — Lost focus for {{ format_duration(entry.duration) }}
{% else -%}
- **{{ format_ts(entry.timestamp) }}** — {{ "IDE Action" if entry.kind == "ide_action" else "Paste" }} ({{ entry.line_count }} lines, {{ entry.char_count }} chars)

  ```{{ session.language }}
  {{ entry.fragment | indent(2) }}
  ```
{% endif -%}
{% endfor %}
{%- else -%}
*(no notable timeline entries)*
{%- endif %}
```

The `format_ts` and `format_duration` helpers come from `recan.utils` and are passed into the Jinja environment by `render_markdown`.

- [ ] **Step 2: Write `recan/formaters.py`**

Replace the current contents of `recan/formaters.py` (which has a stray `2` character) with:

```python
import json
from pathlib import Path

import jinja2

from recan.structure import Session
from recan.utils import format_duration, format_ts


_TEMPLATE_PATH = Path(__file__).resolve().parent / "timeline.md.jinja"


def render_markdown(session: Session) -> str:
    """Render the human-readable Markdown summary from a Session."""
    template_text = _TEMPLATE_PATH.read_text(encoding="utf-8")
    env = jinja2.Environment(
        autoescape=False,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.from_string(template_text)
    return template.render(
        session=session,
        format_ts=format_ts,
        format_duration=format_duration,
    )


def render_json(session: Session) -> str:
    """Serialize the Session as indented JSON for the --json flag."""
    return json.dumps(session, default=str, indent=4)
```

- [ ] **Step 3: Update `recan/main.py` to use `formaters`**

In `recan/main.py`:

1. Delete `_format_entry` and `print_timeline` (lines 462-489).
2. Delete `import json` from the top of `main.py` if nothing else needs it (the bundle assembly added in Task 3 still uses `json.dumps`? No — `_render_player_html` uses `json.dumps` via `_embed_safe_json`, but `main.py` itself no longer needs `json`).
3. Add to the imports:

```python
from recan.formaters import render_markdown, render_json
```

4. In `main()`, replace the format-selection block:

```python
if _json:
    results = json.dumps(session, default=str, indent=4)
else:
    results = print_timeline(session)
```

with:

```python
text = render_json(session) if _json else render_markdown(session)
```

(rename the local `results` variable to `text` while you're at it for readability — only used in the next line for `output.write_text`/`print`).

5. Update the write/print block to use `text`:

```python
if output:
    output.write_text(text, encoding="utf-8")
else:
    print(text)
```

(The old code used `open(output, "w")` — `Path.write_text` is shorter and equivalent.)

- [ ] **Step 4: Verify JSON output is still a superset of the baseline**

```bash
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html --json > /tmp/check.json
python -c "
import json
d = json.load(open('/tmp/check.json'))
need = {'total_time','total_time_unfocused','total_ide_actions','total_pastes','total_generated_events','total_edits','start_time','timeline'}
missing = need - set(d.keys())
print('missing keys:', missing if missing else 'none')
print('total_time:', d['total_time'])
print('total_edits:', d['total_edits'])
"
```

Expected: `missing keys: none` and reasonable values printed.

- [ ] **Step 5: Verify Markdown output renders**

```bash
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html
```

Expected: stdout shows the new Markdown summary (`# Recording Summary`, totals table, timeline list with fenced code blocks). Verify it covers the same fields the baseline did:

- Title with document name.
- Total Time, Time Focused, Time Unfocused, IDE Actions, Pastes.
- Recording-started timestamp.
- A bulleted timeline of focus losses + bursts in chronological order, with fragment text in fenced code blocks for bursts.

This is an **intentional output change** — there is no parity check against `/tmp/recan-baseline/text.out` here.

- [ ] **Step 6: Verify HTML player still works**

```bash
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html --view
```

Expected: HTML file written; opens and works in a browser. (Behavior should be identical to end of Task 3 — this task didn't touch the player path.)

- [ ] **Step 7: Commit**

```bash
git add recan/formaters.py recan/timeline.md.jinja recan/main.py
git commit -m "refactor: render text output via Jinja markdown template in formaters.py"
```

---

## Task 5: Move HTML player rendering to `viewer.py`

**Files:**
- Modify: `recan/viewer.py` (currently empty)
- Modify: `recan/main.py` (delete `_embed_safe_json`, `_render_player_html`, `PLAYER_DIR`, the inline bundle assembly)

- [ ] **Step 1: Write `recan/viewer.py`**

```python
import json
from pathlib import Path

import jinja2

from recan.structure import Session


_PLAYER_DIR = Path(__file__).resolve().parent / "player"


def _to_player_bundle(session: Session) -> dict:
    """Project a Session into the JSON bundle the existing player consumes.

    Field names here match what player.js reads — keep this stable.
    """
    start = session["start_time"]
    end = session["end_time"]
    return {
        "metadata": {
            "document": session["document"],
            "language": session["language"],
            "start_time": start.isoformat().replace("+00:00", "Z") if start else "",
            "end_time": end.isoformat().replace("+00:00", "Z") if end else "",
            "duration_seconds": session["total_time"],
        },
        "events": session["events"],
        "bursts": [
            {
                "kind": b["kind"],
                "start_idx": b["start_idx"],
                "end_idx": b["end_idx"],
                "lines": b["line_count"],
                "chars": b["char_count"],
            }
            for b in session["bursts"]
        ],
        "idle_gaps": [
            {"after_idx": g["after_idx"], "duration_seconds": g["duration"]}
            for g in session["idle_gaps"]
        ],
        "focus_intervals": [
            {"blur_idx": fi["blur_idx"], "focus_idx": fi["focus_idx"], "duration_seconds": fi["duration"]}
            for fi in session["focus_intervals"]
        ],
        "snapshots": session["snapshots"],
        "summary": {
            "total_seconds": session["total_time"],
            "unfocused_seconds": session["total_time_unfocused"],
            "burst_count": len(session["bursts"]),
            "edit_count": session["total_edits"],
            "generated_count": len(session["bursts"]),
            "ide_action_count": session["total_ide_actions"],
            "paste_count": session["total_pastes"],
        },
    }


def _embed_safe_json(bundle: dict) -> str:
    """JSON-encode a bundle for safe embedding in an HTML <script> tag.

    Two specific dangers when embedding JSON in HTML inside a <script> block:
    1. The substring ``</script`` ends the script element early. Escape ``</`` to ``<\\/``.
    2. U+2028 / U+2029 are valid in JSON but illegal in JS string literals.
    """
    text = json.dumps(bundle, ensure_ascii=False, default=str)
    text = text.replace("</", "<\\/")
    text = text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return text


def build_player_html(session: Session) -> str:
    """Render the static HTML player by inlining the bundle and assets via Jinja2."""
    bundle = _to_player_bundle(session)
    template_text = (_PLAYER_DIR / "template.html").read_text(encoding="utf-8")
    css = (_PLAYER_DIR / "player.css").read_text(encoding="utf-8")
    js = (_PLAYER_DIR / "player.js").read_text(encoding="utf-8")
    highlight = (_PLAYER_DIR / "highlight.min.js").read_text(encoding="utf-8")

    env = jinja2.Environment(autoescape=False, keep_trailing_newline=True)
    template = env.from_string(template_text)
    return template.render(
        document_name=bundle["metadata"].get("document", "recording"),
        css=css,
        js=js,
        highlight=highlight,
        bundle=_embed_safe_json(bundle),
    )


def write_player_html(session: Session, recording_file: Path) -> Path:
    """Write the player HTML next to the recording file and return its path."""
    html = build_player_html(session)
    stem = recording_file.with_suffix("") if recording_file.suffix == ".gz" else recording_file
    html_path = stem.with_suffix(stem.suffix + ".html") if stem.suffix else stem.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")
    return html_path
```

Note: the original `main.py` source contains literal U+2028 / U+2029 codepoints inside the `.replace(...)` arguments. The version above uses the explicit `\u2028` / `\u2029` Python escapes for clarity — same runtime behavior, but the source is unambiguous when read in any editor.

- [ ] **Step 2: Update `recan/main.py` to use `viewer.write_player_html`**

In `recan/main.py`:

1. Delete `PLAYER_DIR`, `_embed_safe_json`, `_render_player_html`.
2. Delete the inline bundle-assembly block from Task 3 step 6.
3. Add to imports:

```python
from recan.viewer import write_player_html
```

4. The `view` branch in `main()` becomes:

```python
if view:
    html_path = write_player_html(session, recording_file)
    print(f"Wrote playback HTML to {html_path}")
```

5. Remove `import jinja2` from `main.py` if nothing else uses it.

- [ ] **Step 3: Verify the player still works**

```bash
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html --view
```

Expected: prints `Wrote playback HTML to ...`. Open the HTML in a browser. Confirm:

- The player loads (no JS errors in console).
- The document text is visible.
- The scrubber works.
- The U+2028 fix didn't break embedding (size should be similar to baseline; if a recording happens to contain a literal U+2028 in a fragment, that one would now embed correctly where before it would have been mangled).

- [ ] **Step 4: Commit**

```bash
git add recan/viewer.py recan/main.py
git commit -m "refactor: move HTML player rendering to viewer.py"
```

---

## Task 6: Slim `main.py` to CLI + orchestration only

**Files:**
- Modify: `recan/main.py`

After Tasks 1–5, `main.py` should already be close to the target shape. This task is a final cleanup pass — delete unused imports, ensure the `main()` function is the simple orchestration shown in the spec, and confirm the file is roughly 50 lines.

- [ ] **Step 1: Replace `recan/main.py` with the final form**

```python
from argparse import ArgumentParser
from pathlib import Path

from recan.formaters import render_json, render_markdown
from recan.session import analyze_inputs
from recan.utils import load_recording
from recan.viewer import write_player_html


def main(
    recording_file: Path,
    excluded_file_types: list[str],
    output: Path | None = None,
    _json: bool = False,
    view: bool = False,
) -> None:
    inputs = load_recording(recording_file, excluded_file_types)
    session = analyze_inputs(inputs)

    text = render_json(session) if _json else render_markdown(session)
    if output:
        output.write_text(text, encoding="utf-8")
    else:
        print(text)

    if view:
        html_path = write_player_html(session, recording_file)
        print(f"Wrote playback HTML to {html_path}")


def entry() -> None:
    parser = ArgumentParser(description="Analyze IDE recording files.")
    parser.add_argument("recording_file", type=Path,
                        help="Path to the recording file (JSONL or gzipped JSONL).")
    parser.add_argument("--exclude", nargs="*", default=[],
                        help="List of file extensions to exclude (e.g. .html .md).")
    parser.add_argument("--output", type=Path,
                        help="Path to write the analysis results (defaults to stdout).")
    parser.add_argument("--json", action="store_true",
                        help="Output the analysis results as JSON instead of human-readable Markdown.")
    parser.add_argument("--view", action="store_true",
                        help="Generate a self-contained HTML player next to the recording.")

    args = parser.parse_args()
    main(args.recording_file, args.exclude, args.output, args.json, args.view)


if __name__ == "__main__":
    entry()
```

- [ ] **Step 2: Verify line count is roughly 50**

```bash
wc -l recan/main.py
```

Expected: somewhere between 40 and 55 lines. If it's much larger, look for orphan code that should have moved during earlier tasks.

- [ ] **Step 3: Verify all three CLI modes still work**

```bash
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html --json | python -c "import json, sys; json.load(sys.stdin); print('json ok')"
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html --view
```

Expected: Markdown summary on stdout; `json ok` after the JSON run; HTML player file written and openable.

- [ ] **Step 4: Verify imports are clean**

```bash
poetry run python -c "import recan.main; import recan.session; import recan.formaters; import recan.viewer; import recan.utils; import recan.structure; print('all import ok')"
```

Expected: prints `all import ok`.

- [ ] **Step 5: Commit**

```bash
git add recan/main.py
git commit -m "refactor: slim main.py to CLI parsing and orchestration"
```

---

## Task 7: Update `analyze_assignment.py`

**Files:**
- Modify: `recan/analyze_assignment.py`

Fix the broken `from main import` and use `load_recording` instead of inline gzip/json loading.

- [ ] **Step 1: Update imports and the recording-load block**

In `recan/analyze_assignment.py`:

1. Replace the imports block at the top:

```python
import gzip
import json
import re
import statistics
import zlib

from pathlib import Path
from argparse import ArgumentParser

from main import analyze_inputs
```

with:

```python
import json
import re
import statistics
import zlib

from pathlib import Path
from argparse import ArgumentParser

from recan.session import analyze_inputs
from recan.utils import load_recording
```

(Drops `gzip` because `load_recording` handles it. Keeps `json` and `zlib` because they appear in the exception-handler tuple.)

2. In `analyze_assignment` (the function), replace the per-recording load block:

```python
opener = gzip.open if recording.suffix == '.gz' else open
try:
    with opener(recording, 'rt') as f:
        inputs = [json.loads(line) for line in f]
    metrics = analyze_inputs(inputs, EXCLUDED_FILE_TYPES)
except (OSError, EOFError, json.JSONDecodeError, zlib.error) as exc:
    print(f"Warning: skipping {recording.name} — failed to read ({type(exc).__name__}: {exc})")
    continue
```

with:

```python
try:
    inputs = load_recording(recording, EXCLUDED_FILE_TYPES)
    metrics = analyze_inputs(inputs)
except (OSError, EOFError, json.JSONDecodeError, zlib.error) as exc:
    print(f"Warning: skipping {recording.name} — failed to read ({type(exc).__name__}: {exc})")
    continue
```

(Same exception types are caught — `gzip` errors surface as `OSError` / `EOFError` / `zlib.error`, which is what the original tuple already covered.)

- [ ] **Step 2: Verify the script runs from the repo root with the new import**

```bash
poetry run python -m recan.analyze_assignment --folder samples/homework-1b-functions --out-dir /tmp/recan-check-csvs
```

Expected: prints `Generated CSV for ...` lines, no import errors, no broken-pipe warnings.

- [ ] **Step 3: Verify CSV outputs match the baseline**

```bash
diff -r /tmp/recan-baseline/assignment-csvs /tmp/recan-check-csvs
```

**Expected:** if the sample assignment contains paste-then-delete sequences, the `num_pastes` / `num_generated_events` / `__mean__` / `__median__` / `__stdev__` rows may differ — this is the intentional behavior change. If the diff shows changes to *focus times*, *edits*, or *student_ids*, the walker is broken.

Save the diff for review:

```bash
diff -r /tmp/recan-baseline/assignment-csvs /tmp/recan-check-csvs > /tmp/recan-csv-diff.out
```

- [ ] **Step 4: Commit**

```bash
git add recan/analyze_assignment.py
git commit -m "refactor: update analyze_assignment to use recan.session and load_recording"
```

---

## Task 8: Final cleanup and verification sweep

**Files:**
- (no edits — this is a verification task)

A final pass to catch anything orphaned by the refactor.

- [ ] **Step 1: Search for stray references to removed names**

```bash
grep -rn -E '_event_kind|_is_generated_edit|_apply_edit|_language_from_extension|_format_ts|_format_entry|_collapse_ide_action_bursts|_build_playback_bundle|_render_player_html|_embed_safe_json|PLAYER_DIR|print_timeline' recan/
```

Expected: no matches. (Names with leading underscore are private to their original location and should be gone everywhere.)

- [ ] **Step 2: Check `pyproject.toml` entry points still resolve**

```bash
poetry run recan --help
```

Expected: argparse help output. (The `recan` script in `pyproject.toml:21` points at `recan.main:entry`, which still exists.)

- [ ] **Step 3: Re-run all three CLI modes one more time**

```bash
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html --json | python -c "import json, sys; json.load(sys.stdin); print('json ok')"
poetry run recan samples/homework-1b-functions/assignment_7360066_export-submission_380567267__one_tree.recording.jsonl.gz --exclude .html --view
```

Expected: Markdown summary; `json ok`; HTML player written.

- [ ] **Step 4: Verify the file count and shape match the spec**

```bash
ls -la recan/
wc -l recan/*.py recan/*.jinja
```

Expected file list: `__init__.py`, `VERSION`, `main.py`, `session.py`, `structure.py`, `utils.py`, `formaters.py`, `timeline.md.jinja`, `viewer.py`, `analyze_assignment.py`, `player/` directory. Sizes roughly: `main.py` ~50, `session.py` ~150, `utils.py` ~100, `formaters.py` ~30, `viewer.py` ~80, `structure.py` ~70.

- [ ] **Step 5: No commit** — verification only.

---

## Spec coverage check

Every section of the spec maps to a task in this plan:

| Spec section                          | Task(s)         |
| ------------------------------------- | --------------- |
| Module layout                         | Task 1, 2, 3, 4, 5, 6, 7 |
| Dependency graph                      | Verified in Task 8 step 1 |
| `utils.py` responsibilities           | Task 1          |
| `structure.py` responsibilities       | Task 2          |
| `session.py` responsibilities         | Task 3          |
| `formaters.py` responsibilities       | Task 4          |
| `timeline.md.jinja` responsibilities  | Task 4          |
| `viewer.py` responsibilities          | Task 5          |
| `main.py` final shape                 | Task 6          |
| `analyze_assignment.py` updates       | Task 7          |
| Data flow                             | Tasks 1, 3, 5   |
| Behavior preservation (legacy keys)   | Task 3 step 4, Task 4 step 4 |
| Behavior change: Markdown output      | Task 4          |
| Behavior change: drop paste-then-delete | Task 3        |
| Move-detection rule (delete-then-paste) | Task 3        |
| Build sequence (1–7)                  | Tasks 1–7       |
| Verification on sample recording      | Task 0 + per-task verify steps |
| Risks                                 | Per-task verify steps explicitly call out expected diffs |
