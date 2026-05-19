import gzip
import zlib
import yaml
import json

from pathlib import Path
from datetime import datetime


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


def language_from_extension(document: str) -> str:
    """
    Map document filename to a highlight.js language name.
    """
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

    try:
        with opener(path, "rt") as f:
            events = [json.loads(line) for line in f]
    except (gzip.BadGzipFile, zlib.error, json.JSONDecodeError, OSError) as e:
        raise RuntimeError(f"Failed to read recording {path}: {type(e).__name__}: {e}") from e

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
