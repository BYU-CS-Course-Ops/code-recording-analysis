import csv

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
