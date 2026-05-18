import gzip
import yaml
import json

from pathlib import Path
from datetime import datetime

from .session import analyze_inputs
from .algorithm import DocumentMatcher
from .structure import CREATE_FUNCTION_PATTERN, MAIN_BLOCK_PATTERN, Session, GradescopeExport


def parse_ts(value: str) -> datetime:
    """
    Tolerant ISO timestamp parser.

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
    """
    Older recordings omit "type" — infer from which fields are present.
    """

    explicit = event.get("type")

    if explicit:
        return explicit

    if "focused" in event:
        return "focusStatus"

    if "newFragment" in event:
        return "edit"

    return "unknown"


def is_generated_edit(event: dict) -> bool:
    """
    Heuristic for "this insert wasn't typed character-by-character."

    Checks:
        - Is the inserted fragment longer than 1 character?
        - Is the inserted fragment pure whitespace?
        - Does the inserted fragment look like an IDE tab completion (e.g. "function_name(")?
        - Does the inserted fragment look like a commented-out line of code?
    """

    fragment = event.get("newFragment", "")

    # If a student is truly typing fragments should only be 1 char
    if len(fragment) == 1:
        return False

    # Pure whitespace
    if not fragment.strip():
        return False

    # IDE Tab completes
    if all(c in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in fragment.rstrip('()').lower()):
        return False

    # Commented out line
    # TODO: Make this more robust
    if fragment.startswith('#'):
        return False

    # If not one of the previous checks it is probably a "paste" event
    return True


def is_ide_action(event: dict) -> bool:
    """
    Heuristic for "this edit looks like an IDE auto-completion or refactor."

    Checks:
        - Does the event contain a fragment that looks like PyCharm's "create function" action?
        - Does the event contain a fragment that looks like PyCharm's main block generation when tab completing "if __name__ == "__main__""?
    """

    # TODO: Need a refactor/rename heuristic

    fragment = event.get("newFragment", "")

    if CREATE_FUNCTION_PATTERN.fullmatch(fragment):
        return True

    if MAIN_BLOCK_PATTERN.fullmatch(fragment):
        return True

    return False


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
    """
    Load a .jsonl or .jsonl.gz recording and drop events for excluded file types.

    If excluded_file_types is empty, returns all events.
    Otherwise, it filters out all documents that end with any of the specified extensions.
    """

    opener = gzip.open if path.suffix == ".gz" else open

    with opener(path, "rt") as f:
        events = [json.loads(line) for line in f]

    # Remove the first event if the old and new fragments are identical
    # Reflects the state of the document at the start of the recording.
    first_event = events[0]
    if first_event.get("oldFragment") == first_event["newFragment"]:
        events.remove(first_event)

    if not excluded_file_types:
        return events

    return [
        e for e in events
        if not any(e.get("document", "").endswith(ext) for ext in excluded_file_types)
    ]


def normalize_newlines(text, target='\n') -> str:
    """
    Normalize all newlines in the given text to the target newline character(s).

    Used to ensure consistent handling of approved fragments across different platforms and editors.
    """
    return target.join(text.splitlines())


def load_approved_fragments(approved_fragments_path: Path | None) -> str | None:
    if not approved_fragments_path:
       return ''

    approved_fragments_path = Path(approved_fragments_path)
    if approved_fragments_path.is_file():
        return normalize_newlines(approved_fragments_path.read_text())

    return ''


def resolve_additional(paths: Path | list[Path] | None, recording_file: Path) -> list[Path]:
    if not paths:
        return []

    candidates = []

    if isinstance(paths, Path):
        if paths.is_dir():
            jsonl_gz_paths = list(paths.glob("*.jsonl.gz"))
            jsonl_paths = list(paths.glob("*.jsonl"))

            candidates.append(jsonl_gz_paths + jsonl_paths)
        else:
            # TODO: Make a logging warning if the provided path is not a directory or file
            return []

    if isinstance(paths, list):
        # #TODO: Going to assume these are all jsonl or jsonl.gz files, but could add a check here and log a warning if not
        candidates = paths

    recording_resolved = recording_file.resolve()

    if not candidates:
        return []

    return [p for p in candidates if p.resolve() != recording_resolved]


def build_suffix_array(events: list[dict]) -> DocumentMatcher:
    """
    Build a suffix array from the event stream for efficient substring search.

    Usage: Used to see if recordings contain "internal paste" events from a session they
    already worked on, which can help identify when a student is pasting in code they
    previously wrote (and thus "approving" that fragment).
    """

    matcher = DocumentMatcher()

    for event in events:
        if event_kind(event) == "edit":
            matcher.apply_edit(
                offset=event["offset"],
                old_fragment=event["oldFragment"],
                new_fragment=event["newFragment"],
                is_generated=is_generated_edit(event),
                ts=parse_ts(event["timestamp"]),
            )

    matcher.finalize()

    return matcher


def load_session(recording_file: Path, approved_fragments_path: Path | None, excluded_file_types: list[str],
                 additional_recordings: Path | list[Path] | None) -> Session:
    """
    Analyze a recording with optional filters and additional context, returning a Session object with the results.

    Usage:
        - recording_file: Path to the main .jsonl or .jsonl.gz recording to analyze.
        - additional_recordings: Optional list of paths or a directory containing additional
          .jsonl.gz recordings to include in the analysis for more context. The main recording_file
          is automatically excluded if present.
    """
    approved_fragments = load_approved_fragments(approved_fragments_path)
    inputs = load_recording(recording_file, excluded_file_types)

    additional_inputs = [
        load_recording(recording, excluded_file_types)
        for recording in resolve_additional(additional_recordings, recording_file)
    ]

    additional_sessions = []
    for additional_input in additional_inputs:
        additional_sessions.append(build_suffix_array(additional_input))

    return analyze_inputs(inputs, additional_sessions, approved_fragments)


def is_gradescope_export(folder: Path) -> bool:
    if folder:
        folder = Path(folder)

    if folder.is_dir() and (folder / 'submission_metadata.yml').exists():
        return True

    return False


def generate_submission_student_map(folder: Path) -> dict[str, tuple[int, str]]:
    submission_metadata_path = folder / 'submission_metadata.yml'
    metadata = yaml.safe_load(submission_metadata_path.read_text())

    submission_student_info = {}

    for submission, info in metadata.items():
        submitters = info.get(':submitters', [])

        if len(submitters) != 1:
            continue

        student_id = submitters[0][':sid']
        student_email = submitters[0][':email']
        submission_student_info[submission] = (student_id, student_email)

    return submission_student_info


def generate_problem_set(problems_path: Path) -> set[str]:
    if not problems_path:
        return set()

    problems_path = Path(problems_path)
    problems = yaml.safe_load(problems_path.read_text())

    if not isinstance(problems, list) or not all(isinstance(p, str) for p in problems):
        raise ValueError("Problems file must contain a list of strings.")

    return set(problems)
