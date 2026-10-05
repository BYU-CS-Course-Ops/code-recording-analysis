"""Unit tests for the timestamp-keyed gate on DocumentMatcher.

These exercise `_before_pos_at` directly and `contains` through its
`ts` parameter. Cross-matcher behavior lives in test_cross_matcher.py.
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
    m.apply_edit(0, "", "hello", ts=_ts(1000))
    assert m._before_pos_at(_ts(500)) == 0


def test_before_pos_at_ts_equals_snapshot_returns_prior_snapshots_end():
    """bisect_left's contract: ts equal to a snapshot timestamp returns the
    PRIOR snapshot's end offset, not that snapshot's own end. This is the
    self-capture guard — a generated edit's queued check runs at the same ts
    as its own snapshot, and we want that snapshot excluded from the search.
    """
    m = DocumentMatcher()
    m.apply_edit(0, "", "aaa", ts=_ts(100))
    end_at_100 = len(m._history_bytes)
    m.apply_edit(3, "", "bbb", ts=_ts(200))
    # ts == 100 excludes the snapshot at 100; only snapshots strictly before
    # 100 count, and there are none.
    assert m._before_pos_at(_ts(100)) == 0
    # ts == 200 excludes the snapshot at 200; the snapshot at 100 still counts.
    assert m._before_pos_at(_ts(200)) == end_at_100


def test_before_pos_at_ts_strictly_after_last_returns_last_end():
    m = DocumentMatcher()
    m.apply_edit(0, "", "aaa", ts=_ts(100))
    m.apply_edit(3, "", "bbb", ts=_ts(200))
    last_end = len(m._history_bytes)
    assert m._before_pos_at(_ts(9999)) == last_end


def test_before_pos_at_duplicate_timestamps_excludes_all_at_that_ts():
    """Two edits in the same millisecond — with bisect_left, neither counts
    as "before" that millisecond. Without a prior snapshot, the cutoff is 0.
    """
    m = DocumentMatcher()
    m.apply_edit(0, "", "aaa", ts=_ts(100))
    m.apply_edit(3, "", "bbb", ts=_ts(100))
    assert m._before_pos_at(_ts(100)) == 0


# ---------- _contains via ts ----------

def test_contains_finds_past_fragment():
    m = DocumentMatcher()
    m.apply_edit(0, "", "def main():\n    pass", ts=_ts(100))
    m.finalize()
    assert m.contains("def main", _ts(200)) is True


def test_contains_rejects_future_fragment():
    """Fragment present only in a snapshot whose ts > query ts is not found."""
    m = DocumentMatcher()
    m.apply_edit(0, "", "before", ts=_ts(100))
    m.apply_edit(6, "", "_after_fragment", ts=_ts(500))
    m.finalize()
    assert m.contains("_after_fragment", _ts(200)) is False


def test_contains_empty_fragment_returns_false():
    m = DocumentMatcher()
    m.apply_edit(0, "", "hello", ts=_ts(100))
    m.finalize()
    assert m.contains("", _ts(200)) is False


def test_contains_sentinel_prevents_match_spanning_snapshots():
    """Sentinel guarantee: a fragment that would only exist by crossing the
    boundary between snapshots must not match."""
    m = DocumentMatcher()
    m.apply_edit(0, "", "abc", ts=_ts(100))
    m.apply_edit(0, "abc", "xyz", ts=_ts(200))
    m.finalize()
    assert m.contains("cx", _ts(300)) is False


def test_contains_before_finalize_raises():
    m = DocumentMatcher()
    m.apply_edit(0, "", "hello", ts=_ts(100))
    with pytest.raises(RuntimeError):
        m.contains("hello", _ts(200))
