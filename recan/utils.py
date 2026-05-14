import gzip
import yaml
import json

from datetime import datetime
from pathlib import Path

from .structure import CREATE_FUNCTION_PATTERN, MAIN_BLOCK_PATTERN

GENERATED_MIN_CHARS_SINGLE_LINE = 20


def parse_ts(value: str) -> datetime:
    """Tolerant ISO timestamp parser.

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
    """Older recordings omit "type" — infer from which fields are present."""
    explicit = event.get("type")
    if explicit:
        return explicit
    if "focused" in event:
        return "focusStatus"
    if "newFragment" in event:
        return "edit"
    return "unknown"


def is_generated_edit(event: dict) -> bool:
    """Heuristic for "this insert wasn't typed character-by-character."

    Filters out IDE word-completion (e.g. PyCharm emits ``"n "`` when a
    completion fires after typing ``n`` + space) by requiring the fragment
    to be either multi-line or substantively long on a single line.
    """
    fragment = event.get("newFragment", "")
    if not fragment.strip():
        return False
    if not (any(c == ' ' for c in fragment) and any(c != ' ' for c in fragment)):
        return False
    if "\n" in fragment:
        return True
    return len(fragment) >= GENERATED_MIN_CHARS_SINGLE_LINE


def is_ide_action(group: list[dict]) -> bool:
    """Heuristic for this edit looks like an IDE auto-completion or refactor."""
    return bool(any(CREATE_FUNCTION_PATTERN.fullmatch(e['fragment']) for e in group) or any(
        MAIN_BLOCK_PATTERN.fullmatch(e['fragment']) is not None for e in group))


def apply_edit(document: str, offset: int, old_fragment: str, new_fragment: str) -> str:
    """Apply a single edit to the in-memory document string."""
    end = offset + len(old_fragment)
    return document[:offset] + new_fragment + document[end:]


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
    """Load a .jsonl or .jsonl.gz recording and drop events for excluded file types.

    Events whose ``document`` field ends in any of ``excluded_file_types``
    (e.g. ``.html``, ``.md``) are filtered out. The resulting list is the
    chronological event stream the analyzer consumes.
    """
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as f:
        events = [json.loads(line) for line in f]
    if not excluded_file_types:
        return events
    return [
        e for e in events
        if not any(e.get("document", "").endswith(ext) for ext in excluded_file_types)
    ]


def load_approved_fragments(approved_fragments_path: Path | None) -> list[str]:
    """Load the set of approved fragments from a file, if provided."""
    if approved_fragments_path is None:
        return []

    # list of flat strings, e.g. from a YAML or JSON file of approved code blocks
    approved_fragments_path = Path(approved_fragments_path)
    if approved_fragments_path.suffix in ['.yaml', '.yml']:
        return yaml.safe_load(approved_fragments_path.read_text())
    elif approved_fragments_path.suffix == '.json':
        return json.loads(approved_fragments_path.read_text())
    else:
        raise ValueError(f"Unsupported approved fragments file type: {approved_fragments_path.suffix}")
