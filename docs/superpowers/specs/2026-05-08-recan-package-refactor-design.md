# `recan` package refactor — design

## Goal

Split the current monolithic `recan/main.py` (~530 lines) into focused modules so that:

1. `main.py` contains only CLI parsing and high-level orchestration.
2. The duplicated event-walking logic in `analyze_inputs` and `_build_playback_bundle` collapses into a single walker producing one `Session` model.
3. Each module has one clear responsibility, narrow imports, and is easy to navigate.
4. The human-readable text output upgrades to a Markdown-flavored summary rendered from a Jinja template.

## Non-goals

- No change to the HTML player UI (the `player/` directory is untouched).
- No restructuring of `analyze_assignment.py` beyond what's required to keep it working with the new module layout.
- No new features (no new metrics, no new CLI flags). This refactor is purely structural.
- No new tests in this refactor — behavior parity is verified by diffing outputs against the old implementation on sample recordings.

## Module layout

```
recan/
├── __init__.py             # __version__ (unchanged)
├── VERSION
├── main.py                 # CLI entry + orchestration only (~50 lines)
├── session.py              # analyze_inputs(): single-pass walker → Session
├── structure.py            # TypedDicts (inputs + Session + sub-types)
├── utils.py                # pure helpers + load_recording
├── formaters.py            # render_markdown(session), render_json(session)
├── timeline.md.jinja       # Markdown template for human-readable summary
├── viewer.py               # build_player_html(session), write_player_html
├── player/                 # unchanged (template.html, css, js, highlight)
└── analyze_assignment.py   # updated to import from recan.session and use load_recording
```

### Dependency graph (no cycles)

```
main.py            ──►  utils, session, formaters, viewer
session            ──►  utils, structure
formaters          ──►  utils, structure   (loads timeline.md.jinja)
viewer             ──►  utils, structure   (loads player/*)
structure          ──►  (stdlib only)
utils              ──►  (stdlib only)
analyze_assignment ──►  utils, session
```

`main.py` is the only module that imports all four leaves. Every other module has a small, intentional surface.

## Module responsibilities

### `utils.py`
Pure helpers and the single I/O entry point. No business logic.

- `parse_ts(value: str) -> datetime` — tolerant ISO timestamp parser (handles `Z` suffix and nanosecond precision).
- `format_duration(seconds: float) -> str` — `HH:MM:SS` / `MM:SS`.
- `format_ts(ts: datetime) -> str` — `YYYY-MM-DD HH:MM:SS`.
- `event_kind(event: dict) -> str` — infer `focusStatus` / `edit` / `unknown` for older recordings.
- `is_generated_edit(event: dict) -> bool` — heuristic for "this insert wasn't typed character-by-character" (existing logic).
- `apply_edit(document: str, offset: int, old: str, new: str) -> str` — apply a single edit to an in-memory document.
- `language_from_extension(document: str) -> str` — map filename to highlight.js language.
- `load_recording(path: Path, excluded_file_types: list[str]) -> list[dict]` — opens `.jsonl` or `.jsonl.gz`, parses lines, **drops events whose `document` ends in any excluded extension**. This is the single place that touches the filesystem and the single place that filters by file type.

### `structure.py`
TypedDicts only. No behavior. Existing input types extended with the unified `Session` model.

```python
class Input(TypedDict):
    type: Literal["focusStatus", "edit"]
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
    kind: Literal["paste", "ide_action"]
    timestamp: datetime
    start_idx: int          # index into Session["events"]
    end_idx: int
    line_count: int
    char_count: int
    fragment: str           # text inserted (used by markdown summary)

class Snapshot(TypedDict):
    after_idx: int
    document_text: str

class TimelineEntry(TypedDict):
    # Unified entry for the human-readable timeline.
    # kind ∈ {"focus", "paste", "ide_action"}
    kind: str
    timestamp: datetime
    duration: float | None      # focus only
    line_count: int | None      # bursts only
    char_count: int | None
    fragment: str | None

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
    events: list[dict]              # every focusStatus + edit, chronological
    focus_intervals: list[FocusInterval]
    idle_gaps: list[IdleGap]
    bursts: list[Burst]
    snapshots: list[Snapshot]

    # derived view (markdown summary reads this)
    timeline: list[TimelineEntry]
```

### `session.py`
The unified walker. One pass, one Session.

- `analyze_inputs(inputs: list[dict]) -> Session` — pure: takes pre-filtered events, returns a `Session` TypedDict. No filesystem, no filtering, no I/O.

The walker subsumes both old functions:

