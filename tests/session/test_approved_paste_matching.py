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


@pytest.mark.parametrize("fragment", ["- a", "1. i", "* e", "> t", "1) a", "- the"])
def test_markers_cannot_shrink_a_short_paste_into_a_match(fragment):
    """Stripping a list marker leaves a character or two, which is in almost any source."""
    assert not _is_approved_paste(fragment, [INSTRUCTIONS])


def test_a_short_exact_match_is_still_approved():
    """The length floor is only for the loose check; exact matches are unaffected."""
    assert _is_approved_paste("Example", [INSTRUCTIONS])
