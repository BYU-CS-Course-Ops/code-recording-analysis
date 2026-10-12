import csv

import pytest

from recan.session import analyze_events
from recan.stats import _row_from_session, write_csv


def test_stats_omit_number_of_initial_characters(tmp_path):
    initial = "starter = 41\n"
    events = [
        {
            "type": "edit",
            "document": "example.py",
            "timestamp": "2026-01-01T00:00:00Z",
            "offset": 0,
            "oldFragment": initial,
            "newFragment": initial,
        },
    ]
    session = analyze_events(events)
    row = _row_from_session(tmp_path / "submission", session, 123, "student@example.com")

    output = tmp_path / "stats.csv"
    write_csv(output, [row])

    with output.open(newline="", encoding="utf-8") as stream:
        result = next(csv.DictReader(stream))

    assert "num_initial_chars" not in result


@pytest.mark.parametrize("changes, expected", [
    ([], (0, 0, False)),
    # Replacement is already present: no history is lost.
    ([("missing", "known", 0)], (1, 1, False)),
    # Unreliable edits cannot contribute to reported counts.
    ([("missing", "paste!", 0), ("", "another paste!", 0)], (1, 2, True)),
    # Restoring the document does not restore the skipped edit history.
    ([("missing", "paste!", 0), ("", "another paste!", 0),
      ("restored", "restored", 0), ("", "!", 8)], (2, 2, True)),
    # A changed snapshot can reveal missing history without skipped events.
    ([("restored", "restored", 0)], (1, 0, True)),
])
def test_csv_reports_recording_quality(tmp_path, changes, expected):
    events = []
    for second, (old, new, offset) in enumerate([("known", "known", 0), *changes]):
        events.append({
            "type": "edit", "document": "example.py",
            "timestamp": f"2026-01-01T00:00:{second:02d}Z",
            "offset": offset, "oldFragment": old, "newFragment": new,
        })
    session = analyze_events(events)
    row = _row_from_session(tmp_path / "submission", session, 123, "student@example.com")
    output = tmp_path / "stats.csv"
    write_csv(output, [row])

    with output.open(newline="", encoding="utf-8") as stream:
        result = next(csv.DictReader(stream))
    assert tuple(result[key] for key in (
        "num_recording_issues", "num_skipped_edits", "analysis_incomplete",
    )) == tuple(str(value) for value in expected)
    assert result["num_unapproved_pastes"] == "0"
    assert result["num_edits"] == ("1" if len(changes) == 4 else "0")
