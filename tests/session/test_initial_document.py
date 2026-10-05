import gzip
import json

from recan.session import analyze_events
from recan.utils import _load_recording
from recan.viewer import _to_session_bundle


def _event(timestamp: str, offset: int, old: str, new: str) -> dict:
    return {
        "type": "edit",
        "document": r"C:\work\example.py",
        "timestamp": timestamp,
        "offset": offset,
        "oldFragment": old,
        "newFragment": new,
    }


def test_initial_snapshot_seeds_reconstruction_without_counting_as_edit():
    initial = "answer = 41\n"
    events = [
        _event("2026-01-01T00:00:00Z", 0, initial, initial),
        _event("2026-01-01T00:00:10Z", 9, "41", "42"),
    ]

    session = analyze_events(events)

    assert session["initial_document"] == initial
    assert session["total_edits"] == 1
    assert session["total_chars"] == len("answer = 42\n")
    assert len(session["events"]) == 2
    assert session["events"][0] == {
        "timestamp": "2026-01-01T00:00:00Z",
        "type": "initialSnapshot",
        "document_text": initial,
    }
    assert session["idle_gaps"] == []
    assert session["snapshots"][-1]["document_text"] == "answer = 42\n"

    bundle = _to_session_bundle(session)
    assert bundle["initial_document"] == initial


def test_initial_character_limit_flags_only_when_exceeded():
    initial = "answer = 41\n"
    events = [_event("2026-01-01T00:00:00Z", 0, initial, initial)]

    assert not analyze_events(events, initial_char_limit=len(initial))["initial_char_limit_exceeded"]
    assert analyze_events(events, initial_char_limit=len(initial) - 1)["initial_char_limit_exceeded"]
    assert not analyze_events(events)["initial_char_limit_exceeded"]


def test_recording_loader_preserves_initial_snapshot(tmp_path):
    initial = "answer = 41\n"
    events = [
        _event("2026-01-01T00:00:00Z", 0, initial, initial),
        _event("2026-01-01T00:00:01Z", 9, "41", "42"),
    ]
    recording = tmp_path / "example.recording.jsonl.gz"
    with gzip.open(recording, "wt", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps(event) + "\n")

    assert _load_recording(recording, []) == events
