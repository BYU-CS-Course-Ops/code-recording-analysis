import gzip
import json
import re
import statistics
import zlib

from pathlib import Path
from argparse import ArgumentParser

from analyze import analyze_inputs


EXCLUDED_FILE_TYPES = ['.html']

# assignment_<aid>_export-submission_<student_id>__<problem>.recording
_FILENAME_RE = re.compile(
    r'^assignment_\d+_export-submission_(?P<student_id>[^_]+)__(?P<problem>.+?)\.recording$'
)

CSV_COLUMNS = [
    'student_id',
    'total_time',
    'time_focused',
    'time_unfocused',
    'unfocused_pct',
    'num_generated_events',
    'num_ide_actions',
    'num_pastes',
    'num_edits',
    'start_time',
]
NUMERIC_COLUMNS = [
    'total_time',
    'time_focused',
    'time_unfocused',
    'unfocused_pct',
    'num_generated_events',
    'num_ide_actions',
    'num_pastes',
    'num_edits',
]


def _parse_filename(path: Path) -> tuple[str, str] | None:
    """Extract (student_id, problem_name) from a recording filename, or None."""
    name = path.name
    if name.endswith('.jsonl.gz'):
        name = name[:-len('.jsonl.gz')]
    elif name.endswith('.jsonl'):
        name = name[:-len('.jsonl')]
    m = _FILENAME_RE.match(name)
    if not m:
        return None
    return m.group('student_id'), m.group('problem')


def _row_for_student(student_id: str, data: dict) -> dict:
    total_time = float(data['total_time'])
    time_unfocused = float(data['total_time_unfocused'])
    time_focused = total_time - time_unfocused
    unfocused_pct = (time_unfocused / total_time) if total_time > 0 else 0.0
    start_time = data.get('start_time')
    return {
        'student_id': student_id,
        'total_time': total_time,
        'time_focused': time_focused,
        'time_unfocused': time_unfocused,
        'unfocused_pct': unfocused_pct,
        'num_generated_events': int(data['total_generated_events']),
        'num_ide_actions': int(data['total_ide_actions']),
        'num_pastes': int(data['total_pastes']),
        'num_edits': int(data['total_edits']),
        'start_time': start_time.isoformat() if start_time else '',
    }


def _format_cell(col: str, value) -> str:
    if value == '' or value is None:
        return ''
    if col in ('num_generated_events', 'num_ide_actions', 'num_pastes', 'num_edits'):
        return str(value) if isinstance(value, int) else f'{value:.2f}'
    if col == 'unfocused_pct':
        return f'{value:.4f}'
    if col in ('total_time', 'time_focused', 'time_unfocused'):
        return f'{value:.3f}'
    return str(value)


def _aggregate_rows(rows: list[dict]) -> list[dict]:
    """Build mean/median/stdev footer rows for the numeric columns."""
    footer = []
    for label, fn in (('__mean__', statistics.fmean), ('__median__', statistics.median)):
        agg = {col: '' for col in CSV_COLUMNS}
        agg['student_id'] = label
        for col in NUMERIC_COLUMNS:
            agg[col] = fn(r[col] for r in rows)
        footer.append(agg)

    stdev_row = {col: '' for col in CSV_COLUMNS}
    stdev_row['student_id'] = '__stdev__'
    if len(rows) > 1:
        for col in NUMERIC_COLUMNS:
            stdev_row[col] = statistics.stdev(r[col] for r in rows)
    footer.append(stdev_row)
    return footer


def generate_csv(problem_name: str, students: dict, out_dir: Path) -> None:
    rows = [_row_for_student(sid, data) for sid, data in sorted(students.items())]
    if not rows:
        print(f"Skipping {problem_name}: no submissions parsed")
        return

    rows.extend(_aggregate_rows(rows))

    csv_path = out_dir / f'{problem_name}_analysis.csv'
    with open(csv_path, 'w') as f:
        f.write(','.join(CSV_COLUMNS) + '\n')
        for row in rows:
            f.write(','.join(_format_cell(col, row[col]) for col in CSV_COLUMNS) + '\n')
    print(f"Generated CSV for {problem_name}: {csv_path}")


def analyze_assignment(folder: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, dict[str, dict]] = {}
    for recording in folder.glob('*.jsonl.gz'):
        parsed = _parse_filename(recording)
        if parsed is None:
            print(f"Warning: skipping {recording.name} — filename does not match expected pattern")
            continue
        student_id, problem_name = parsed

        opener = gzip.open if recording.suffix == '.gz' else open
        try:
            with opener(recording, 'rt') as f:
                inputs = [json.loads(line) for line in f]
            metrics = analyze_inputs(inputs, EXCLUDED_FILE_TYPES)
        except (OSError, EOFError, json.JSONDecodeError, zlib.error) as exc:
            print(f"Warning: skipping {recording.name} — failed to read ({type(exc).__name__}: {exc})")
            continue

        results.setdefault(problem_name, {})[student_id] = metrics

    for problem, students in results.items():
        generate_csv(problem, students, out_dir)


def main():
    parser = ArgumentParser()
    parser.add_argument('--folder', type=Path, required=True, help='Path to the folder containing the assignment files')
    parser.add_argument('--out-dir', type=Path, default=Path.cwd(), help='Directory to write per-problem CSVs (default: cwd)')
    args = parser.parse_args()

    analyze_assignment(args.folder, args.out_dir)


if __name__ == '__main__':
    main()
