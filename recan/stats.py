import csv
import gzip
import json
import logging
import yaml
import zlib

from pathlib import Path

from recan.session import analyze_inputs
from recan.structure import Session
from recan.utils import load_recording, load_approved_fragments

log = logging.getLogger(__name__)

CSV_COLUMNS = [
    'assignment',
    'submission',
    'problem',
    'student_id',
    'student_email',
    'start_time',
    'end_time',
    'total_time',
    'time_focused',
    'time_unfocused',
    'num_unfocused_events',
    'num_ide_actions',
    'num_unapproved_pastes',
    'num_approved_pastes',
    'num_edits',
    'num_chars'
]

NUMERIC_COLUMNS = [
    'total_time',
    'time_focused',
    'time_unfocused',
    'num_unfocused_events',
    'num_ide_actions',
    'num_unapproved_pastes',
    'num_approved_pastes',
    'num_edits',
    'num_chars',
]


def generate_submission_student_map(submission_metadata: dict) -> dict[str, tuple[int, str]]:
    submission_student_info = {}

    for submission, info in submission_metadata.items():
        submitters = info.get(':submitters', [])

        if len(submitters) != 1:
            log.warning("Skipping %s: %d submitters (expected 1)", submission, len(submitters))
            continue

        student_id = submitters[0][':sid']
        student_email = submitters[0][':email']
        submission_student_info[submission] = (student_id, student_email)

    return submission_student_info


def _row_from_session(problem, submission, session_info: Session, student_id, student_email) -> dict:
    total_time = session_info['total_time']
    time_unfocused = session_info['total_time_unfocused']
    snapshots = session_info['snapshots']
    final_doc = snapshots[-1]['document_text'] if snapshots else ''
    return {
        'assignment': submission.parent.name,
        'submission': submission.name,
        'problem': problem,
        'student_id': student_id,
        'student_email': student_email,
        'start_time': session_info['start_time'],
        'end_time': session_info['end_time'],
        'total_time': total_time,
        'time_focused': total_time - time_unfocused,
        'time_unfocused': time_unfocused,
        'num_unfocused_events': len(session_info['focus_intervals']),
        'num_ide_actions': session_info['total_ide_actions'],
        'num_unapproved_pastes': session_info['total_unapproved_pastes'],
        'num_approved_pastes': session_info['total_approved_pastes'],
        'num_edits': session_info['total_edits'],
        'num_chars': len(final_doc)
    }


def generate_stat_csvs(folder: Path, output_path: Path, problems: set[str] | None, excluded_file_types: list[str],
                       approved_fragments_path: Path | None,
                       submission_student_map: dict[str, tuple[int, str]]) -> None:
    stats: list[dict] = []
    excluded_count = 0
    unreadable: list[tuple[Path, str]] = []
    processed = 0

    for submission in folder.iterdir():
        if not submission.is_dir():
            continue
        recordings = submission.glob('*.jsonl.gz')

        for recording in recordings:
            problem = recording.name.split('.')[0]

            if problems:
                if not problem in problems:
                    log.debug("Excluded by whitelist: %s", recording)
                    excluded_count += 1
                    continue

            try:
                inputs = load_recording(recording, excluded_file_types)
            except (gzip.BadGzipFile, zlib.error, json.JSONDecodeError, OSError) as e:
                unreadable.append((recording, f"{type(e).__name__}: {e}"))
                continue

            approved_fragments = load_approved_fragments(approved_fragments_path)
            session_info = analyze_inputs(inputs, approved_fragments)

            student_id, student_email = submission_student_map.get(submission.name, (None, None))

            stats.append(_row_from_session(problem, submission, session_info, student_id, student_email))
            processed += 1

    with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in stats:
            for col in NUMERIC_COLUMNS:
                row[col] = row[col] if row[col] is not None else 0
            writer.writerow(row)

    _log_summary(processed, excluded_count, unreadable, len(stats))


def _log_summary(processed: int, excluded: int, unreadable: list[tuple[Path, str]], csv_count: int) -> None:
    log.info("")
    log.info("=== Summary ===")
    log.info("Processed recordings: %d", processed)
    log.info("CSVs written:         %d", csv_count)
    log.info("Excluded by pattern:  %d", excluded)
    log.info("Unreadable:           %d", len(unreadable))

    if unreadable:
        log.warning("")
        log.warning("The following %d file(s) could not be read and may need manual review:", len(unreadable))
        for path, reason in unreadable:
            log.warning("  %s  --  %s", path, reason)


def load_whitelist_problems(include: str | Path) -> set[str]:
    include_path = Path(include)
    if include_path.is_file():
        if include_path.suffix in ('.yml', '.yaml'):
            return yaml.safe_load(include_path.read_text())
        elif include_path.suffix == '.json':
            return json.loads(include_path.read_text())
        else:
            raise ValueError(f"Unsupported include file type: {include_path.suffix}")
    return {p.strip() for p in str(include).split(',') if p.strip()}


def generate_stats(folder: Path, output_path: Path, include: str | Path, excluded_file_types: list[str],
                   approved_fragments_path: Path | None) -> None:
    folder = Path(folder)
    output_path = Path(output_path)

    try:
        submission_metadata_path = folder / 'submission_metadata.yml'
        metadata = yaml.safe_load(submission_metadata_path.read_text())

    except FileNotFoundError:
        raise FileNotFoundError(
            f"Warning: no submission_metadata.yaml found in {folder}, please ensure you are pointing to a Gradescope export folder")

    submission_student_map = generate_submission_student_map(metadata)

    # list of problems to analysis, or None to include all
    problems = None
    if include:
        problems = load_whitelist_problems(include)

    generate_stat_csvs(folder, output_path, problems, excluded_file_types, approved_fragments_path, submission_student_map)
