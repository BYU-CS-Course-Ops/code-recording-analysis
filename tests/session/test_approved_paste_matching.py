"""Approved pastes are matched loosely: students paste from the rendered page, not the Markdown source."""

from pathlib import Path

import pytest

from recan.session import _is_approved_paste

# Verbatim from the hw4b instructions.md: a Markdown list, hard-wrapped.
INSTRUCTIONS = """\
## 3. Out of Stock

Write a program that:

1. Using a sequence of inputs, prompt the person for a list of out-of-stock
   items
2. Using a sequence of inputs, prompt the person for the items they would like
   to purchase
   - If the item is out-of-stock, inform the person
   - Otherwise add it to the list of items requested
3. Print the available items the person indicated they want to purchase

Match the prompts and formatting from the example.

**Example**
Run it with `python out_of_stock.py` and don’t panic.
"""


@pytest.mark.parametrize("fragment", [
    # list markers dropped and wrapped lines joined, as copied from the rendered page
    "Write a program that:\n"
    "Using a sequence of inputs, prompt the person for a list of out-of-stock items\n"
    "Using a sequence of inputs, prompt the person for the items they would like to purchase\n"
    "If the item is out-of-stock, inform the person\n"
    "Otherwise add it to the list of items requested\n"
    "Print the available items the person indicated they want to purchase\n"
    "Match the prompts and formatting from the example.",
    # the same, pasted as a comment block
    "# Write a program that:\n"
    "# Using a sequence of inputs, prompt the person for a list of out-of-stock items\n"
    "# Using a sequence of inputs, prompt the person for the items they would like to purchase",
    # list markers kept, lines joined
    "1. Using a sequence of inputs, prompt the person for a list of out-of-stock items",
    # wrapped differently / extra spaces / Windows line endings
    "Using a sequence   of inputs, prompt the person\r\nfor a list of out-of-stock items",
    # bold and code formatting removed, smart apostrophe typed straight
    "Example Run it with python out_of_stock.py and don't panic.",
    # nbsp from a browser
    "Match the prompts and formatting",
])
def test_instructions_pasted_from_the_rendered_page_are_approved(fragment):
    assert _is_approved_paste(fragment, [INSTRUCTIONS])


@pytest.mark.parametrize("fragment", [
    "print('Otherwise add it to the list of items requested')",           # code, not the prose
    "Otherwise add it to the list of items requested\nUsing a sequence of inputs",  # wrong order
    "Using a sequence of inputs, prompt the person for something else",
    "def out_of_stock():\n    pass",
    # real lines from the instructions, but not contiguous: the middle items are skipped
    "Write a program that:\nMatch the prompts and formatting from the example.",
])
def test_other_text_is_not_approved_by_loose_matching(fragment):
    assert not _is_approved_paste(fragment, [INSTRUCTIONS])


def test_loose_matching_stays_within_one_source():
    sources = ["Write a program that:\n1. First thing", "2. Second thing\n"]
    assert _is_approved_paste("Write a program that: First thing", sources)
    assert not _is_approved_paste("First thing Second thing", sources)


def test_loose_matching_does_not_make_code_whitespace_insensitive():
    assert not _is_approved_paste("x=1", ["x = 1\n"])


@pytest.mark.parametrize("fragment", ["-", "#", "1.", "> ", "**"])
def test_a_paste_of_only_markers_is_not_approved(fragment):
    """It simplifies to an empty string, which is a substring of every source."""
    assert not _is_approved_paste(fragment, ["Hello world\n"])


def test_a_short_exact_match_is_still_approved():
    """Short exact matches are approved."""
    assert _is_approved_paste("Example", [INSTRUCTIONS])


# --- default approved pastes (recan/default_approved_pastes.txt) ---

from recan.session import _load_approved_pastes, _load_default_approved_pastes  # noqa: E402


@pytest.fixture
def extra_file(tmp_path):
    path = tmp_path / "extra.txt"
    path.write_text("Something only in this file\n", encoding="utf-8")
    return path


def test_the_defaults_are_added_to_the_given_files(extra_file):
    sources = _load_approved_pastes([extra_file])
    assert set(_load_default_approved_pastes()) <= set(sources)
    assert "Something only in this file\n" in sources


def test_the_defaults_apply_even_without_any_approved_pastes_path():
    defaults = _load_default_approved_pastes()
    assert defaults
    assert _load_approved_pastes(None) == defaults
    assert _load_approved_pastes([]) == defaults
    assert _is_approved_paste("if __name__ == '__main__':", _load_approved_pastes(None))


def test_each_default_entry_is_its_own_source():
    defaults = _load_default_approved_pastes()
    assert len(defaults) >= 2
    assert not any("---" in entry for entry in defaults)


@pytest.mark.parametrize("fragment", [
    "if __name__ == '__main__':",
    'if __name__ == "__main__":',
    "if __name__ == '__main__':\n    main()",
    "if __name__  ==  '__main__':",  # extra spaces
    "if __name__ == '__main__':\n\tmain()",  # different indentation
])
def test_the_main_block_is_approved_without_the_ide_template(fragment, extra_file):
    assert _is_approved_paste(fragment, _load_approved_pastes([extra_file]))


@pytest.mark.parametrize("fragment", [
    "if __name__ == '__main__':\n    evil()",
    "if __name__ == '__other__':",
])
def test_near_misses_are_not_approved(fragment, extra_file):
    assert not _is_approved_paste(fragment, _load_approved_pastes([extra_file]))
