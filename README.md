# recan

Analyze IDE recording files captured by the BeanLab IDE recorder.

`recan` reads a single recording (JSONL or gzipped JSONL), reconstructs the
session timeline, and reports on focus, edits, pastes, and IDE-generated
actions. It can also render a self-contained HTML player so you can scrub
through the recording with the original document state.

## Install

This is a Poetry project targeting Python 3.13+.

```bash
poetry install
```

## Usage

```bash
recan <recording_file> [--exclude EXT ...] [--output PATH] [--json] [--view]
```

| Flag | Description |
| --- | --- |
| `recording_file` | Path to a `.jsonl` or `.jsonl.gz` recording. |
| `--exclude` | File extensions to ignore (e.g. `--exclude .html .md`). |
| `--output` | Write results to a file instead of stdout. |
| `--json` | Emit machine-readable JSON instead of the human-readable timeline. |
| `--view` | Also render a standalone HTML player next to the recording. |

### Example

```bash
recan samples/session.jsonl.gz --exclude .html --view
```

Produces a focus/edit summary on stdout and writes `samples/session.jsonl.html`,
a single-file player you can open in any browser.

## What it reports

- **Total / focused / unfocused time** — paired from `focusStatus` events.
- **Edits** — every recorded document change.
- **Pastes** — chunked inserts that look like a single clipboard or completion event.
- **IDE actions** — tightly-grouped bursts of generated edits (e.g. PyCharm
  "Generate constructor"), distinguished from pastes by burst size.
- **Idle gaps** — pauses between events longer than the idle threshold.
- **Move/re-paste detection** — a paste that gets deleted, or a delete that
  reappears as a paste, is treated as a move and excluded from paste counts.

## HTML player (`--view`)

The player inlines the recording, CSS, JS, and a syntax highlighter into a
single HTML file. Open it directly — no server required. You can scrub the
timeline, jump between bursts and idle gaps, and see the document state at
any point.

## Stats analysis

Cross-submission and assignment-level stats analysis is coming soon.

## Authors

- Gordon Bean — `gbean@cs.byu.edu`
- Robert Greathouse — `robbykap@byu.edu`

## License

MIT
