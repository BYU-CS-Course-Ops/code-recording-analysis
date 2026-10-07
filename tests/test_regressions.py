from argparse import ArgumentParser, Namespace
from datetime import datetime, timedelta
import gzip
import json

import pytest

from recan.algorithm import DocumentMatcher
from recan.main import _handle_view, _parse_view
from recan.session import analyze_events
from recan.utils import _get_recordings, _load_recording, parse_ts
from recan.viewer import build_player_html


def test_negative_timezone_survives_fractional_second_parsing():
    ts = parse_ts("2026-01-01T12:00:00.123456789-07:00")
    assert ts.microsecond == 123456
    assert ts.utcoffset() == timedelta(hours=-7)


def test_idle_time_is_excluded_and_filename_drives_language():
    events = [{"timestamp": f"2026-01-01T00:00:{second:02d}Z", "document": "simple.java",
               "offset": i, "oldFragment": "", "newFragment": "a"}
              for i, second in enumerate([0, 1, 20, 21])]
    session = analyze_events(events)
    assert session["total_time_typing"] == 2
    idle = [a for a in session["annotations"] if a["kind"] == "idle_gap"]
    assert len(idle) == 1
    assert (idle[0]["entry_start"], idle[0]["entry_end"]) == (1, 2)
    assert idle[0]["duration"] == 19
    assert session["language"] == "java"
    assert session["document"] == "simple"


def test_edit_invalidates_suffix_array_until_finalized_again():
    matcher = DocumentMatcher()
    ts = datetime(2026, 1, 1)
    matcher.apply_edit(0, "", "hello", ts)
    matcher.finalize()
    matcher.apply_edit(5, "", " world", ts + timedelta(seconds=1))
    with pytest.raises(RuntimeError):
        matcher.contains("world", ts + timedelta(seconds=2))
    matcher.finalize()
    assert matcher.contains("world", ts + timedelta(seconds=2))


def test_out_of_order_edit_is_rejected_without_mutating_document():
    matcher = DocumentMatcher()
    ts = datetime(2026, 1, 1)
    matcher.apply_edit(0, "", "hello", ts)
    with pytest.raises(ValueError, match="timestamp order"):
        matcher.apply_edit(0, "hello", "bad", ts - timedelta(seconds=1))
    assert matcher.document == "hello"


def test_recursive_globs_find_nested_recordings_and_deduplicate(tmp_path):
    nested = tmp_path / "one" / "two"
    nested.mkdir(parents=True)
    recording = nested / "test.jsonl"
    recording.write_text("", encoding="utf-8")
    assert _get_recordings([tmp_path / "**" / "*.jsonl", recording]) == [recording]


@pytest.mark.parametrize("compressed", [False, True])
def test_recordings_use_utf8(tmp_path, compressed):
    path = tmp_path / ("test.jsonl.gz" if compressed else "test.jsonl")
    opener = gzip.open if compressed else open
    event = {"newFragment": "é漢字🙂"}
    with opener(path, "wt", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    assert _load_recording(path, []) == [event]


def test_view_can_disable_browser_launch():
    parser = ArgumentParser()
    _parse_view(parser.add_subparsers())
    assert parser.parse_args(["view", "test.jsonl"]).auto_open is True
    assert parser.parse_args(["view", "test.jsonl", "--no-open"]).auto_open is False


def test_view_glob_uses_first_matched_recording_for_output(tmp_path):
    nested = tmp_path / "recordings"
    nested.mkdir()
    event = {
        "type": "edit",
        "document": "main.py",
        "timestamp": "2026-01-01T00:00:00Z",
        "offset": 0,
        "oldFragment": "",
        "newFragment": "x",
    }
    for name in ("b.jsonl", "a.jsonl"):
        (nested / name).write_text(json.dumps(event) + "\n", encoding="utf-8")

    _handle_view(Namespace(
        recording_files=[tmp_path / "**" / "*.jsonl"],
        problems=None,
        approved_pastes=None,
        exclude=[],
        auto_open=False,
    ))

    assert (nested / "a.jsonl.html").is_file()
    assert not (nested / "b.jsonl.html").exists()


def test_view_reports_when_glob_matches_no_recordings(tmp_path):
    args = Namespace(
        recording_files=[tmp_path / "**" / "*.jsonl"],
        problems=None,
        approved_pastes=None,
        exclude=[],
        auto_open=False,
    )
    with pytest.raises(FileNotFoundError, match="No recording files matched"):
        _handle_view(args)


def test_player_title_escapes_document_name():
    session = analyze_events([])
    session["document"] = "</title><script>alert(1)</script>"
    html = build_player_html(session)
    assert "<title>Recording Playback — &lt;/title&gt;" in html
    assert "</title><script>alert(1)</script>" not in html
