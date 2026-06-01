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


def test_returns_exactly_the_feature_columns():
    feats = gcg._cadence_features([_edit(_t(0)), _edit(_t(10))])
    assert set(feats) == set(gcg.CADENCE_FEATURE_COLUMNS)
    assert len(gcg.CADENCE_FEATURE_COLUMNS) == 11


def test_zero_edits_all_zero():
    feats = gcg._cadence_features([_focus(_t(0), True)])
    assert all(v == 0.0 for v in feats.values())


def test_single_edit_zeros_gap_features():
    feats = gcg._cadence_features([_edit(_t(0), new="hello")])
    # Gap-derived features are 0 with <2 edits ...
    for col in ("mean_gap", "median_gap", "burstiness", "gap_cv", "longest_pause"):
        assert feats[col] == 0.0
    # ... but bin-derived features follow their formulas on the single active bin.
    assert feats["num_bursts"] == 1.0
    assert feats["active_bin_frac"] == 1.0
    assert feats["activity_centroid"] == 0.0
    # single active bin: max == mean, so the ratio is 1
    assert feats["peak_to_mean"] == 1.0


def test_bursty_session_more_bursty_than_steady():
    steady = [_edit(_t(i * 10)) for i in range(11)]            # uniform 10s gaps
    bursty = [_edit(_t(t)) for t in (0, 1, 2, 3, 90, 91, 92)]  # tight clusters + long gap
    assert gcg._cadence_features(bursty)["burstiness"] > gcg._cadence_features(steady)["burstiness"]


def test_front_loaded_centroid_less_than_back_loaded():
    front = [_edit(_t(t), new="x" * 50) for t in (0, 5, 10, 15)] + [_edit(_t(120), new="x")]
    back = [_edit(_t(0), new="x")] + [_edit(_t(t), new="x" * 50) for t in (105, 110, 115, 120)]
    assert (gcg._cadence_features(front)["activity_centroid"]
            < gcg._cadence_features(back)["activity_centroid"])


def test_edit_rate_is_edits_per_active_minute():
    # 3 edits in one 30s bin -> 1 active bin -> 0.5 active min -> 3 / 0.5 == 6.0
    feats = gcg._cadence_features([_edit(_t(0)), _edit(_t(1)), _edit(_t(2))])
    assert feats["edit_rate"] == 6.0


def _write_recording(path: Path, events: list[dict]) -> None:
    with gzip.open(path, "wt") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _build_export(tmp_path: Path) -> Path:
    export = tmp_path / "assignment_7360081_export"
    sub = export / "submission_111"
    sub.mkdir(parents=True)
    (export / "submission_metadata.yml").write_text(
        "submission_111:\n"
        "  :submitters:\n"
        "  - :sid: 999\n"
        "    :email: stud@byu.edu\n"
    )
    raw = [
        {"timestamp": _t(0), "document": "analyze_logs.py", "offset": 0, "oldFragment": "", "newFragment": "h"},
        {"timestamp": _t(5), "document": "analyze_logs.py", "offset": 1, "oldFragment": "", "newFragment": "e"},
        {"timestamp": _t(40), "document": "analyze_logs.py", "offset": 2, "oldFragment": "", "newFragment": "llo"},
    ]
    _write_recording(sub / "analyze_logs.recording.jsonl.gz", raw)
    return export


def test_generate_cadence_features_writes_one_row_per_session(tmp_path):
    export = _build_export(tmp_path)
    out = tmp_path / "out"
    student_map = gcg.generate_submission_student_map(export)

    gcg.generate_cadence_features(export, out, None, [], None, student_map)

    with open(out / "cadence_features.csv") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 1
    row = rows[0]
    assert row["submission"] == "submission_111"
    assert row["student_id"] == "999"
    assert row["problem"] == "analyze_logs"           # current code strips the .py extension
    assert list(row.keys())[:3] == ["submission", "student_id", "problem"]
    assert set(list(row.keys())[3:]) == set(gcg.CADENCE_FEATURE_COLUMNS)
    assert float(row["edit_rate"]) > 0
    assert int(float(row["num_bursts"])) >= 1


def test_generate_cadence_features_one_row_per_submission(tmp_path):
    export = tmp_path / "assignment_7360081_export"
    export.mkdir(parents=True)
    (export / "submission_metadata.yml").write_text(
        "submission_111:\n  :submitters:\n  - :sid: 999\n    :email: a@byu.edu\n"
        "submission_222:\n  :submitters:\n  - :sid: 888\n    :email: b@byu.edu\n"
    )
    (export / "submission_111").mkdir()
    (export / "submission_222").mkdir()
    _write_recording(export / "submission_111" / "analyze_logs.recording.jsonl.gz", [
        {"timestamp": _t(0), "document": "analyze_logs.py", "offset": 0, "oldFragment": "", "newFragment": "h"},
        {"timestamp": _t(5), "document": "analyze_logs.py", "offset": 1, "oldFragment": "", "newFragment": "e"},
    ])
    _write_recording(export / "submission_222" / "remove_frequent.recording.jsonl.gz", [
        {"timestamp": _t(0), "document": "remove_frequent.py", "offset": 0, "oldFragment": "", "newFragment": "x"},
        {"timestamp": _t(8), "document": "remove_frequent.py", "offset": 1, "oldFragment": "", "newFragment": "yz"},
    ])
    out = tmp_path / "out"
    student_map = gcg.generate_submission_student_map(export)

    gcg.generate_cadence_features(export, out, None, [], None, student_map)

    with open(out / "cadence_features.csv") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 2
    assert {(r["submission"], r["problem"], r["student_id"]) for r in rows} == {
        ("submission_111", "analyze_logs", "999"),
        ("submission_222", "remove_frequent", "888"),
    }


def test_features_subcommand_parses(tmp_path):
    parser = gcg._build_parser()
    args = parser.parse_args(["features", str(tmp_path), str(tmp_path / "out")])
    assert args.func is gcg._handle_features
    assert args.folder == tmp_path
    assert args.output == tmp_path / "out"


def test_graphs_subcommand_still_parses(tmp_path):
    parser = gcg._build_parser()
    args = parser.parse_args(["graphs", str(tmp_path), str(tmp_path / "out")])
    assert args.func is gcg._handle_graphs


def test_common_options_present_on_features(tmp_path):
    parser = gcg._build_parser()
    args = parser.parse_args(
        ["features", str(tmp_path), str(tmp_path / "out"),
         "--exclude", ".md", ".html",
         "--approved-pastes", str(tmp_path / "frags.txt")]
    )
    assert args.exclude == [".md", ".html"]
    assert args.approved_pastes == tmp_path / "frags.txt"
