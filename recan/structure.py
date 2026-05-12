import re
from datetime import datetime
from typing import Literal, TypedDict

# Matches an IDE-completed function stub, e.g.:
#   def foo():\n    pass
#   def foo(a, b):\n    pass
#   def foo(a: int) -> str:\n    pass
#   def foo():\n        (cursor sits on indented empty line)
CREATE_FUNCTION_PATTERN = re.compile(
    r'^[ \t]*def\s+\w+\s*\([^)]*\)\s*(?:->\s*[^:]+?)?\s*:[ \t]*\n[ \t]+(?:pass\b|\.\.\.|$)',
    re.MULTILINE,
)

# Matches the IDE-completed main guard, e.g.:
#   if __name__ == "__main__":\n
#   if __name__ == '__main__':\n        main()
MAIN_BLOCK_PATTERN = re.compile(
    r'^[ \t]*if\s+__name__\s*==\s*[\'"]__main__[\'"]\s*:[ \t]*\n[ \t]+',
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
    kind: Literal['paste', 'ide_action']
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
    # kind ∈ {"focus", "paste", "ide_action"}
    kind: str
    timestamp: datetime
    duration: float
    line_count: int
    char_count: int
    fragment: str


class Session(TypedDict):
    document: str
    language: str
    start_time: datetime
    end_time: datetime
    total_time: float
    total_time_unfocused: float

    total_edits: int
    total_pastes: int
    total_ide_actions: int
    total_generated_events: int

    events: list[dict]
    focus_intervals: list[FocusInterval]
    idle_gaps: list[IdleGap]
    bursts: list[Burst]
    snapshots: list[Snapshot]

    timeline: list[TimelineEntry]
