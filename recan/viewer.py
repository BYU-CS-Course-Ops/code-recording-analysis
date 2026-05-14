import json
from pathlib import Path

import jinja2

from recan.structure import Session


_PLAYER_DIR = Path(__file__).resolve().parent / "player"


def _to_player_bundle(session: Session) -> dict:
    """Project a Session into the JSON bundle the existing player consumes.

    Field names here match what player.js reads — keep this stable.
    """
    start = session["start_time"]
    end = session["end_time"]
    return {
        "metadata": {
            "document": session["document"],
            "language": session["language"],
            "start_time": start.isoformat().replace("+00:00", "Z") if start else "",
            "end_time": end.isoformat().replace("+00:00", "Z") if end else "",
            "duration_seconds": session["total_time"],
        },
        "events": session["events"],
        "bursts": [
            {
                "kind": b["kind"],
                "start_idx": b["start_idx"],
                "end_idx": b["end_idx"],
                "lines": b["line_count"],
                "chars": b["char_count"],
            }
            for b in session["bursts"]
        ],
        "idle_gaps": [
            {"after_idx": g["after_idx"], "duration_seconds": g["duration"]}
            for g in session["idle_gaps"]
        ],
        "focus_intervals": [
            {"blur_idx": fi["blur_idx"], "focus_idx": fi["focus_idx"], "duration_seconds": fi["duration"]}
            for fi in session["focus_intervals"]
        ],
        "snapshots": session["snapshots"],
        "summary": {
            "total_seconds": session["total_time"],
            "unfocused_seconds": session["total_time_unfocused"],
            "burst_count": len(session["bursts"]),
            "edit_count": session["total_edits"],
            "generated_count": len(session["bursts"]),
            "ide_action_count": session["total_ide_actions"],
            "approved_paste_count": session["total_approved_pastes"],
            "unapproved_paste_count": session["total_unapproved_pastes"],
            "internal_paste_count": session["total_internal_pastes"],
        },
    }


def _embed_safe_json(bundle: dict) -> str:
    """JSON-encode a bundle for safe embedding in an HTML <script> tag.

    Two specific dangers when embedding JSON in HTML inside a <script> block:
    1. The substring ``</script`` ends the script element early. Escape ``</`` to ``<\\/``.
    2. U+2028 / U+2029 are valid in JSON but illegal in JS string literals.
    """
    text = json.dumps(bundle, ensure_ascii=False, default=str)
    text = text.replace("</", "<\\/")
    text = text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return text


def build_player_html(session: Session) -> str:
    """Render the static HTML player by inlining the bundle and assets via Jinja2."""
    bundle = _to_player_bundle(session)
    template_text = (_PLAYER_DIR / "template.html").read_text(encoding="utf-8")
    css = (_PLAYER_DIR / "player.css").read_text(encoding="utf-8")
    js = (_PLAYER_DIR / "player.js").read_text(encoding="utf-8")
    highlight = (_PLAYER_DIR / "highlight.min.js").read_text(encoding="utf-8")

    env = jinja2.Environment(autoescape=False, keep_trailing_newline=True)
    template = env.from_string(template_text)
    return template.render(
        document_name=bundle["metadata"].get("document", "recording"),
        css=css,
        js=js,
        highlight=highlight,
        bundle=_embed_safe_json(bundle),
    )


def write_player_html(session: Session, recording_file: Path) -> Path:
    """Write the player HTML next to the recording file and return its path."""
    html = build_player_html(session)
    stem = recording_file.with_suffix("") if recording_file.suffix == ".gz" else recording_file
    html_path = stem.with_suffix(stem.suffix + ".html") if stem.suffix else stem.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")
    return html_path
