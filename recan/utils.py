import gzip
import zlib
import yaml
import json

from glob import glob
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


def _get_recordings(paths: list[Path]) -> list[Path]:
    """
    Expand a list of file paths and/or glob patterns into a sorted, deduplicated
    list of recording files.

    Each entry may be:
        - an existing file (kept as-is)
        - a glob pattern (expanded against the filesystem)

    Useful for CLI invocations where the shell may pre-expand a glob into many
    args, or pass a single unexpanded pattern.
    """
    seen: set[Path] = set()
    out: list[Path] = []

    for path in paths:
        if path.is_file():
            candidates = [path]
        else:
            candidates = [Path(p) for p in glob(str(path)) if Path(p).is_file()]

        for c in candidates:
            resolved = c.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            out.append(c)

    return sorted(out)


def _load_recording(path: Path, excluded_file_types: list[str]) -> list[dict]:
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
        print(f"Error reading {path}: {e}")
        return []

    # Remove the first event if the old and new fragments are identical
    # Reflects the state of the document at the start of the recording.
    first_event = events[0]
    if first_event.get("oldFragment") == first_event.get("newFragment"):
        events.remove(first_event)

    if not excluded_file_types:
        return events

    return [
        e for e in events
        if not any(e.get("document", "").endswith(ext) for ext in excluded_file_types)
    ]


def _is_problem(recording: Path, problems: set[str]) -> bool:
    """
    Check if the recording's document matches any of the specified problems.

    If problems is empty, returns True for all recordings.
    """
    if not problems:
        return True

    name = recording.name.split('.')[0]

    return any(p == name for p in problems)



def load_recordings(paths: list[Path], problems: Path, excluded_file_types: list[str]) -> list[tuple[Path, list[dict]]]:
    """
    Expand the given paths (files and/or glob patterns) and load each recording,
    returning a list of (recording_path, events) tuples.
    """
    recordings = _get_recordings(paths)

    inputs = []
    for recording in recordings:
        inputs.append((recording, _load_recording(recording, excluded_file_types)))

    problem_set = generate_problem_set(problems)

    return [
        (recording, events) for recording, events in inputs
        if _is_problem(recording, problem_set)
    ]


def splice(doc: str, offset: int, old: str, new: str) -> str:
    """Replace `old` at `offset` in `doc` with `new`, returning the rewritten doc."""
    before_edit = doc[:offset]
    after_edit = doc[offset + len(old):]
    return before_edit + new + after_edit


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
