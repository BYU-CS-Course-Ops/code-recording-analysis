"""
DocumentMatcher — "was this fragment ever in the document before time T?"

After every edit we append the full post-edit document to a growing
bytearray, separated by a NUL SENTINEL, then build a suffix array over it.
Substring search becomes O(F log B); a parallel (snapshot_end, timestamp)
index converts a wall-clock cutoff into a byte cutoff via bisect.

Complexity (E edits, D avg doc size, B = corpus ≈ E·D, F fragment len):
  apply_edit  O(D) time / O(D) corpus growth
  finalize    O(B) time and space (suffix array)
  _contains   O(F log B + matches)
  resolve     sums _contains over queued checks × matchers consulted

Corpus grows as O(E·D), so this assumes one student / one file / one sitting.
"""

import bisect
from datetime import datetime
from typing import Sequence

from pydivsufsort import divsufsort, sa_search


class DocumentMatcher:
    """Tracks the live document and the full history of its states, then
    answers "did this fragment ever appear in the document before this point
    in wall-clock time?"

    The gate is a *timestamp*, not a byte offset, so multiple matchers (one
    per recording) can be queried against each other — each matcher resolves
    the timestamp to its own byte cutoff via a parallel index.

    Use:
        m = DocumentMatcher()
        for edit in events:
            m.apply_edit(offset, old, new, is_generated=..., ts=edit_ts)
        m.finalize()
        flags = m.resolve()              # self-only
        flags = m.resolve([other, ...])  # cross-recording
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

        # Queued (fragment, ts) pairs from generated edits, answered by resolve().
        self._pending_checks: list[tuple[str, datetime]] = []

        # Parallel arrays mapping snapshot index -> (end-byte-offset, timestamp).
        # `_snapshot_ends[i]` is len(_history_bytes) after appending snapshot i
        # plus its trailing sentinel — i.e. the exclusive upper bound of snapshot i.
        self._snapshot_ends: list[int] = []
        self._snapshot_times: list[datetime] = []

        # The suffix array over `_history_bytes`. Populated by finalize().
        self._sa = None

    def _before_pos_at(self, ts: datetime) -> int:
        """
        Return the exclusive byte cutoff in `_history_bytes` corresponding to `ts`:
        only corpus positions strictly less than this cutoff happened before `ts`.

        Uses bisect_left so that a ts equal to a snapshot's timestamp returns
        the end of the PRIOR snapshot, not that snapshot's own end. This is
        what prevents self-capture: every queued check inherits the timestamp
        of the edit that created it, and that edit's snapshot is the very
        place its fragment trivially appears. Excluding it means we only
        report a match when the fragment also lived in some earlier snapshot.
        A ts earlier than every snapshot returns 0.
        """
        # bisect_left returns the leftmost index i where _snapshot_times[i] >= ts.
        # Stepping back one gives the latest snapshot whose timestamp is strictly
        # less than ts — i.e. the snapshot that came BEFORE the edit we're querying.
        i = bisect.bisect_left(self._snapshot_times, ts) - 1
        return self._snapshot_ends[i] if i >= 0 else 0

    def _find_match_positions(self, needle: bytes) -> Sequence[int]:
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

    def _contains(self, fragment: str, ts: datetime) -> bool:
        """
        Has `fragment` appeared anywhere in the document at any moment before `ts`?

        Steps:
            1. Convert `ts` to a byte cutoff in the corpus.
            2. Find every position where `fragment` occurs in the corpus.
            3. Return True iff at least one occurrence ends at or before the cutoff
               (i.e. lies entirely inside a snapshot that was already recorded by `ts`).
        """
        if self._sa is None:
            raise RuntimeError(
                "DocumentMatcher._contains called before finalize(); "
                "all matchers (including extras) must be finalize()-d before resolve()."
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

    @property
    def document(self) -> str:
        """
        The current live document text, reflecting every edit applied so far.
        """
        return self._document

    def apply_edit(
            self,
            offset: int,
            old_fragment: str,
            new_fragment: str,
            is_generated: bool,
            ts: datetime,
    ) -> None:
        """
        Apply an edit to the live document and append the post-edit state to history.

        Generated edits also queue a pending "did this exist before `ts`?" check,
        to be answered later by `resolve()` once every matcher has been finalized.
        """
        # Queue the lookup now; we can't answer it yet because future edits
        # (and other matchers' edits) still need to be folded into the corpus.
        if is_generated:
            self._pending_checks.append((new_fragment, ts))

        # Splice the edit into the live document: keep everything before
        # `offset`, drop the `len(old_fragment)` characters that were there,
        # and insert `new_fragment` in their place.
        before_edit = self._document[:offset]
        after_edit = self._document[offset + len(old_fragment):]
        self._document = before_edit + new_fragment + after_edit

        # Append the new full document state to the corpus, terminated by SENTINEL.
        # We snapshot the WHOLE document (not just the diff) so the suffix array
        # can answer "did this substring ever exist anywhere in the document?"
        # without having to reconstruct intermediate states.
        self._history_bytes.extend(self._document.encode("utf-8"))
        self._history_bytes.append(self.SENTINEL)

        # Index this snapshot's end-byte and timestamp so _before_pos_at can
        # later convert a wall-clock cutoff into a byte cutoff.
        self._snapshot_ends.append(len(self._history_bytes))
        self._snapshot_times.append(ts)

    def finalize(self) -> None:
        """
        Build the suffix array over the concatenated history.

        Must be called before resolve(). All matchers participating in a
        cross-recording resolve() — both `self` and every entry in `extras` —
        must have been finalize()-d first.
        """
        self._sa = divsufsort(self._history_bytes)

    def resolve(self, extras: Sequence["DocumentMatcher"] = ()) -> list[bool]:
        """
        Answer each queued generated edit with True/False: did this fragment
        exist in some document state before its timestamp, either in this
        matcher's history or in any of the given extra matchers?

        Order of the returned list matches the order edits were queued via
        apply_edit(..., is_generated=True).
        """
        return [
            self._contains(fragment, ts)
            or any(extra._contains(fragment, ts) for extra in extras)
            for fragment, ts in self._pending_checks
        ]
