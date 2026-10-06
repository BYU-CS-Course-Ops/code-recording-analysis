# recan

Analyze IDE recording files captured by the BeanLab IDE recorder.

`recan` reads one or more recordings (`.jsonl` or `.jsonl.gz`), reconstructs each session's timeline, and reports on
focus, edits, pastes, IDE-generated actions, and idle gaps. It can also render a self-contained HTML player for
scrubbing through a recording and produce CSVs for batch / cross-submission analysis of a Gradescope export.

## Install

```bash
pip install byu-recan
```

That registers a `recan` console script.

## Usage

```bash
recan <command> [options]
```

`recan` has three subcommands:

| Command               | Purpose                                                                    |
|-----------------------|----------------------------------------------------------------------------|
| [`summary`](#summary) | Analyze one or more recordings and print/write a Markdown or JSON summary. |
| [`view`](#view)       | Render a self-contained HTML player for a recording.                       |
| [`stats`](#stats)     | Walk a Gradescope export and emit a per-submission CSV of metrics.         |

### Common arguments

Every subcommand accepts the same set of recording-filtering options:

| Flag                             | Description                                                                                                                                                                                                                      |
|----------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `--exclude .ext1 .ext2 …`        | File extensions to ignore (e.g. `--exclude .html .md`). Events whose `document` ends in any listed extension are dropped.                                                                                                        |
| `--approved-pastes approved.txt` | Path to a file of approved fragments (e.g. starter code, scaffolding given to students). Matching pastes are classified as `approved` rather than `unapproved`.                                                                  |
| `--problems problems.yaml`       | YAML list of problem names to include. A recording is kept only if its filename (before the first `.`) matches an entry. Omit to include everything. See [`samples/problems_to_analyze.yaml`](samples/problems_to_analyze.yaml). |
| `--starter-code [FILE …]`         | Optionally supply starter files. Each recording is matched by exact basename (including Windows document paths); a mismatch is flagged. Files are also approved paste sources. With no files, the value is an empty list. |

Recording paths are positional and may be:

- a single file (`samples/session.jsonl.gz`),
- many files,
- or a glob pattern (`'submissions/**/*.jsonl.gz'` — quote it so your shell doesn't pre-expand).

---

### `summary`

Generate a per-session summary, either as human-readable Markdown (default) or structured JSON.

```bash
recan summary <recording_file(s)> [common args] [--output PATH] [--json]
```

| Flag              | Description                                              |
|-------------------|----------------------------------------------------------|
| `recording_files` | One or more recording files or glob patterns (required). |
| `--output PATH`   | Write the rendered summary to a file instead of stdout.  |
| `--json`          | Emit structured JSON instead of Markdown.                |

**Example**

```bash
recan summary samples/session.jsonl.gz --exclude .html --approved-pastes samples/approved_fragments.md
```

---

### `view`

Render a self-contained HTML player. The player inlines the recording, CSS, JS, and a syntax highlighter into one file —
open it directly, no server required. You can scrub the timeline, jump between bursts and idle gaps, and inspect the
document state at any point.

```bash
recan view <recording_file(s)> [common args] [--no-open]
```

| Flag              | Description                                                     |
|-------------------|-----------------------------------------------------------------|
| `recording_files` | One or more recording files or glob patterns (required).        |
| `--no-open`       | Write the player without opening it in a browser.                 |

**Example**

```bash
recan view samples/session.jsonl.gz --exclude .html
```

---

### `stats`

Walk a Gradescope export folder and write a single CSV with one row per
recording in each submission. The folder is expected to contain a `submission_metadata.yml`
plus per-submission subdirectories holding the recording `.jsonl.gz` files.

```bash
recan stats <folder> <output.csv> [common args]
```

| Flag     | Description                                      |
|----------|--------------------------------------------------|
| `folder` | Path to the Gradescope export folder (required). |
| `output` | Path to write the CSV (required).                |

The CSV includes: `assignment`, `submission`, `problem`, `student_id`,
`student_email`, `start_time`, `end_time`, `total_time`, `time_focused`,
`time_unfocused`, `num_unfocused_events`, `num_ide_actions`,
`num_unapproved_pastes`, `num_approved_pastes`, `num_internal_pastes`,
`num_edits`, `num_chars`, `num_typed_chars`, `num_pasted_chars`, `num_deleted_chars`, `starts_with_starter_code`, and `time_typing`.

**Example**

```bash
recan stats samples/assignment_7360081_export out.csv \
    --problems samples/problems_to_analyze.yaml \
    --approved-pastes samples/approved_fragments.md \
    --exclude .html .md
```

---

## What gets reported

- **Total / focused / unfocused time** — paired from `focusStatus` events.
- **Edits** — every recorded document change.
- **Starter-code matching** — `starts_with_starter_code` is `true`, `false`, or `null` when no starter basename matches; only `false` is flagged.
- **Pastes** — chunked inserts that look like a single clipboard/completion event, classified as:
    - `approved` — matches a fragment in `--approved-pastes`,
    - `internal` — content was previously present in this recording (a move/re-paste),
    - `unapproved` — everything else.
- **IDE actions** — tightly-grouped bursts of generated edits (e.g. PyCharm "generate constructor", VS Code snippet
  expansion), distinguished from pastes by burst shape.
- **Idle gaps** — pauses between events longer than the idle threshold.

## Development

Install the project and development dependencies with `poetry install`, then run
`poetry run python -m pytest -q`. Tests use synthetic recordings and need no private sample files.
Pytest plugin autoload is disabled to prevent unrelated globally installed plugins from changing the suite.
The suite is grouped by behavior under `tests/matcher`, `tests/session`,
`tests/ide-actions`, and `tests/browser`; cross-cutting regressions live at the test root.

The optional browser tests run the generated player in headless Chromium:

```bash
poetry install --with browser
poetry run playwright install chromium
poetry run python -m pytest -q
```

Without Playwright or its Chromium installation, the browser tests are skipped.
The remaining tests still run normally.

See [MAINTENANCE.md](MAINTENANCE.md) for cleanup details and remaining review findings.

## Authors

- Gordon Bean — `gbean@cs.byu.edu`
- Robert Greathouse — `robbykap@byu.edu`

## License

MIT
