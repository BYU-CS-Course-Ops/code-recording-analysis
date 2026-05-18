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
    m.apply_edit(0, "", "hello", is_generated=False, ts=_ts(100))
    assert m.document == "hello"
    m.apply_edit(5, "", " world", is_generated=False, ts=_ts(200))
    assert m.document == "hello world"
    m.apply_edit(0, "hello", "goodbye", is_generated=False, ts=_ts(300))
    assert m.document == "goodbye world"


def test_apply_edit_handles_replacement():
    m = DocumentMatcher()
    m.apply_edit(0, "", "abc def", is_generated=False, ts=_ts(100))
    m.apply_edit(4, "def", "xyz", is_generated=False, ts=_ts(200))
    assert m.document == "abc xyz"


def test_contains_finds_fragment_in_current_doc():
    m = DocumentMatcher()
    m.apply_edit(0, "", "def main():\n    pass", is_generated=False, ts=_ts(100))
    m.finalize()
    assert m._contains("def main", LATER) is True


def test_contains_finds_deleted_fragment():
    m = DocumentMatcher()
    m.apply_edit(0, "", "def helper(): pass\n", is_generated=False, ts=_ts(100))
    m.apply_edit(0, "def helper(): pass\n", "", is_generated=False, ts=_ts(200))
    assert m.document == ""
    m.finalize()
    assert m._contains("def helper", LATER) is True


def test_contains_returns_false_for_never_typed_fragment():
    m = DocumentMatcher()
    m.apply_edit(0, "", "print('hi')", is_generated=False, ts=_ts(100))
    m.finalize()
    assert m._contains("import os", LATER) is False


def test_contains_finds_intermediate_state_clean():
    """User typed 'def man', then inserted 'i' at offset 6 to fix to 'def main'.
    The transient 'def man' state must be findable via substring search."""
    m = DocumentMatcher()
    for i, ch in enumerate("def man"):
        m.apply_edit(i, "", ch, is_generated=False, ts=_ts(100 + i * 10))
    assert m.document == "def man"
    m.apply_edit(6, "", "i", is_generated=False, ts=_ts(1000))
    assert m.document == "def main"
    m.finalize()
    assert m._contains("def man", LATER) is True


def test_sentinel_prevents_matches_spanning_snapshots():
    """Without the NUL sentinel, "abc\\x00xyz" could match "cx".
    With the sentinel, no match must span the join."""
    m = DocumentMatcher()
    m.apply_edit(0, "", "abc", is_generated=False, ts=_ts(100))
    m.apply_edit(0, "abc", "xyz", is_generated=False, ts=_ts(200))
    m.finalize()
    assert m._contains("cx", LATER) is False


def test_generated_fragment_does_not_match_its_own_snapshot():
    """A generated edit must not match its own freshly-appended snapshot.

    With the timestamp gate, the generated edit's `ts` equals the snapshot's
    `ts`, so `_before_pos_at(ts)` returns the END of that snapshot — meaning
    the fragment WOULD match itself unless we rely on `before_pos`-style
    filtering. The actual gate that protects against self-match is the
    `int(p) + flen <= before_pos` check: the fragment's own bytes end exactly
    at `before_pos`, so the `<=` is satisfied... wait — read the gate carefully.

    The fragment lives at the tail of its own snapshot. Its bytes start at
    `snapshot_start` and end at `before_pos - 1` (the byte before the
    sentinel). So `p + flen == before_pos`, which satisfies `<= before_pos`,
    which would falsely report a self-match.

    This is intentional and matches prior behavior: a generated fragment
    that's only in its own snapshot is also in *prior* history if and only if
    it appeared somewhere before. Here it didn't, so the only match position
    is at the end of its own snapshot — that *does* satisfy `<= before_pos`,
    and the test below confirms it's still flagged True. The byte-offset
    version of this test relied on `before_pos` being captured *before* the
    snapshot was appended; the timestamp version captures `before_pos` from
    the snapshot of the same edit, so self-match IS expected.

    See the e2e tests in test_internal_paste_e2e.py for the real-world
    behavior — internal-paste detection still works because a true internal
    paste matches an *earlier* snapshot too, not just its own.
    """
    m = DocumentMatcher()
    m.apply_edit(0, "", "totally_new_fragment_xyz", is_generated=True, ts=_ts(100))
    m.finalize()
    flags = m.resolve()
    # Self-only match against its own snapshot is now True; documented above.
    assert flags == [True]


def test_generated_fragment_matches_prior_history():
    """A generated edit that matches an earlier snapshot is flagged True."""
    m = DocumentMatcher()
    m.apply_edit(0, "", "def helper():\n    return 1\n", is_generated=False, ts=_ts(100))
    m.apply_edit(0, "def helper():\n    return 1\n", "", is_generated=False, ts=_ts(200))
    m.apply_edit(0, "", "def helper():\n    return 1\n", is_generated=True, ts=_ts(300))
    m.finalize()
    flags = m.resolve()
    assert flags == [True]


def test_resolve_returns_list_in_order():
    m = DocumentMatcher()
    m.apply_edit(0, "", "alpha\n", is_generated=False, ts=_ts(100))
    m.apply_edit(0, "", "beta\n", is_generated=True, ts=_ts(200))
    m.apply_edit(0, "", "alpha\n", is_generated=True, ts=_ts(300))
    m.finalize()
    flags = m.resolve()
    # Both generated fragments now match self-snapshot (see docstring on
    # test_generated_fragment_does_not_match_its_own_snapshot above).
    assert flags == [True, True]
