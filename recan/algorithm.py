class DocumentMatcher:
    """Tracks the live document and the full history of its states, then
    answers "did this fragment ever appear in the document before this point?"

    Use:
        m = DocumentMatcher()
        for edit in events:
            m.apply_edit(offset, old, new, is_generated=...)
        m.finalize()
        flags = m.resolve()  # list[bool], one per is_generated=True call, in order
    """

    SENTINEL = b"\x00"

    def __init__(self) -> None:
        self._current: str = ""
        self._history_bytes: bytes = b""
        self._pending_checks: list[tuple[str, int]] = []
        self._sa = None

    @property
    def document(self) -> str:
        return self._current

    def apply_edit(self, offset: int, old_fragment: str, new_fragment: str, is_generated: bool) -> None:
        if is_generated:
            self._pending_checks.append((new_fragment, len(self._history_bytes)))
        end = offset + len(old_fragment)
        self._current = self._current[:offset] + new_fragment + self._current[end:]
        self._history_bytes += self._current.encode("utf-8") + self.SENTINEL
