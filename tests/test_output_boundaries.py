import csv
import json

import pytest

from recan.formaters import render_json, render_markdown
from recan.session import analyze_events
from recan.stats import _row_from_session, write_csv
from recan.viewer import build_player_html


def _session(*, initial="typed\n", starter_code=None):
    event = {
        "type": "edit",
        "document": "main.py",
        "timestamp": "2026-01-01T00:00:00Z",
        "offset": 0,
        "oldFragment": initial,
        "newFragment": initial,
    }
    return analyze_events([event], starter_code=starter_code)


@pytest.mark.parametrize(
    ("starter_code", "expected"),
    [({"main.py": "typed\n"}, True), ({"main.py": "other\n"}, False), (None, None)],
)
def test_markdown_starter_mismatch_is_false_only_and_separated(starter_code, expected):
    markdown = render_markdown([_session(starter_code=starter_code)])

    assert markdown.count("Starter-code mismatch") == (1 if expected is False else 0)
    if expected is False:
        assert "| Starter-code mismatch | Yes |\n\n## Initial content" in markdown
    assert "\n\n## Summary" in markdown
    assert "\n\n## Initial content" in markdown


@pytest.mark.parametrize("value", [True, False, None])
def test_csv_preserves_starter_code_tri_state(tmp_path, value):
    session = _session()
    session["starts_with_starter_code"] = value
    row = _row_from_session(tmp_path / "submission", session, 1, "student@example.com")
    output = tmp_path / "stats.csv"
    write_csv(output, [row])

    with output.open(newline="", encoding="utf-8") as stream:
        result = next(csv.DictReader(stream))
    assert result["starts_with_starter_code"] == ("" if value is None else str(value))


@pytest.mark.parametrize("value", [True, False, None])
def test_session_json_preserves_starter_code_tri_state(value):
    session = _session()
    session["starts_with_starter_code"] = value
    assert session["starts_with_starter_code"] is value
    assert json.loads(render_json([session]))[0]["starts_with_starter_code"] is value


def test_render_json_rejects_unsupported_objects():
    session = _session()
    session["document"] = object()
    with pytest.raises(TypeError, match="object is not JSON serializable"):
        render_json([session])


def test_player_escapes_hostile_document_names_without_browser_runtime():
    session = _session()
    session["document"] = 'folder/<img src=x onerror="bad">.py'
    html = build_player_html([session])
    assert '<img src=x onerror="bad">' not in html
    assert "&lt;img" in html or "\\u003c" in html


def test_viewer_embeds_false_mismatch_without_creating_unknown_warning():
    session = _session(starter_code={"main.py": "other\n"})
    html = build_player_html([session])
    payload = json.loads(html.split("window.__BUNDLE__ = ", 1)[1].split(";</script>", 1)[0])
    assert "starts_with_starter_code" not in payload["sessions"][0]
    mismatch = [a for a in session["annotations"] if a["kind"] == "starter_code_mismatch"]
    assert len(mismatch) == 1
    assert mismatch[0]["review_severity"] == "HIGH"
    assert "starter_code_mismatch" in html
