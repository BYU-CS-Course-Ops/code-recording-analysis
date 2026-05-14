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
