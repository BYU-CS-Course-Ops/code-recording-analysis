import json
from datetime import datetime
from pathlib import Path

import jinja2

from recan.structure import Session
from recan.session import annotation_counts
from recan.utils import format_duration, format_ts, to_utc_iso8601

_TEMPLATE_PATH = Path(__file__).resolve().parent / "timeline.md.jinja"

_MARKDOWN_SEPARATOR = "\n\n---\n\n"


def _markdown_env() -> jinja2.Environment:
    return jinja2.Environment(
        autoescape=False,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _render_one_markdown(session: Session, template: jinja2.Template) -> str:
    return template.render(
        session=session,
        format_ts=format_ts,
        format_duration=format_duration,
        len=len,
        counts=annotation_counts(session),
    )


def render_markdown(sessions: list[Session]) -> str:
    """
    Render each Session as Markdown and join them with a horizontal-rule separator.
    """
    template = _markdown_env().from_string(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return _MARKDOWN_SEPARATOR.join(_render_one_markdown(s, template) for s in sessions)


def render_json(sessions: list[Session]) -> str:
    """Serialize Sessions with the canonical UTC timestamp representation."""
    def encode_datetime(value: object) -> str:
        if isinstance(value, datetime):
            return to_utc_iso8601(value)
        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

    return json.dumps(sessions, default=encode_datetime, indent=4)
