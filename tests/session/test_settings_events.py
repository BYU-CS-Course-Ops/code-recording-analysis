from copy import deepcopy
from pathlib import Path

import pytest

from recan.algorithm import build_matcher
from recan.formaters import render_json, render_markdown
from recan.replay import validated_events
from recan.session import analyze_events
from recan.stats import _row_from_session
from recan.utils import normalize_event_timestamps
from recan.viewer import _to_session_bundle


def edit(second, old, new, offset=0):
    return {
        "type": "edit", "document": "/project/example.py",
        "timestamp": f"2026-11-04T12:00:{second:02d}Z",
        "offset": offset, "oldFragment": old, "newFragment": new,
    }


def status(kind, second, **fields):
    return {"type": kind, "timestamp": f"2026-11-04T12:00:{second:02d}Z", **fields}


@pytest.mark.parametrize("initial", ["", "answer = 41\n"])
@pytest.mark.parametrize("prefix", [
    [status("recorderSettingChanged", 0, setting="inlineCompletion.enabled", value=False)],
    [status("userSettingChanged", 0, setting="inlineCompletion.enabled", value=True),
     status("recorderSettingChanged", 0, setting="inlineCompletion.enabled", value=False)],
    [status("fileSwitch", 0, left="", arrived="/project/example.py")],
])
def test_metadata_before_snapshot_preserves_analysis_matcher_and_reports(initial, prefix):
    baseline = [edit(1, initial, initial), edit(10, "", "x", len(initial))]
    events = normalize_event_timestamps(prefix + baseline + [
        status("userSettingChanged", 11, setting="inlineCompletion.enabled", value=True),
        status("recorderSettingChanged", 11, setting="inlineCompletion.enabled", value=False),
        status("recorderSettingChanged", 20, setting="inlineCompletion.enabled", value=True),
    ])
    original = deepcopy(events)
    matcher = build_matcher(events)
    session = analyze_events(events, [matcher], starter_code={"example.py": initial})
    expected = analyze_events(baseline, starter_code={"example.py": initial})

    assert session == expected
    assert session["starts_with_starter_code"] is True
    assert session["total_edits"] == 1
    assert [entry["type"] for entry in session["entries"]] == ["initialSnapshot", "edit"]
    assert matcher.document == initial + "x"
    assert session["snapshots"][-1]["document_text"] == matcher.document
    row = _row_from_session(Path("/assignment/submission"), session, 1, "student@example.com")
    assert row["analysis_incomplete"] is False
    assert row["num_recording_issues"] == 0
    assert _to_session_bundle(session) == _to_session_bundle(expected)
    assert render_json([session]) == render_json([expected])
    assert render_markdown([session]) == render_markdown([expected])
    assert events == original


def test_focus_before_initial_snapshot_is_preserved_without_false_recovery():
    events = [
        status("focusStatus", 0, focused=False),
        edit(1, "starter", "starter"),
        status("focusStatus", 2, focused=True),
        edit(3, "", "x", 7),
    ]
    session = analyze_events(events, starter_code={"example.py": "starter"})
    assert [entry["type"] for entry in session["entries"]] == [
        "focusStatus", "initialSnapshot", "focusStatus", "edit",
    ]
    assert not any(a["kind"] == "recording_issue" for a in session["annotations"])
    assert session["starts_with_starter_code"] is True
    assert session["total_time_unfocused"] == 2
    assert session["snapshots"][-1]["document_text"] == "starterx"


def test_settings_prefix_does_not_hide_genuine_interruption_or_change_source_indices():
    events = [
        status("recorderSettingChanged", 0, setting="inlineCompletion.enabled", value=False),
        edit(1, "missing", "bad edit"),
        edit(2, "restored", "restored"),
        edit(3, "", "x", 8),
    ]
    session = analyze_events(events)
    issues = [a for a in session["annotations"] if a["kind"] == "recording_issue"]
    assert [issue["action"] for issue in issues] == ["interrupted", "resynchronized"]
    assert [issue["event_index"] for issue in issues] == [1, 2]
    assert session["snapshots"][-1]["document_text"] == "restoredx"
    row = _row_from_session(Path("/assignment/submission"), session, 1, "student@example.com")
    assert row["analysis_incomplete"] is True


def test_snapshot_after_real_edit_is_recovery_not_initial_content():
    events = [
        status("recorderSettingChanged", 0, setting="inlineCompletion.enabled", value=False),
        edit(1, "", "x"),
        edit(2, "restored", "restored"),
    ]
    session = analyze_events(events)
    assert session["entries"][0]["type"] == "edit"
    assert any(a.get("action") == "resynchronized" for a in session["annotations"])
    assert session["snapshots"][-1]["document_text"] == "restored"


def test_settings_only_recording_does_not_invent_a_snapshot_or_edits():
    events = [status("recorderSettingChanged", 0, setting="inlineCompletion.enabled", value=False)]
    assert validated_events(events) == events
    session = analyze_events(events)
    assert session["entries"] == []
    assert session["annotations"] == []
    assert session["total_edits"] == 0
