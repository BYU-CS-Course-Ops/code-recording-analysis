import gzip
import re
import zlib
import yaml
import json

from glob import glob
from pathlib import Path
from datetime import datetime, timezone


def parse_ts(value: str) -> datetime:
    """Parse an ISO timestamp, accepting the recorder's naive timestamps."""
    return datetime.fromisoformat(value)


def to_utc_iso8601(value) -> str:
    """Return a canonical UTC ISO-8601 timestamp for a datetime or string."""
    if value is None:
        return ""
    timestamp = parse_ts(value) if isinstance(value, str) else value
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def format_duration(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_ts(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "N/A"


def language_from_extension(document: str, content: str | None = None) -> str:
    """
    Map a document filename to a display language name.

    Primarily keys off the file extension. When the document name has no
    extension (or an unknown one) and ``content`` is supplied, fall back to a
    lightweight content sniff so extension-less recordings still get a useful
    label instead of silently defaulting to plaintext.
    """
    ext = Path(document).suffix.lower()
    lang = {
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
    }.get(ext)

    if lang:
        return lang
    if content:
        return language_from_content(content)
    return "plaintext"


def language_from_content(content: str) -> str:
    """
    Best-effort language guess from a source snippet when the document name
    carries no usable extension. Deliberately conservative: it only returns a
    concrete language on a clear signal, otherwise "plaintext".
    """
    sample = content[:4000]
    if not sample.strip():
        return "plaintext"

    # Python — imports, defs, classes, or the __main__ guard.
    if re.search(r"^\s*(?:import \w|from \w[\w.]* import |def \w+\s*\(|class \w+\s*[:\(]|@\w)", sample, re.M) \
            or re.search(r"__name__\s*==\s*['\"]__main__['\"]", sample):
        return "python"

    # C / C++ — preprocessor includes (check before generic braces).
    if re.search(r"^\s*#\s*include\s*[<\"]", sample, re.M):
        return "cpp" if re.search(r"std::|template\s*<|::|\bclass\b", sample) else "c"

    # JavaScript / TypeScript — declarations or arrow functions.
    if re.search(r"\b(?:function|const|let|var)\b|=>", sample) and ";" in sample:
        return "typescript" if re.search(r":\s*(?:string|number|boolean|any)\b|\binterface\b", sample) else "javascript"

    # JSON — a single top-level object/array.
    stripped = sample.strip()
    if stripped[:1] in "{[" and re.search(r'"\w+"\s*:', sample):
        return "json"

    return "plaintext"


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
            candidates = [Path(p) for p in glob(str(path), recursive=True) if Path(p).is_file()]

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
        with opener(path, "rt", encoding="utf-8") as f:
            events = [json.loads(line) for line in f]
    except (EOFError, UnicodeError, zlib.error, json.JSONDecodeError, OSError) as e:
        print(f"Error reading {path}: {e}")
        return []

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
    """Normalize CRLF and CR line endings without dropping trailing newlines."""
    return text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', target)


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
