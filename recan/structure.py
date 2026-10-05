import re
from datetime import datetime
from typing import Literal, TypedDict

# Matches a complete IDE function-stub fragment, with optional trailing blank lines:
#   def foo():\n    pass
#   def foo(a, b):\n    pass
#   def foo(a: int) -> str:\n    pass
#   def foo():\n        (cursor sits on indented empty line)
CREATE_FUNCTION_PATTERN = re.compile(
    r'^[ \t]*def[ \t]+\w+[ \t]*\([^\r\n)]*\)[ \t]*(?:->[ \t]*[^:\r\n]+?)?[ \t]*:'
    r'[ \t]*\r?\n[ \t]+(?:(?:pass|\.\.\.)[ \t]*)?(?:\r?\n[ \t]*)*\Z',
    re.MULTILINE,
)

# Matches the complete IDE main guard, with indentation alone or a main() call:
#   if __name__ == "__main__":\n        (cursor on an indented empty line)
#   if __name__ == '__main__':\n        main()
MAIN_BLOCK_PATTERN = re.compile(
    r'^[ \t]*if[ \t]+__name__[ \t]*==[ \t]*(?P<quote>[\'"])__main__(?P=quote)[ \t]*:'
    r'[ \t]*\r?\n[ \t]+(?:main[ \t]*\([ \t]*\)[ \t]*)?(?:\r?\n[ \t]*)*\Z',
    re.MULTILINE,
)


class Input(TypedDict):
    type: Literal['focusStatus', 'edit']
    editor: str
    recorderVersion: str
    timestamp: str


class EditInput(Input):
    document: str
    offset: int
    oldFragment: str
    newFragment: str


class FocusStatusInput(Input):
    focused: bool


class FocusInterval(TypedDict):
    blur_idx: int
    focus_idx: int
    duration: float


class IdleGap(TypedDict):
    after_idx: int
    duration: float


class Burst(TypedDict):
    kind: Literal['approved paste', 'unapproved paste', 'internal paste', 'ide_action']
    timestamp: datetime
    start_idx: int
    end_idx: int
    line_count: int
    char_count: int
    fragment: str


class Snapshot(TypedDict):
    after_idx: int
    document_text: str


class TimelineEntry(TypedDict, total=False):
    # kind ∈ {"focus", "approved paste", "unapproved paste", "internal paste", "ide_action"}
    kind: str
    timestamp: datetime
    duration: float
    line_count: int
    char_count: int
    fragment: str


class Session(TypedDict):
    document: str
    language: str
    initial_document: str
    start_time: datetime
    end_time: datetime
    total_time: float
    total_time_unfocused: float
    total_time_typing: float

    total_edits: int
    total_chars: int
    total_typed_chars: int
    total_pasted_chars: int
    total_deleted_chars: int
    total_unapproved_pastes: int
    total_approved_pastes: int
    total_internal_pastes: int
    total_ide_actions: int
    total_generated_events: int

    events: list[dict]
    focus_intervals: list[FocusInterval]
    idle_gaps: list[IdleGap]
    bursts: list[Burst]
    snapshots: list[Snapshot]

    timeline: list[TimelineEntry]
