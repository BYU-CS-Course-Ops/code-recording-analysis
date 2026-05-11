import pytest

from recan.structure import MAIN_BLOCK_PATTERN


# IDE-completed main guard: `if __name__ == "__main__":` followed by
# auto-inserted indentation on the next line. SHOULD match.
test_case_1 = """if __name__ == "__main__":\n\t"""
test_case_2 = """if __name__ == '__main__':\n\t"""
test_case_3 = """if __name__ == "__main__":\n\tmain()"""
test_case_4 = """if __name__ == '__main__':\n    main()"""

# Incomplete: no indentation on the line after the colon. SHOULD NOT match.
test_case_5 = """if __name__ == "__main__":\n"""
test_case_6 = """if __name__ == '__main__':\n"""


@pytest.mark.parametrize(
    "fragment",
    [test_case_1, test_case_2, test_case_3, test_case_4],
)
def test_main_block_matches(fragment):
    assert MAIN_BLOCK_PATTERN.search(fragment) is not None


@pytest.mark.parametrize(
    "fragment",
    [test_case_5, test_case_6],
)
def test_main_block_without_indent_does_not_match(fragment):
    assert MAIN_BLOCK_PATTERN.search(fragment) is None
