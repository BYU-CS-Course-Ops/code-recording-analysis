import json

from recan.session import analyze_events, annotation_counts
from recan.viewer import build_player_html, _to_player_bundle


def edit(ts, text="x", offset=0):
    return {"type": "edit", "document": "main.py", "timestamp": ts,
            "offset": offset, "oldFragment": "", "newFragment": text}


def test_annotation_references_are_bounded_and_ranges_include_cluster_fixups():
    events = [
        edit("2026-01-01T00:00:00Z", "paste!"),
        edit("2026-01-01T00:00:00.100Z", " ", 5),
        edit("2026-01-01T00:00:10Z", "z", 6),
    ]
    session = analyze_events(events)
    for annotation in session["annotations"]:
        start, end = annotation["entry_start"], annotation["entry_end"]
        assert (start is None) == (end is None)
        if start is not None:
            assert 0 <= start <= end < len(session["entries"])
    generated = [a for a in session["annotations"] if a["kind"] == "unapproved_paste"]
    assert len(generated) == 1
    assert (generated[0]["entry_start"], generated[0]["entry_end"]) == (0, 1)


def test_idle_gap_spans_entries_on_both_sides():
    session = analyze_events([
        edit("2026-01-01T00:00:00Z", "a"),
        edit("2026-01-01T00:00:10Z", "b", 1),
    ])
    idle = [a for a in session["annotations"] if a["kind"] == "idle_gap"]
    assert len(idle) == 1
    assert (idle[0]["entry_start"], idle[0]["entry_end"]) == (0, 1)
    assert idle[0]["timestamp"].isoformat() == "2026-01-01T00:00:00+00:00"
    assert idle[0]["end_timestamp"].isoformat() == "2026-01-01T00:00:10+00:00"
    bundle = _to_player_bundle([session])
    assert bundle["annotation_counts"]["idle_gap"] == 1
    assert bundle["sessions"][0]["total_time"] == 10


def test_focus_interval_does_not_also_emit_duplicate_idle_gap():
    events = [
        edit("2026-01-01T00:00:00Z"),
        {"type": "focusStatus", "focused": False, "document": "main.py",
         "timestamp": "2026-01-01T00:00:01Z"},
        {"type": "focusStatus", "focused": True, "document": "main.py",
         "timestamp": "2026-01-01T00:00:10Z"},
        edit("2026-01-01T00:00:20Z", "y", 1),
    ]
    session = analyze_events(events)
    assert [a["kind"] for a in session["annotations"]].count("unfocused") == 1
    assert not any(
        a["kind"] == "idle_gap" and (a["entry_start"], a["entry_end"]) == (1, 2)
        for a in session["annotations"]
    )
    # The later focus-to-edit inactivity is distinct and must not be hidden.
    assert any(
        a["kind"] == "idle_gap" and (a["entry_start"], a["entry_end"]) == (2, 3)
        for a in session["annotations"]
    )


def test_severity_and_reporting_counts_are_python_derived():
    session = analyze_events(
        [edit("2026-01-01T00:00:00Z", "not approved")],
        starter_code={"main.py": "starter"},
    )
    assert session["review_severity"] == "HIGH"
    assert annotation_counts(session) == {
        "ide_action": 0, "approved_paste": 0, "internal_paste": 0,
        "unapproved_paste": 1, "unfocused": 0, "idle_gap": 0,
    }
    bundle = _to_player_bundle([session])
    session_bundle = bundle["sessions"][0]
    assert bundle["review_severity"] == "HIGH"
    assert bundle["annotation_counts"] == annotation_counts(session)
    assert set(session_bundle) == {
        "document", "language", "start_time", "end_time", "total_time",
        "total_edits", "entries", "annotations", "snapshots",
    }
    assert not {"total_unapproved_pastes", "total_approved_pastes",
                 "total_internal_pastes", "total_ide_actions",
                 "total_generated_events"} & session_bundle.keys()


def test_player_bundle_sorts_sessions_chronologically_with_stable_ties():
    later = analyze_events([edit("2026-01-01T00:00:02Z", "paste!")])
    tied_first = analyze_events([edit("2026-01-01T00:00:01Z", "paste!")])
    # Equivalent naive and aware timestamps should sort together without a
    # datetime comparison error and retain caller order.
    tied_second = analyze_events([edit("2026-01-01T00:00:01", "paste!")])
    later["document"] = "later.py"
    tied_first["document"] = "first-tie.py"
    tied_second["document"] = "second-tie.py"

    bundle = _to_player_bundle([later, tied_first, tied_second])

    assert [session["document"] for session in bundle["sessions"]] == [
        "first-tie.py", "second-tie.py", "later.py",
    ]
    assert bundle["annotation_counts"]["unapproved_paste"] == 3


def test_bundle_and_player_use_entries_annotations_and_removed_navigation_is_absent():
    session = analyze_events([edit("2026-01-01T00:00:00Z")])
    bundle = _to_player_bundle([session])
    session_bundle = bundle["sessions"][0]
    assert "entries" in session_bundle and "annotations" in session_bundle
    assert not {"events", "bursts", "focus_intervals", "idle_gaps", "timeline"} & session_bundle.keys()
    html = build_player_html([session])
    # Keep this as a serialization-boundary check; player interactions belong
    # in the Chromium tests rather than generated-JS source assertions.
    payload = json.loads(html.split("window.__BUNDLE__ = ", 1)[1].split(";</script>", 1)[0])
    assert payload == bundle
    assert "display_counts" not in payload["sessions"][0]
    assert payload["annotation_counts"] == annotation_counts(session)


def test_player_has_marker_and_legend_colors_for_every_annotation_kind():
    session = analyze_events([edit("2026-01-01T00:00:00Z", "generated text")])
    html = build_player_html([session])
    for css_class in (
        "idle-gap",
        "unfocused",
        "starter-code-mismatch",
        "ide-action",
        "approved-paste",
        "internal-paste",
        "unapproved-paste",
    ):
        assert f".tick-annotation.{css_class}" in html
    for legend_class in (
        "idle",
        "unfocused",
        "ide-action",
        "approved-paste",
        "internal-paste",
        "unapproved-paste",
        "starter-code-mismatch",
    ):
        assert f'legend-swatch {legend_class}' in html
