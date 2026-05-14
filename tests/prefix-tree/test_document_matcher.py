from recan.algorithm import DocumentMatcher


def test_document_property_tracks_current_state():
    m = DocumentMatcher()
    assert m.document == ""
    m.apply_edit(0, "", "hello", is_generated=False)
    assert m.document == "hello"
    m.apply_edit(5, "", " world", is_generated=False)
    assert m.document == "hello world"
    m.apply_edit(0, "hello", "goodbye", is_generated=False)
    assert m.document == "goodbye world"


def test_apply_edit_handles_replacement():
    m = DocumentMatcher()
    m.apply_edit(0, "", "abc def", is_generated=False)
    m.apply_edit(4, "def", "xyz", is_generated=False)
    assert m.document == "abc xyz"


def test_contains_finds_fragment_in_current_doc():
    m = DocumentMatcher()
    m.apply_edit(0, "", "def main():\n    pass", is_generated=False)
    m.finalize()
    assert m._contains("def main", before_pos=len(m._history_bytes)) is True


def test_contains_finds_deleted_fragment():
    m = DocumentMatcher()
    m.apply_edit(0, "", "def helper(): pass\n", is_generated=False)
    m.apply_edit(0, "def helper(): pass\n", "", is_generated=False)
    assert m.document == ""
    m.finalize()
    assert m._contains("def helper", before_pos=len(m._history_bytes)) is True


def test_contains_returns_false_for_never_typed_fragment():
    m = DocumentMatcher()
    m.apply_edit(0, "", "print('hi')", is_generated=False)
    m.finalize()
    assert m._contains("import os", before_pos=len(m._history_bytes)) is False


def test_contains_finds_intermediate_state_clean():
    """User typed 'def man', then inserted 'i' at offset 6 to fix to 'def main'.
    The transient 'def man' state must be findable via substring search."""
    m = DocumentMatcher()
    for i, ch in enumerate("def man"):
        m.apply_edit(i, "", ch, is_generated=False)
    assert m.document == "def man"
    m.apply_edit(6, "", "i", is_generated=False)
    assert m.document == "def main"
    m.finalize()
    assert m._contains("def man", before_pos=len(m._history_bytes)) is True


def test_sentinel_prevents_matches_spanning_snapshots():
    """Without the NUL sentinel, "abc\\x00xyz" could match "cx".
    With the sentinel, no match must span the join."""
    m = DocumentMatcher()
    m.apply_edit(0, "", "abc", is_generated=False)
    m.apply_edit(0, "abc", "xyz", is_generated=False)
    m.finalize()
    assert m._contains("cx", before_pos=len(m._history_bytes)) is False


def test_before_pos_prevents_self_match():
    """A generated edit must not match its own freshly-appended snapshot."""
    m = DocumentMatcher()
    m.apply_edit(0, "", "totally_new_fragment_xyz", is_generated=True)
    m.finalize()
    flags = m.resolve()
    assert flags == [False]


def test_before_pos_allows_match_against_prior_history():
    """A generated edit IS allowed to match anything strictly before its
    own snapshot."""
    m = DocumentMatcher()
    m.apply_edit(0, "", "def helper():\n    return 1\n", is_generated=False)
    m.apply_edit(0, "def helper():\n    return 1\n", "", is_generated=False)
    m.apply_edit(0, "", "def helper():\n    return 1\n", is_generated=True)
    m.finalize()
    flags = m.resolve()
    assert flags == [True]


def test_resolve_returns_list_in_order():
    m = DocumentMatcher()
    m.apply_edit(0, "", "alpha\n", is_generated=False)
    m.apply_edit(0, "", "beta\n", is_generated=True)
    m.apply_edit(0, "", "alpha\n", is_generated=True)
    m.finalize()
    flags = m.resolve()
    assert flags == [False, True]
