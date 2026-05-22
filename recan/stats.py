import csv
import logging

from pathlib import Path, PureWindowsPath

from recan.structure import Session
from recan.session import load_sessions

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
    'num_internal_pastes',
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
    'num_internal_pastes',
    'num_edits',
    'num_chars',
]


def get_submissions(folder: Path) -> list[Path]:
    return [
        submission for submission in folder.iterdir() if submission.is_dir()
    ]


def _row_from_session(submission, session_info: Session, student_id, student_email) -> dict:
    total_time = session_info['total_time']
    time_unfocused = session_info['total_time_unfocused']
    snapshots = session_info['snapshots']
    final_doc = snapshots[-1]['document_text'] if snapshots else ''
    return {
        'assignment': submission.parent.name,
        'submission': submission.name,
        'problem': PureWindowsPath(session_info['document']).name,
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
        'num_internal_pastes': session_info['total_internal_pastes'],
        'num_edits': session_info['total_edits'],
        'num_chars': len(final_doc)
    }


def write_csv(output_path: Path, rows: list[dict]) -> None:
    with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            for col in NUMERIC_COLUMNS:
                row[col] = row[col] if row[col] is not None else 0
            writer.writerow(row)


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


def generate_stat_csvs(folder: Path, output_path: Path,
                       problems: Path, excluded_file_types: list[str],
                       approved_fragments_path: Path | None,
                       submission_student_map: dict[str, tuple[int, str]]) -> None:
    stats: list[dict] = []
    excluded_count = 0
    unreadable: list[tuple[Path, str]] = []
    processed = 0

    for submission in get_submissions(folder):

        recordings = Path(submission) / '*.jsonl.gz'

        sessions = load_sessions([recordings], problems, approved_fragments_path, excluded_file_types)

        student_id, student_email = submission_student_map.get(submission.name, (None, None))

        for session in sessions:
            stats.append(_row_from_session(submission, session, student_id, student_email))
            processed += 1

    write_csv(output_path, stats)

    _log_summary(processed, excluded_count, unreadable, len(stats))
