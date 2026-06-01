import gzip
import json
import csv
import importlib.util
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "w26-final-analysis" / "generate_cadence_graph.py"

if not _SCRIPT.exists():
    pytest.skip(
        "generate_cadence_graph.py not present (w26-final-analysis is gitignored scratch).",
        allow_module_level=True,
    )


def _load_script() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("generate_cadence_graph", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gcg = _load_script()

_BASE = datetime(2026, 4, 23, 3, 0, 0, tzinfo=timezone.utc)


def _t(second: float) -> str:
    """ISO8601 timestamp `second` seconds past a fixed base (handles minute rollover)."""
    return (_BASE + timedelta(seconds=second)).isoformat()


def _edit(ts: str, new: str = "x", old: str = "") -> dict:
    return {"type": "edit", "timestamp": ts, "offset": 0, "oldFragment": old, "newFragment": new}


def _focus(ts: str, focused: bool) -> dict:
    return {"type": "focusStatus", "timestamp": ts, "focused": focused}


def test_inter_edit_gaps_ignores_focus_and_returns_seconds():
    events = [_edit(_t(0)), _focus(_t(3), False), _edit(_t(10)), _edit(_t(25))]
    gaps = gcg._inter_edit_gaps(events)
    assert list(np.round(gaps, 3)) == [10.0, 15.0]


def test_inter_edit_gaps_empty_when_under_two_edits():
    assert gcg._inter_edit_gaps([_edit(_t(0))]).size == 0
    assert gcg._inter_edit_gaps([]).size == 0


def test_burst_runs_counts_contiguous_active_bins():
    active = np.array([True, True, False, True, False, False, True, True, True])
    assert gcg._burst_runs(active) == [2, 1, 3]


def test_burst_runs_all_idle_is_empty():
    assert gcg._burst_runs(np.array([False, False])) == []


def test_burst_runs_all_active_is_single_run():
    assert gcg._burst_runs(np.array([True, True, True])) == [3]
