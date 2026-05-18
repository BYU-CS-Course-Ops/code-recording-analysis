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

    SENTINEL = 0

    def __init__(self) -> None:
        self._current: str = ""
        self._history_bytes: bytearray = bytearray()
        self._pending_checks: list[tuple[str, datetime]] = []
        self._snapshot_ends: list[int] = []
        self._snapshot_times: list[datetime] = []
        self._sa = None

    @property
    def document(self) -> str:
        return self._current

    def apply_edit(
        self,
        offset: int,
        old_fragment: str,
        new_fragment: str,
        is_generated: bool,
        ts: datetime,
    ) -> None:
        if is_generated:
            self._pending_checks.append((new_fragment, ts))
        end = offset + len(old_fragment)
        self._current = self._current[:offset] + new_fragment + self._current[end:]
        self._history_bytes.extend(self._current.encode("utf-8"))
        self._history_bytes.append(self.SENTINEL)
        self._snapshot_ends.append(len(self._history_bytes))
        self._snapshot_times.append(ts)

    def finalize(self) -> None:
        self._sa = divsufsort(self._history_bytes)

    def resolve(self, extras: Sequence["DocumentMatcher"] = ()) -> list[bool]:
        return [
            self._contains(frag, ts)
            or any(extra._contains(frag, ts) for extra in extras)
            for frag, ts in self._pending_checks
        ]

    def _before_pos_at(self, ts: datetime) -> int:
        i = bisect.bisect_left(self._snapshot_times, ts) - 1
        return self._snapshot_ends[i] if i >= 0 else 0

    def _contains(self, fragment: str, ts: datetime) -> bool:
        if self._sa is None:
            raise RuntimeError(
                "DocumentMatcher._contains called before finalize(); "
                "all matchers (including extras) must be finalize()-d before resolve()."
            )
        if not fragment:
            return False
        before_pos = self._before_pos_at(ts)
        if before_pos == 0:
            return False
        fb = fragment.encode("utf-8")
        count, sa_pos = sa_search(self._history_bytes, self._sa, fb)
        if count == 0:
            return False
        positions = self._sa[sa_pos : sa_pos + count]
        flen = len(fb)
        return any(int(p) + flen <= before_pos for p in positions)
