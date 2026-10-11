from copy import deepcopy

from recan.algorithm import build_matcher
from recan.session import analyze_events
from recan.utils import parse_ts


def edit(second, old, new, offset=0):
    return {"type": "edit", "document": "main.py",
            "timestamp": f"2026-01-01T00:00:{second:02d}Z",
            "offset": offset, "oldFragment": old, "newFragment": new}


def issues(session):
    return [a for a in session["annotations"] if a["kind"] == "recording_issue"]


def test_stale_replacement_is_not_applied_twice_or_counted_as_a_paste():
    events = [edit(0, "", ""), edit(1, "", "print(42)\n"),
              edit(2, "\n", "print(42)\n"), edit(3, "", "x", 10)]
    original = deepcopy(events)
    matcher = build_matcher(events)
    session = analyze_events(events, [matcher])
    assert session["snapshots"][-1]["document_text"] == "print(42)\nx"
    assert matcher.document == "print(42)\nx"
    assert session["total_edits"] == 2
    assert len([a for a in session["annotations"] if "paste" in a["kind"]]) == 1
    assert issues(session)[0]["action"] == "already_applied"
    assert issues(session)[0]["event_index"] == 2
    assert events == original


def test_unrecoverable_section_is_skipped_until_snapshot_and_matcher_agrees():
    events = [edit(0, "known", "known"), edit(1, "missing", "unreliable paste!"),
              edit(2, "", "also unreliable!"), edit(3, "restored", "restored"),
              edit(4, "", "!", 8)]
    matcher = build_matcher(events)
    session = analyze_events(events, [matcher])
    assert session["snapshots"][-1]["document_text"] == "restored!"
    assert matcher.document == "restored!"
    assert not matcher.contains("unreliable", parse_ts("2026-01-01T00:00:05Z"))
    assert not any("paste" in a["kind"] for a in session["annotations"])
    assert [a["action"] for a in issues(session)] == ["interrupted", "resynchronized"]
    assert issues(session)[0]["skipped_edits"] == 2
    assert session["total_edits"] == 1
    assert any(e["type"] == "documentSnapshot" for e in session["entries"])


def test_invalid_insertion_offset_does_not_silently_append():
    session = analyze_events([edit(0, "abc", "abc"), edit(1, "", "paste!", 99)])
    assert session["snapshots"][-1]["document_text"] == "abc"
    assert issues(session)[0]["action"] == "interrupted"
    assert session["total_edits"] == 0


def test_repeated_valid_insertion_is_not_mistaken_for_stale_replacement():
    session = analyze_events([edit(0, "", "paste!"), edit(1, "", "paste!")])
    assert session["snapshots"][-1]["document_text"] == "paste!paste!"
    assert not issues(session)