- Pairs `focusStatus` blur → next focus, producing `focus_intervals` and accumulating `total_time_unfocused`.
- Tracks gaps between adjacent kept events, emitting `idle_gaps` for gaps over the threshold.
- Maintains a running document string, snapshotting every `SNAPSHOT_INTERVAL_EVENTS` edits and one final snapshot at the end.
- Tracks `deleted_fragments` (multiset) to implement the move-detection rule:
  - **Delete-then-paste**: if a generated insert matches a previously-deleted fragment, treat it as a move and skip.

  The previous **paste-then-delete** retroactive cancellation is dropped — the walker no longer maintains `pasted_fragments` and no longer rewrites past timeline entries.
- Collapses adjacent generated edits within `BURST_GROUP_WINDOW_MS` into `bursts`, classifying singletons as `paste` and groups as `ide_action`.
- Builds `timeline` (the human-friendly rollup of focus losses + bursts in chronological order) for `formaters.py` to consume.

Constants live in `session.py`: `BURST_GROUP_WINDOW_MS`, `IDLE_GAP_THRESHOLD_SECONDS`, `SNAPSHOT_INTERVAL_EVENTS`. The one constant `is_generated_edit` reads (`GENERATED_MIN_CHARS_SINGLE_LINE`) lives in `utils.py` next to the function that uses it.

### `formaters.py`
Output rendering for stdout / `--output`. No I/O beyond reading the Jinja template.

- `render_markdown(session: Session) -> str` — loads `timeline.md.jinja` and renders it with the session. Output is a richer Markdown summary than the current text format: headings, bullets, fenced code blocks for fragment text. Replaces `print_timeline` and `_format_entry`.
- `render_json(session: Session) -> str` — `json.dumps(session, default=str, indent=4)`. The Session shape includes the legacy keys (`total_time`, `total_time_unfocused`, `total_generated_events`, `total_ide_actions`, `total_pastes`, `total_edits`, `start_time`, `timeline`) so any external consumer of the old `--json` output (including `analyze_assignment.py`) still finds what it needs.

### `timeline.md.jinja`
Markdown template rendered by `formaters.render_markdown`. Covers the same fields as the current text output (totals, focus times, IDE action / paste counts, chronological timeline of focus losses + bursts) but as proper Markdown — headings for each section, a bulleted timeline, fenced code blocks around fragment text.

### `viewer.py`
HTML player rendering. Owns everything in `player/`.

- `build_player_html(session: Session) -> str` — assembles the JSON bundle the player consumes (derived directly from the Session — `events`, `bursts`, `focus_intervals`, `idle_gaps`, `snapshots`, plus `metadata`/`summary` blocks), embeds it safely (`</` → `<\/`, ` `/` ` escaping), and renders `player/template.html` with inlined `player.css`, `player.js`, `highlight.min.js`.
- `write_player_html(session: Session, recording_file: Path) -> Path` — picks the output path next to the recording file (strips `.gz` and `.jsonl`, adds `.html`), writes the HTML, returns the path.

### `main.py`
CLI parsing + orchestration. Target: ~50 lines.

```python
def main(recording_file, excluded, output, _json, view):
    inputs = load_recording(recording_file, excluded)
    session = analyze_inputs(inputs)
    text = render_json(session) if _json else render_markdown(session)
    if output:
        output.write_text(text)
    else:
        print(text)
    if view:
        path = write_player_html(session, recording_file)
        print(f"Wrote playback HTML to {path}")

def entry():
    # ArgumentParser unchanged
    ...
```

### `analyze_assignment.py`
Minimal change to keep working with the new layout:

- Imports change: `from recan.session import analyze_inputs`, `from recan.utils import load_recording`.
- The current `gzip.open / json.loads` block is replaced with one call:
  ```python
  inputs = load_recording(recording, EXCLUDED_FILE_TYPES)
  metrics = analyze_inputs(inputs)
  ```
- It continues to consume the dict by key (`metrics['total_time']`, etc.) — the legacy keys are preserved on `Session`.

## Data flow

```
recording_file (.jsonl[.gz])
        │
        ▼
  utils.load_recording(path, excluded)   ← opens, gunzips, parses, filters
        │
        ▼
   list[dict]  (filtered, chronological)
        │
        ▼
  session.analyze_inputs(inputs)         ← single pass, pure
        │
        ▼
     Session
        │
        ├──► formaters.render_markdown(session) ──► timeline.md.jinja ──► str
        │              (or)
        ├──► formaters.render_json(session)     ──► json.dumps        ──► str
        │              │
        │              ▼
        │       stdout / --output file
        │
        └──► viewer.write_player_html(session, recording_file) ──► <recording>.html
```

