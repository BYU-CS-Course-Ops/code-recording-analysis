import csv
import logging

from pathlib import Path, PureWindowsPath

from recan.structure import Session
from recan.session import load_sessions, annotation_counts

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
    'num_chars',
    'num_typed_chars',
    'num_pasted_chars',
    'num_deleted_chars',
    'starts_with_starter_code',
    'review_severity',
    'time_typing',
    'num_recording_issues',
    'num_skipped_edits',
    'analysis_incomplete',
]

NUMERIC_COLUMNS = [
    'total_time',
    'time_focused',
    'time_unfocused',
    'time_typing',
    'num_unfocused_events',
    'num_ide_actions',
    'num_unapproved_pastes',
    'num_approved_pastes',
    'num_internal_pastes',
    'num_edits',
    'num_chars',
    'num_typed_chars',
    'num_pasted_chars',
    'num_deleted_chars',
    'num_recording_issues',
    'num_skipped_edits',
]


def get_submissions(folder: Path) -> list[Path]:
    return [
        submission for submission in folder.iterdir() if submission.is_dir()
    ]


def _row_from_session(submission, session_info: Session, student_id, student_email) -> dict:
    total_time = session_info['total_time']
    time_unfocused = session_info['total_time_unfocused']
    counts = annotation_counts(session_info)
    recording_issues = [
        annotation for annotation in session_info['annotations']
        if annotation['kind'] == 'recording_issue'
    ]
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
        'time_typing': session_info['total_time_typing'],
        'num_unfocused_events': counts['unfocused'],
        'num_ide_actions': counts['ide_action'],
        'num_unapproved_pastes': counts['unapproved_paste'],
        'num_approved_pastes': counts['approved_paste'],
        'num_internal_pastes': counts['internal_paste'],
        'num_edits': session_info['total_edits'],
        'num_chars': session_info['total_chars'],
        'starts_with_starter_code': session_info['starts_with_starter_code'],
        'review_severity': session_info['review_severity'],
        'num_typed_chars': session_info['total_typed_chars'],
        'num_pasted_chars': session_info['total_pasted_chars'],
        'num_deleted_chars': session_info['total_deleted_chars'],
        'num_recording_issues': len(recording_issues),
        'num_skipped_edits': sum(issue.get('skipped_edits', 0) for issue in recording_issues),
        # A later snapshot restores the document, not the missing edit history.
        # Confirmed stale replacements alone do not make analysis incomplete.
        'analysis_incomplete': any(
            issue.get('action') in {'interrupted', 'resynchronized'}
            for issue in recording_issues
        ),
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
                       submission_student_map: dict[str, tuple[int, str]],
                       starter_code_paths=None) -> None:
    stats: list[dict] = []
    excluded_count = 0
    unreadable: list[tuple[Path, str]] = []
    processed = 0

    for submission in get_submissions(folder):

        recordings = Path(submission) / '*.jsonl.gz'

        sessions = load_sessions(
            [recordings], problems, approved_fragments_path, excluded_file_types,
            starter_code_paths
        )

        student_id, student_email = submission_student_map.get(submission.name, (None, None))

        for session in sessions:
            stats.append(_row_from_session(submission, session, student_id, student_email))
            processed += 1

    write_csv(output_path, stats)

    _log_summary(processed, excluded_count, unreadable, len(stats))
