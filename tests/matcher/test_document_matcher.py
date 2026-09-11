from datetime import datetime, timedelta

from recan.algorithm import DocumentMatcher


BASE = datetime(2026, 5, 17, 12, 0, 0)


def _ts(ms: int) -> datetime:
    return BASE + timedelta(milliseconds=ms)


# A timestamp safely after every edit any of these tests applies.
LATER = _ts(10_000_000)


def test_document_property_tracks_current_state():
    m = DocumentMatcher()
    assert m.document == ""
    m.apply_edit(0, "", "hello", ts=_ts(100))
    assert m.document == "hello"
    m.apply_edit(5, "", " world", ts=_ts(200))
    assert m.document == "hello world"
    m.apply_edit(0, "hello", "goodbye", ts=_ts(300))
    assert m.document == "goodbye world"


def test_apply_edit_handles_replacement():
    m = DocumentMatcher()
    m.apply_edit(0, "", "abc def", ts=_ts(100))
    m.apply_edit(4, "def", "xyz", ts=_ts(200))
    assert m.document == "abc xyz"


def test_contains_finds_fragment_in_current_doc():
    m = DocumentMatcher()
    m.apply_edit(0, "", "def main():\n    pass", ts=_ts(100))
    m.finalize()
    assert m.contains("def main", LATER) is True


def test_contains_finds_deleted_fragment():
    m = DocumentMatcher()
    m.apply_edit(0, "", "def helper(): pass\n", ts=_ts(100))
    m.apply_edit(0, "def helper(): pass\n", "", ts=_ts(200))
    assert m.document == ""
    m.finalize()
    assert m.contains("def helper", LATER) is True


def test_contains_returns_false_for_never_typed_fragment():
    m = DocumentMatcher()
    m.apply_edit(0, "", "print('hi')", ts=_ts(100))
    m.finalize()
    assert m.contains("import os", LATER) is False


def test_contains_finds_intermediate_state_clean():
    """User typed 'def man', then inserted 'i' at offset 6 to fix to 'def main'.
    The transient 'def man' state must be findable via substring search."""
    m = DocumentMatcher()
    for i, ch in enumerate("def man"):
        m.apply_edit(i, "", ch, ts=_ts(100 + i * 10))
    assert m.document == "def man"
    m.apply_edit(6, "", "i", ts=_ts(1000))
    assert m.document == "def main"
    m.finalize()
    assert m.contains("def man", LATER) is True


def test_sentinel_prevents_matches_spanning_snapshots():
    """Without the NUL sentinel, "abc\\x00xyz" could match "cx".
    With the sentinel, no match must span the join."""
    m = DocumentMatcher()
    m.apply_edit(0, "", "abc", ts=_ts(100))
    m.apply_edit(0, "abc", "xyz", ts=_ts(200))
    m.finalize()
    assert m.contains("cx", LATER) is False


def test_generated_fragment_does_not_match_its_own_snapshot():
    """A generated edit must not match its own freshly-appended snapshot.

    `_before_pos_at` uses bisect_left, so a queued check at ts=T sees a
    cutoff equal to the end of the snapshot strictly BEFORE ts=T. The
    snapshot created by the edit being queried — which is at ts=T exactly —
    is excluded, so a fragment that only lives there cannot self-match.
    """
    m = DocumentMatcher()
    m.apply_edit(0, "", "totally_new_fragment_xyz", ts=_ts(100))
    m.finalize()
    assert m.contains("totally_new_fragment_xyz", _ts(100)) is False


def test_generated_fragment_matches_prior_history():
    """A generated edit that matches an earlier snapshot is flagged True."""
    m = DocumentMatcher()
    m.apply_edit(0, "", "def helper():\n    return 1\n", ts=_ts(100))
    m.apply_edit(0, "def helper():\n    return 1\n", "", ts=_ts(200))
    m.apply_edit(0, "", "def helper():\n    return 1\n", ts=_ts(300))
    m.finalize()
    assert m.contains("def helper():\n    return 1\n", _ts(300)) is True


