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
