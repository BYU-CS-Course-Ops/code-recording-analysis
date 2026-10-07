"""End-to-end tests: drive analyze_events with synthetic event streams and
assert that the resulting annotation kinds reflect internal-paste logic."""

from datetime import datetime, timedelta

from recan.session import analyze_events
from recan.algorithm import build_matcher


def _ts(base: datetime, ms: int) -> str:
    return (base + timedelta(milliseconds=ms)).isoformat() + "Z"


def _typing_edits(text: str, base: datetime, start_ms: int, document: str = "main.py") -> list[dict]:
    """Generate one event per character, 50ms apart - not a generated paste."""
    events = []
    for i, ch in enumerate(text):
        events.append({
            "type": "edit",
            "document": document,
            "timestamp": _ts(base, start_ms + i * 50),
            "offset": i,
            "oldFragment": "",
            "newFragment": ch,
        })
    return events


def _paste_edit(text: str, offset: int, ts_str: str, document: str = "main.py") -> dict:
    return {
        "type": "edit",
        "document": document,
        "timestamp": ts_str,
        "offset": offset,
        "oldFragment": "",
        "newFragment": text,
    }


def test_internal_paste_after_explicit_deletion():
    """Type a block, delete it, then paste it back -> 'internal paste'."""
    base = datetime(2026, 5, 14, 12, 0, 0)
    block = "def helper(arg):\n    return arg * 2\n"

    events = []
    events.extend(_typing_edits(block, base, start_ms=0))
    events.append({
        "type": "edit",
        "document": "main.py",
        "timestamp": _ts(base, 10_000),
        "offset": 0,
        "oldFragment": block,
        "newFragment": "",
    })
    events.append(_paste_edit(block, 0, _ts(base, 15_000)))

    session = analyze_events(events, [build_matcher(events)])
    paste_annotations = [a for a in session["annotations"] if a["kind"] in {"approved_paste", "internal_paste", "unapproved_paste"}]
    assert len(paste_annotations) == 1
    assert paste_annotations[0]["kind"] == "internal_paste"
    assert sum(a["kind"] == "internal_paste" for a in session["annotations"]) == 1
    assert sum(a["kind"] == "unapproved_paste" for a in session["annotations"]) == 0


def test_internal_paste_matches_transient_state_not_in_final_doc():
    """Type a line, mutate it in-place so the original string is no longer
    a substring of the document, then generate-paste the original.
    The matcher must find it via the transient-state snapshot in history."""
    base = datetime(2026, 5, 14, 12, 0, 0)

    line = "def calculate_average_value():"
    events = list(_typing_edits(line, base, start_ms=0))
    # Doc state now == line.

    # Insert ", x" at offset 22 -> "def calculate_average_, xvalue():"
    # After this, `line` is NOT a substring of the current document.
    events.append({
        "type": "edit", "document": "main.py",
        "timestamp": _ts(base, 5_000),
        "offset": 22, "oldFragment": "", "newFragment": ", x",
    })

    # Now generate-paste `line` at offset 0. It matches the transient pre-mutation state.
    events.append(_paste_edit(line, 0, _ts(base, 10_000)))

    session = analyze_events(events, [build_matcher(events)])
    paste_annotations = [a for a in session["annotations"] if a["kind"] in {"approved_paste", "internal_paste", "unapproved_paste"}]
    assert [a["kind"] for a in paste_annotations] == ["unapproved_paste", "internal_paste"]
    assert sum(a["kind"] == "internal_paste" for a in session["annotations"]) == 1


def test_approved_paste_beats_internal():
    """A fragment that's BOTH internal AND approved is labeled approved."""
    base = datetime(2026, 5, 14, 12, 0, 0)
    block = "print('starter template')\n"

    events = []
    events.extend(_typing_edits(block, base, start_ms=0))
    events.append({
        "type": "edit", "document": "main.py",
        "timestamp": _ts(base, 10_000),
        "offset": 0, "oldFragment": block, "newFragment": "",
    })
    events.append(_paste_edit(block, 0, _ts(base, 15_000)))

    session = analyze_events(events, [build_matcher(events)], approved_pastes=block)
    paste_annotations = [a for a in session["annotations"] if a["kind"] in {"approved_paste", "internal_paste", "unapproved_paste"}]
    assert len(paste_annotations) == 1
    assert paste_annotations[0]["kind"] == "approved_paste"
    assert sum(a["kind"] == "internal_paste" for a in session["annotations"]) == 0
    assert sum(a["kind"] == "approved_paste" for a in session["annotations"]) == 1


def test_external_fragment_still_unapproved():
    """A pasted fragment that has NEVER been in the document is unapproved."""
    base = datetime(2026, 5, 14, 12, 0, 0)

    events = []
    events.extend(_typing_edits("print('hello')\n", base, start_ms=0))
    events.append(_paste_edit(
        "import requests\nr = requests.get('https://example.com')\nprint(r.text)\n",
        len("print('hello')\n"),
        _ts(base, 10_000),
    ))

    session = analyze_events(events, [build_matcher(events)])
    paste_annotations = [a for a in session["annotations"] if a["kind"] in {"approved_paste", "internal_paste", "unapproved_paste"}]
    assert len(paste_annotations) == 1
    assert paste_annotations[0]["kind"] == "unapproved_paste"
    assert sum(a["kind"] == "unapproved_paste" for a in session["annotations"]) == 1
    assert sum(a["kind"] == "internal_paste" for a in session["annotations"]) == 0
