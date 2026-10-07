import re
from datetime import datetime
from typing import Literal, TypedDict

CREATE_FUNCTION_PATTERN = re.compile(
    r'^[ \t]*def[ \t]+\w+[ \t]*\([^\r\n)]*\)[ \t]*(?:->[ \t]*[^:\r\n]+?)?[ \t]*:'
    r'[ \t]*\r?\n[ \t]+(?:(?:pass|\.\.\.)[ \t]*)?(?:\r?\n[ \t]*)*\Z',
    re.MULTILINE,
)
MAIN_BLOCK_PATTERN = re.compile(
    r'^[ \t]*if[ \t]+__name__[ \t]*==[ \t]*(?P<quote>[\'\"])__main__(?P=quote)[ \t]*:'
    r'[ \t]*\r?\n[ \t]+(?:main[ \t]*\([ \t]*\)[ \t]*)?(?:\r?\n[ \t]*)*\Z',
    re.MULTILINE,
)


class Input(TypedDict):
    type: Literal["focusStatus", "edit"]
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


class InitialSnapshotInput(TypedDict):
    type: Literal["initialSnapshot"]
    document_text: str


AnnotationKind = Literal[
    "ide_action",
    "approved_paste",
    "internal_paste",
    "unapproved_paste",
    "unfocused",
    "idle_gap",
    "starter_code_mismatch",
]
ReviewSeverity = Literal["LOW", "MEDIUM", "HIGH"]


class AnnotationFields(TypedDict):
    kind: AnnotationKind
    review_severity: ReviewSeverity | None
    timestamp: datetime
    entry_start: int | None
    entry_end: int | None


class Annotation(AnnotationFields, total=False):
    source_entry: int
    end_timestamp: datetime
    duration: float
    line_count: int
    char_count: int
    fragment: str


class InitialSnapshotEntry(TypedDict):
    timestamp: str
    type: Literal["initialSnapshot"]
    document_text: str


class EditEntry(TypedDict):
    timestamp: str
    type: Literal["edit"]
    offset: int
    oldFragment: str
    newFragment: str


class FocusStatusEntry(TypedDict):
    timestamp: str
    type: Literal["focusStatus"]
    focused: bool


Entry = InitialSnapshotEntry | EditEntry | FocusStatusEntry


class Snapshot(TypedDict):
    after_idx: int
    document_text: str


class Session(TypedDict):
    document: str
    language: str
    start_time: datetime | None
    end_time: datetime | None
    total_time: float
    total_time_unfocused: float
    total_time_typing: float
    total_edits: int
    total_chars: int
    total_typed_chars: int
    total_pasted_chars: int
    total_deleted_chars: int
    starts_with_starter_code: bool | None
    review_severity: ReviewSeverity | None
    entries: list[Entry]
    annotations: list[Annotation]
    snapshots: list[Snapshot]
