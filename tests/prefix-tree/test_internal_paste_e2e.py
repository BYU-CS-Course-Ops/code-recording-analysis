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


def test_internal_paste_matches_transient_state_not_in_final_doc():
    """Type a line, mutate it in-place so the original string is no longer
    a substring of the document, then generate-paste the original.
    The matcher must find it via the transient-state snapshot in history."""
    base = datetime(2026, 5, 14, 12, 0, 0)

    line = "def calculate_average_value():"  # 30 chars - >= 20 so paste is "generated"
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

    session = analyze_inputs(events, approved_fragments=[])
    paste_bursts = [b for b in session["bursts"] if b["kind"] != "ide_action"]
    assert len(paste_bursts) == 1
    assert paste_bursts[0]["kind"] == "internal paste"
    assert session["total_internal_pastes"] == 1
