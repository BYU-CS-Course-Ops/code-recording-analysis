"""End-to-end tests: drive analyze_inputs with synthetic event streams and
assert that the resulting Burst.kind values reflect internal-paste logic."""

from datetime import datetime, timedelta

from recan.session import analyze_inputs


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

    session = analyze_inputs(events, approved_fragments=[])
    paste_bursts = [b for b in session["bursts"] if b["kind"] != "ide_action"]
    assert len(paste_bursts) == 1
    assert paste_bursts[0]["kind"] == "internal paste"
    assert session["total_internal_pastes"] == 1
    assert session["total_unapproved_pastes"] == 0
