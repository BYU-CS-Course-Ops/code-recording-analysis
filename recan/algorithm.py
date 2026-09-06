"""
DocumentMatcher — searchable index over a document's full edit history.

After every edit, the full post-edit document is appended to a growing
bytearray, separated by a NUL SENTINEL. After finalize(), a suffix array is
built over that corpus and `contains(fragment, ts)` answers
"did this fragment appear anywhere in the document strictly before `ts`?".

Cross-recording matching is just `any(m.contains(...) for m in matchers)` —
each matcher independently resolves the timestamp to its own byte cutoff via
its `_snapshot_times` index.

Complexity (E edits, D avg doc size, B = corpus ≈ E·D, F fragment len):
  apply_edit  O(D) time / O(D) corpus growth
  finalize    O(B) time and space (suffix array)
  contains    O(F log B + matches)

Corpus grows as O(E·D), so this assumes one student / one file / one sitting.
"""

import bisect
from datetime import datetime

from pydivsufsort import divsufsort, sa_search

from recan.utils import parse_ts, splice


class DocumentMatcher:
    """
    Build a searchable index over a document's full history and answer
    "was this fragment in the document strictly before this timestamp?".

    Use:
        m = DocumentMatcher()
        for edit in edits:
            m.apply_edit(offset, old, new, ts=edit_ts)
        m.finalize()
        m.contains(fragment, ts)
    """

    # NUL byte. Used as a snapshot separator inside `_history_bytes` so that
    # no substring search can accidentally match across the boundary between
    # two adjacent snapshots. The user's source code is UTF-8 text and will
    # never contain a raw NUL, so this is a safe terminator.
    SENTINEL = 0

    def __init__(self) -> None:
        # Live document state after the most recent edit.
        self._document: str = ""

        # Concatenation of every post-edit snapshot, separated by SENTINEL.
        # This is the searchable corpus the suffix array is built over.
        self._history_bytes: bytearray = bytearray()

        # Parallel arrays mapping snapshot index -> (end-byte-offset, timestamp).
        # `_snapshot_ends[i]` is len(_history_bytes) after appending snapshot i
        # plus its trailing sentinel — i.e. the exclusive upper bound of snapshot i.
        self._snapshot_ends: list[int] = []
        self._snapshot_times: list[datetime] = []

        # The suffix array over `_history_bytes`. Populated by finalize().
        self._sa = None

    @property
    def document(self) -> str:
        """The current live document text, reflecting every edit applied so far."""
        return self._document

    def apply_edit(
            self,
            offset: int,
            old_fragment: str,
            new_fragment: str,
            ts: datetime,
    ) -> None:
        """
        Apply an edit to the live document and append the post-edit state to history.

        Updates:
            - live `_document` (splice in `new_fragment` at `offset`)
            - `_history_bytes` (append the full post-edit doc + SENTINEL)
            - `_snapshot_ends` / `_snapshot_times` (so a wall-clock ts can later
              be resolved to a byte cutoff)
        """
        if self._snapshot_times and ts < self._snapshot_times[-1]:
            raise ValueError("DocumentMatcher edits must be in timestamp order.")
        self._sa = None
        self._document = splice(self._document, offset, old_fragment, new_fragment)

        # Append the new full document state to the corpus, terminated by SENTINEL.
        # We snapshot the WHOLE document (not just the diff) so the suffix array
        # can answer "did this substring ever exist anywhere in the document?"
        # without having to reconstruct intermediate states.
        self._history_bytes.extend(self._document.encode("utf-8"))
        self._history_bytes.append(self.SENTINEL)

        self._snapshot_ends.append(len(self._history_bytes))
        self._snapshot_times.append(ts)

    def finalize(self) -> None:
        """Build the suffix array over the concatenated history. Required before contains()."""
        self._sa = divsufsort(self._history_bytes)

    def contains(self, fragment: str, ts: datetime) -> bool:
        """
        Has `fragment` appeared anywhere in the document strictly before `ts`?

        Steps:
            - Convert `ts` to a byte cutoff in the corpus.
            - Find every position where `fragment` occurs in the corpus.
            - Return True iff at least one occurrence ends at or before the cutoff
              (i.e. lies entirely inside a snapshot that was already recorded by `ts`).
        """
        if self._sa is None:
            raise RuntimeError(
                "DocumentMatcher.contains called before finalize()."
            )

        # Empty fragment is never a meaningful match.
        if not fragment:
            return False

        cutoff = self._before_pos_at(ts)

        # No snapshots predate `ts`, so nothing could have been seen yet.
        if cutoff == 0:
            return False

        needle = fragment.encode("utf-8")
        positions = self._find_match_positions(needle)
        if len(positions) == 0:
            return False

        # A match "happened before ts" iff it lies entirely inside the prefix
        # of the corpus that was already written by `ts`. The match starts at
        # `p` and ends at `p + len(needle)` (exclusive); requiring that end
        # offset to be <= cutoff guarantees the match doesn't spill into a
        # snapshot that came after `ts`.
        needle_len = len(needle)
        return any(int(p) + needle_len <= cutoff for p in positions)

    def _before_pos_at(self, ts: datetime) -> int:
        """
        Return the exclusive byte cutoff in `_history_bytes` corresponding to `ts`:
        only corpus positions strictly less than this cutoff happened before `ts`.

        Uses bisect_left so that a ts equal to a snapshot's timestamp returns
        the end of the PRIOR snapshot, not that snapshot's own end. This is
        what prevents self-capture: a generated edit's fragment trivially
        appears in the snapshot that edit produced, so excluding it means we
        only report a match when the fragment also lived in some earlier
        snapshot. A ts earlier than every snapshot returns 0.
        """
        # bisect_left returns the leftmost index i where _snapshot_times[i] >= ts.
        # Stepping back one gives the latest snapshot whose timestamp is strictly
        # less than ts — i.e. the snapshot that came BEFORE the edit we're querying.
        i = bisect.bisect_left(self._snapshot_times, ts) - 1
        return self._snapshot_ends[i] if i >= 0 else 0

    def _find_match_positions(self, needle: bytes):
        """
        Return all starting byte positions where `needle` occurs in the corpus.

        Wraps pydivsufsort.sa_search, which returns (count, sa_offset) — a
        contiguous slice of the suffix array whose entries are the matching
        positions. Returns an empty list when there are no matches; otherwise
        returns a numpy view into `self._sa`, so callers should use len()
        rather than truth-testing the result directly.
        """
        count, sa_offset = sa_search(self._history_bytes, self._sa, needle)
        if count == 0:
            # sa_offset is None when count is 0 — guard before slicing _sa.
            return []
        return self._sa[sa_offset: sa_offset + count]


def build_matcher(events: list[dict]) -> DocumentMatcher:
    """
    Build and finalize a DocumentMatcher from a recording's event stream.

    Skips non-edit events (focusStatus etc.) and uses each edit's timestamp so
    later cross-matcher queries can be resolved by wall-clock time.

    Usage: load_sessions() builds one matcher per recording and passes the
    whole list into analyze_events(), which uses them to classify generated
    edits as internal pastes (matches in this recording or any overlapping one).
    """
    matcher = DocumentMatcher()

    for event in events:
        if "newFragment" not in event:
            continue

        matcher.apply_edit(
            offset=event.get("offset", 0),
            old_fragment=event.get("oldFragment", ""),
            new_fragment=event["newFragment"],
            ts=parse_ts(event["timestamp"]),
        )

    matcher.finalize()
    return matcher