## Behavior preservation

This refactor must not change the analyzer's outputs on existing recordings. Specifically:

- `--json` output keeps every key the current implementation produces. New Session keys (`events`, `focus_intervals`, `idle_gaps`, `bursts`, `snapshots`, `document`, `language`, `end_time`) are additive.
- `--view` HTML player consumes the same bundle shape it does today (`metadata` / `events` / `bursts` / `focus_intervals` / `idle_gaps` / `snapshots` / `summary`). `viewer.py` constructs that bundle from the Session.
- The default text output changes intentionally: from the current `print_timeline` string to a richer Markdown summary covering the same information. This is the one user-visible change.
- The delete-then-paste move-detection rule (a generated insert matching a previously-deleted fragment is treated as a move) carries over. The previous paste-then-delete retroactive cancellation is intentionally dropped — both `--json` (`total_pastes`, `total_generated_events`, `timeline`) and the player bundle (`bursts`, `summary`) will show pastes that the old code would have cancelled. This is the second intentional behavior change.
- Excluded file types still apply — they just apply at load time instead of inside the walker. Net behavior on excluded events is identical (they're dropped before they reach the walker).

## Build sequence

Each step leaves the package working so any regression is easy to bisect.

1. **Move pure helpers to `utils.py`** — `parse_ts`, `format_duration`, `format_ts`, `event_kind`, `is_generated_edit`, `apply_edit`, `language_from_extension`. Add `load_recording(path, excluded)`. Update `main.py` imports. Run analyze on a sample → output unchanged.
2. **Fill in `structure.py`** — add `Session`, `Burst`, `FocusInterval`, `IdleGap`, `Snapshot`, `TimelineEntry` next to the existing input TypedDicts.
3. **Create `session.py` with the unified walker** — port both `analyze_inputs` and `_build_playback_bundle` into one `analyze_inputs(inputs) -> Session`. Keep the old top-level functions in `main.py` temporarily as thin wrappers calling the new walker, so the CLI keeps working. Diff `--json` and `--view` outputs against pre-refactor on sample recordings.
4. **Create `formaters.py` and `timeline.md.jinja`** — implement `render_markdown` and `render_json`. Write the Markdown template covering the existing fields. Wire `main.py` to use them. Delete `print_timeline` / `_format_entry` / `_format_ts` from `main.py`.
5. **Create `viewer.py`** — move `_build_playback_bundle`, `_embed_safe_json`, `_render_player_html`, `PLAYER_DIR` here as `build_player_html` / `write_player_html`. Delete from `main.py`.
6. **Slim `main.py`** — should now be the ~10-line `main()` plus `entry()`.
7. **Update `analyze_assignment.py`** — switch to `from recan.session import analyze_inputs` and `from recan.utils import load_recording`. Replace its inline gzip/json block with `load_recording`. Verify CSV output is unchanged on a sample folder.

## Verification

After each step, run:

- `recan samples/<file>.jsonl.gz` → text output sane (matches old format byte-for-byte through step 3; matches new Markdown shape from step 4 onward).
- `recan samples/<file>.jsonl.gz --json` → JSON output is a superset of the old shape; legacy keys present and equal.
- `recan samples/<file>.jsonl.gz --view` → HTML player opens and behaves identically.
- After step 7: `python -m recan.analyze_assignment --folder samples/<assignment>` → CSV output identical to pre-refactor.

## Risks and mitigations

- **Risk:** Subtle drift in the unified walker vs. the two original functions on logic that's *meant* to carry over.
  **Mitigation:** Step 3 keeps both old functions callable as wrappers over the new walker; diff `--json` and the player bundle on every sample recording. Expect intentional differences in paste/generated counts (paste-then-delete cancellation is dropped) — confirm those are the *only* differences before deleting the old code.
- **Risk:** Markdown template output regresses content vs. the old text format (missing a field).
  **Mitigation:** Build the template from the current `print_timeline` field-by-field; cross-check against a known sample.
- **Risk:** `analyze_assignment.py` breaks because a key it reads gets renamed.
  **Mitigation:** The legacy keys it reads (`total_time`, `total_time_unfocused`, `total_generated_events`, `total_ide_actions`, `total_pastes`, `total_edits`, `start_time`) are explicitly preserved on the `Session` TypedDict.

## Open questions

None — all clarifying questions resolved during brainstorming.
