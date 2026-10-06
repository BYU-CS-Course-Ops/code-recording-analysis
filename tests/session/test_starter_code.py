import json

import pytest

from recan.session import analyze_events, load_sessions


def snapshot(document, timestamp="2026-01-01T00:00:00Z"):
    return {
        "type": "edit", "document": r"C:\\work\\main.py", "timestamp": timestamp,
        "offset": 0, "oldFragment": document, "newFragment": document,
    }


def test_starter_match_uses_windows_basename_and_normalized_prefix():
    session = analyze_events(
        [snapshot("# template\nanswer = 1\n")],
        starter_code={"main.py": "# template\r\n"},
    )
    assert session["starts_with_starter_code"] is True


def test_starter_mismatch_and_no_match_are_tri_state():
    assert analyze_events([snapshot("different\n")], starter_code={"main.py": "# template\n"})[
        "starts_with_starter_code"
    ] is False
    assert analyze_events([snapshot("anything\n")], starter_code={"other.py": "anything\n"})[
        "starts_with_starter_code"
    ] is None


def test_matching_starter_without_initial_snapshot_is_false():
    event = snapshot("", timestamp="2026-01-01T00:00:00Z")
    event["oldFragment"] = ""
    event["newFragment"] = "typed"
    assert analyze_events([event], starter_code={"main.py": "typed"})[
        "starts_with_starter_code"
    ] is False


def test_approved_matches_do_not_cross_starter_sources():
    event = snapshot("", timestamp="2026-01-01T00:00:00Z")
    event["oldFragment"] = ""
    event["newFragment"] = "partA-partB"
    session = analyze_events(
        [event],
        approved_pastes=["partA-", "partB"],
    )
    assert session["total_approved_pastes"] == 0


def test_load_sessions_approves_fragments_from_starter_files(tmp_path):
    starter = tmp_path / "main.py"
    starter.write_text("answer = 42\n", encoding="utf-8")
    recording = tmp_path / "recording.jsonl"
    event = snapshot("", timestamp="2026-01-01T00:00:00Z")
    event["newFragment"] = "answer = 42\n"
    recording.write_text(json.dumps(event) + "\n", encoding="utf-8")

    sessions = load_sessions(
        [recording], None, None, [], starter_code_paths=[starter]
    )

    assert sessions[0]["total_approved_pastes"] == 1


def test_duplicate_and_unreadable_starters_fail_clearly(tmp_path):
    first = tmp_path / "one" / "main.py"
    second = tmp_path / "two" / "main.py"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("x", encoding="utf-8")
    second.write_text("y", encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate starter-code basename"):
        load_sessions([], None, None, [], starter_code_paths=[first, second])
    with pytest.raises(ValueError, match="Could not read starter-code file"):
        load_sessions([], None, None, [], starter_code_paths=[tmp_path / "missing.py"])
