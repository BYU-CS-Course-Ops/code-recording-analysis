import json
from pathlib import Path
from textwrap import dedent

import jinja2

from recan.structure import Session
from recan.utils import format_duration, format_ts

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
        dedent=dedent,
    )


def render_markdown(sessions: list[Session]) -> str:
    """
    Render each Session as Markdown and join them with a horizontal-rule separator.
    """
    template = _markdown_env().from_string(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return _MARKDOWN_SEPARATOR.join(_render_one_markdown(s, template) for s in sessions)


def render_json(sessions: list[Session]) -> str:
    """Serialize the list of Sessions as a single indented JSON array."""
    return json.dumps(sessions, default=str, indent=4)
