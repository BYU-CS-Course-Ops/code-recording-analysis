import gzip
import json

from recan.session import analyze_events
from recan.utils import _load_recording
from recan.viewer import _to_session_bundle
from recan.formaters import render_markdown


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

    assert session["total_edits"] == 1
    assert session["total_chars"] == len("answer = 42\n")
    assert len(session["entries"]) == 2
    assert [entry["type"] for entry in session["entries"]] == ["initialSnapshot", "edit"]
    assert session["entries"][0] == {
        "timestamp": "2026-01-01T00:00:00.000000Z",
        "type": "initialSnapshot",
        "document_text": initial,
    }
    assert not any(a["kind"] == "idle_gap" for a in session["annotations"])
    assert session["snapshots"][-1]["document_text"] == "answer = 42\n"

    bundle = _to_session_bundle(session)

    summary = render_markdown([session])
    assert "## Initial content" in summary
    assert initial in summary


def test_later_unchanged_snapshot_is_ignored_entirely():
    initial = "answer = 41\n"
    final = "answer = 42\n"
    events = [
        _event("2026-01-01T00:00:00Z", 0, initial, initial),
        _event("2026-01-01T00:00:01Z", 9, "41", "42"),
        _event("2026-01-01T00:00:20Z", 0, final, final),
    ]

    session = analyze_events(events)

    assert session["total_edits"] == 1
    assert session["total_typed_chars"] == 2
    assert session["total_pasted_chars"] == 0
    assert session["total_deleted_chars"] == 2
    assert session["total_time"] == 1
    assert len(session["entries"]) == 2
    assert [entry["type"] for entry in session["entries"]] == ["initialSnapshot", "edit"]
    assert session["annotations"] == []
    assert session["snapshots"] == [{"after_idx": 1, "document_text": final}]


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
