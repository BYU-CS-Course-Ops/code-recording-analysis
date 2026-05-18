"""Unit tests for the timestamp-keyed gate on DocumentMatcher.

These exercise `_before_pos_at` directly and `_contains` through its new
`ts` parameter. Cross-matcher behavior lives in test_cross_matcher_resolve.py.
"""

from datetime import datetime, timedelta

import pytest

from recan.algorithm import DocumentMatcher


BASE = datetime(2026, 5, 17, 12, 0, 0)


def _ts(ms: int) -> datetime:
    return BASE + timedelta(milliseconds=ms)


# ---------- _before_pos_at ----------

def test_before_pos_at_empty_history_returns_zero():
    m = DocumentMatcher()
    assert m._before_pos_at(_ts(0)) == 0


def test_before_pos_at_ts_strictly_before_first_snapshot_returns_zero():
    m = DocumentMatcher()
    m.apply_edit(0, "", "hello", is_generated=False, ts=_ts(1000))
    assert m._before_pos_at(_ts(500)) == 0


def test_before_pos_at_ts_equals_snapshot_returns_that_snapshots_end():
    """bisect_right's contract: ts equal to a snapshot timestamp returns
    that snapshot's end offset, not the prior one."""
    m = DocumentMatcher()
    m.apply_edit(0, "", "aaa", is_generated=False, ts=_ts(100))
    end_at_100 = len(m._history_bytes)
    m.apply_edit(3, "", "bbb", is_generated=False, ts=_ts(200))
    assert m._before_pos_at(_ts(100)) == end_at_100


def test_before_pos_at_ts_strictly_after_last_returns_last_end():
    m = DocumentMatcher()
    m.apply_edit(0, "", "aaa", is_generated=False, ts=_ts(100))
    m.apply_edit(3, "", "bbb", is_generated=False, ts=_ts(200))
    last_end = len(m._history_bytes)
    assert m._before_pos_at(_ts(9999)) == last_end


def test_before_pos_at_duplicate_timestamps_returns_latest_end():
    """Two edits in the same millisecond — return the *latest* snapshot
    end offset for that timestamp."""
    m = DocumentMatcher()
    m.apply_edit(0, "", "aaa", is_generated=False, ts=_ts(100))
    m.apply_edit(3, "", "bbb", is_generated=False, ts=_ts(100))
    latest_end = len(m._history_bytes)
    assert m._before_pos_at(_ts(100)) == latest_end


# ---------- _contains via ts ----------

def test_contains_finds_past_fragment():
    m = DocumentMatcher()
    m.apply_edit(0, "", "def main():\n    pass", is_generated=False, ts=_ts(100))
    m.finalize()
    assert m._contains("def main", _ts(200)) is True


def test_contains_rejects_future_fragment():
    """Fragment present only in a snapshot whose ts > query ts is not found."""
    m = DocumentMatcher()
    m.apply_edit(0, "", "before", is_generated=False, ts=_ts(100))
    m.apply_edit(6, "", "_after_fragment", is_generated=False, ts=_ts(500))
    m.finalize()
    assert m._contains("_after_fragment", _ts(200)) is False


def test_contains_empty_fragment_returns_false():
    m = DocumentMatcher()
    m.apply_edit(0, "", "hello", is_generated=False, ts=_ts(100))
    m.finalize()
    assert m._contains("", _ts(200)) is False


def test_contains_sentinel_prevents_match_spanning_snapshots():
    """Sentinel guarantee: a fragment that would only exist by crossing the
    boundary between snapshots must not match."""
    m = DocumentMatcher()
    m.apply_edit(0, "", "abc", is_generated=False, ts=_ts(100))
    m.apply_edit(0, "abc", "xyz", is_generated=False, ts=_ts(200))
    m.finalize()
    assert m._contains("cx", _ts(300)) is False


def test_contains_before_finalize_raises():
    m = DocumentMatcher()
    m.apply_edit(0, "", "hello", is_generated=False, ts=_ts(100))
    with pytest.raises(RuntimeError):
        m._contains("hello", _ts(200))
