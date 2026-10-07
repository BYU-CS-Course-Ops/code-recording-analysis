"""Exercise generated-edit detection, clustering, and classification together."""

import pytest

from recan.algorithm import build_matcher
from recan.session import analyze_events


def edit(fragment, *, timestamp="2026-01-01T00:00:01Z", old="", offset=0):
    return {"type": "edit", "document": "main.py", "timestamp": timestamp,
            "offset": offset, "oldFragment": old, "newFragment": fragment}


@pytest.mark.parametrize("fragment", [
    "def foo():\n    pass",
    "def foo():\n    pass\n",
    "def foo(a, b):\n\t...\n",
    "def foo(a: int) -> str:\n    pass\n",
    "    def foo():\n        pass\n\n",
    "def foo():\n    ",
    "def foo():\r\n    pass\r\n",
    'if __name__ == "__main__":\n    ',
    "if __name__ == '__main__':\n\tmain()",
    'if __name__ == "__main__":\n    main()\n',
    'if __name__ == "__main__":\r\n    main()\r\n',
])
def test_completed_templates_are_classified_as_ide_actions(fragment):
    events = [edit(fragment)]
    session = analyze_events(events, [build_matcher(events)])
    assert [a["kind"] for a in session["annotations"]] == ["ide_action"]
    assert session["annotations"][0]["review_severity"] == "LOW"
    assert session["review_severity"] == "LOW"


@pytest.mark.parametrize("fragment", [
    "def foo():\n",
    "def foo():\n    return 42\n",
    "def foo():\n    pass\nprint('extra code')\n",
    "def foo():\n    pass\n    print('extra code')\n",
    "def foo():\n    pass; print('extra code')",
    "print('extra code')\ndef foo():\n    pass\n",
    'if __name__ == "__main__":\n',
    'if __name__ == "__main__\':\n    main()',
    'if __name__ == "__main__":\n    main()\nprint("extra code")',
    'if __name__ == "__main__":\n    print("custom body")',
])
def test_incomplete_or_non_template_code_remains_a_paste(fragment):
    events = [edit(fragment)]
    session = analyze_events(events, [build_matcher(events)])
    assert [a["kind"] for a in session["annotations"]] == ["unapproved_paste"]
    assert session["annotations"][0]["review_severity"] == "HIGH"
    assert session["review_severity"] == "HIGH"


def test_template_detection_precedes_approved_and_internal_paste_matching():
    fragment = "def foo():\n    pass\n"
    history = [edit(fragment, timestamp="2026-01-01T00:00:00Z")]
    session = analyze_events([edit(fragment)], [build_matcher(history)], approved_pastes=fragment)
    kinds = [a["kind"] for a in session["annotations"]]
    assert kinds == ["ide_action"]
    assert session["review_severity"] == "LOW"


def test_template_in_cluster_is_detected_when_longest_fragment_is_not_a_stub():
    stub = "def foo():\n    pass\n"
    replacement = "def foo():\n    return 'completed template'\n"
    events = [edit(stub), edit(replacement, old=stub, timestamp="2026-01-01T00:00:01.010Z")]
    session = analyze_events(events, [build_matcher(events)])
    annotation, = session["annotations"]
    assert annotation["kind"] == "ide_action"
    assert (annotation["entry_start"], annotation["entry_end"]) == (0, 1)
    assert annotation["fragment"] == replacement
    assert session["snapshots"][-1]["document_text"] == replacement
