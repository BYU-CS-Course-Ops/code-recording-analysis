import json
from pathlib import Path
from datetime import datetime

import jinja2

from recan.structure import Session


_PLAYER_DIR = Path(__file__).resolve().parent / "player"


def _iso_z(ts) -> str:
    """Format a datetime as ISO with trailing Z, matching the player's expected shape."""
    if ts is None:
        return ""
    if isinstance(ts, datetime):
        return ts.isoformat().replace("+00:00", "Z")
    return str(ts)


def _to_session_bundle(session: Session) -> dict:
    """Project a Session into the per-session JSON bundle the player consumes."""
    return {
        "document":                session["document"],
        "language":                session["language"],
        "initial_document":        session.get("initial_document", ""),
        "start_time":              _iso_z(session["start_time"]),
        "end_time":                _iso_z(session["end_time"]),
        "total_time":              session["total_time"],
        "total_time_unfocused":    session["total_time_unfocused"],
        "total_edits":             session["total_edits"],
        "total_unapproved_pastes": session["total_unapproved_pastes"],
        "total_approved_pastes":   session["total_approved_pastes"],
        "total_internal_pastes":   session["total_internal_pastes"],
        "total_ide_actions":       session["total_ide_actions"],
        "total_generated_events":  session.get("total_generated_events", 0),
        "events":                  session["events"],
        "focus_intervals":         [dict(fi) for fi in session["focus_intervals"]],
        "idle_gaps":               [dict(g) for g in session["idle_gaps"]],
        "bursts":                  [dict(b) for b in session["bursts"]],
        "snapshots":               session["snapshots"],
        "timeline":                session.get("timeline", []),
    }


def _to_player_bundle(sessions: list[Session] | Session) -> list[dict]:
    """Project one or more Sessions into the list-of-sessions bundle the player accepts."""
    if isinstance(sessions, list):
        return [_to_session_bundle(s) for s in sessions]
    return [_to_session_bundle(sessions)]


def _embed_safe_json(bundle: list[dict]) -> str:
    """JSON-encode a bundle for safe embedding in an HTML <script> tag.

    Two specific dangers when embedding JSON in HTML inside a <script> block:
    1. The substring ``</script`` ends the script element early. Escape ``</`` to ``<\\/``.
    2. U+2028 / U+2029 are valid in JSON but illegal in JS string literals.
    """
    text = json.dumps(bundle, ensure_ascii=False, default=str)
    text = text.replace("</", "<\\/")
    text = text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return text


def _primary_document(bundle: list[dict]) -> str:
    """Pick a display name for the page title — first session's document."""
    if not bundle:
        return "recording"
    return bundle[0].get("document") or "recording"


def build_player_html(sessions: list[Session] | Session) -> str:
    """Render the static HTML player by inlining the bundle and assets via Jinja2."""
    bundle = _to_player_bundle(sessions)
    template_text = (_PLAYER_DIR / "template.html").read_text(encoding="utf-8")
    css = (_PLAYER_DIR / "player.css").read_text(encoding="utf-8")
    js = (_PLAYER_DIR / "player.js").read_text(encoding="utf-8")
    highlight = (_PLAYER_DIR / "highlight.min.js").read_text(encoding="utf-8")

    env = jinja2.Environment(autoescape=False, keep_trailing_newline=True)
    template = env.from_string(template_text)
    return template.render(
        document_name=_primary_document(bundle),
        css=css,
        js=js,
        highlight=highlight,
        bundle=_embed_safe_json(bundle),
    )


def write_player_html(sessions: list[Session] | Session, recording_file: Path) -> Path:
    """Write the player HTML next to the recording file and return its path."""
    html = build_player_html(sessions)
    stem = recording_file.with_suffix("") if recording_file.suffix == ".gz" else recording_file
    html_path = stem.with_suffix(stem.suffix + ".html") if stem.suffix else stem.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")
    return html_path
