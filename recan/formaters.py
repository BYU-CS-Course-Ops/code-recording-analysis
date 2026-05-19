import json
from pathlib import Path
from textwrap import dedent

import jinja2

from recan.structure import Session
from recan.utils import format_duration, format_ts

_TEMPLATE_PATH = Path(__file__).resolve().parent / "timeline.md.jinja"


def render_markdown(session: Session) -> str:
    """Render the human-readable Markdown summary from a Session."""
    template_text = _TEMPLATE_PATH.read_text(encoding="utf-8")
    env = jinja2.Environment(
        autoescape=False,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.from_string(template_text)
    return template.render(
        session=session,
        format_ts=format_ts,
        format_duration=format_duration,
        len=len,
        dedent=dedent,
    )


def render_json(session: Session) -> str:
    """Serialize the Session as indented JSON for the --json flag."""
    return json.dumps(session, default=str, indent=4)
