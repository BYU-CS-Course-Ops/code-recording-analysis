from datetime import datetime, timedelta

import pytest

from recan.algorithm import build_matcher
from recan.session import analyze_events


@pytest.mark.parametrize("source_second, expected", [(0, "internal_paste"), (1, "unapproved_paste"), (2, "unapproved_paste")])
def test_cross_recording_pastes_require_strictly_earlier_history(source_second, expected):
    def event(second, text):
        return {"timestamp": (datetime(2026, 1, 1) + timedelta(seconds=second)).isoformat(),
                "document": "main.py", "offset": 0, "oldFragment": "", "newFragment": text}

    fragment = "print('shared code')\n"
    events = [event(1, fragment)]
    matchers = [build_matcher(events), build_matcher([event(source_second, fragment)])]
    assert analyze_events(events, matchers)["annotations"][0]["kind"] == expected


def test_multiple_recordings_can_match_the_last_matcher():
    base = {"timestamp": "2026-01-01T00:00:00Z", "offset": 0, "oldFragment": ""}
    matchers = [build_matcher([{**base, "newFragment": text}]) for text in ["unrelated", "print(42)"]]
    events = [{**base, "timestamp": "2026-01-01T00:00:01Z", "newFragment": "print(42)"}]
    assert analyze_events(events, matchers)["annotations"][0]["kind"] == "internal_paste"
