import json
from pathlib import Path

import jinja2

from recan.structure import Session
from recan.session import annotation_counts, max_review_severity
from recan.utils import to_utc_iso8601

_PLAYER_DIR = Path(__file__).resolve().parent / "player"


def _json_annotation(annotation):
    result = dict(annotation)
    for key in ("timestamp", "end_timestamp"):
        if key in result:
            result[key] = to_utc_iso8601(result[key])
    return result


def _to_session_bundle(session: Session) -> dict:
    """Project one Session into the player's session data."""
    return {
        "document": session["document"],
        "language": session["language"],
        "start_time": to_utc_iso8601(session["start_time"]),
        "end_time": to_utc_iso8601(session["end_time"]),
        "total_time": session["total_time"],
        "total_edits": session["total_edits"],
        "entries": session["entries"],
        "annotations": [_json_annotation(a) for a in session["annotations"]],
        "snapshots": session["snapshots"],
    }


def _to_player_bundle(sessions) -> dict:
    source = sessions if isinstance(sessions, list) else [sessions]
    # Python's sort is stable, so equal start times retain caller order.
    source = sorted(source, key=lambda session: (
        session["start_time"] is None,
        to_utc_iso8601(session["start_time"]),
    ))
    counts = annotation_counts(source[0]) if source else annotation_counts({"annotations": []})
    for session in source[1:]:
        for kind, count in annotation_counts(session).items():
            counts[kind] += count
    return {
        "sessions": [_to_session_bundle(s) for s in source],
        "annotation_counts": counts,
        "review_severity": max_review_severity(
            [a for s in source for a in s["annotations"]]),
    }


def _embed_safe_json(bundle: dict) -> str:
    text = json.dumps(bundle, ensure_ascii=False, default=str)
    return (text.replace("<", "\\u003c").replace(">", "\\u003e")
                .replace("&", "\\u0026").replace("</", "<\\/")
                .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def build_player_html(sessions) -> str:
    bundle = _to_player_bundle(sessions)
    env = jinja2.Environment(autoescape=False, keep_trailing_newline=True)
    template = env.from_string((_PLAYER_DIR / "template.html").read_text(encoding="utf-8"))
    return template.render(
        document_name=bundle["sessions"][0].get("document", "recording") if bundle["sessions"] else "recording",
        css=(_PLAYER_DIR / "player.css").read_text(encoding="utf-8"),
        js=(_PLAYER_DIR / "player.js").read_text(encoding="utf-8"),
        bundle=_embed_safe_json(bundle),
    )


def write_player_html(sessions, recording_file: Path) -> Path:
    html = build_player_html(sessions)
    stem = recording_file.with_suffix("") if recording_file.suffix == ".gz" else recording_file
    html_path = stem.with_suffix(stem.suffix + ".html") if stem.suffix else stem.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")
    return html_path
