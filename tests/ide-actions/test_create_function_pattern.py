import pytest

from recan.structure import CREATE_FUNCTION_PATTERN


# Incomplete: just the `def` line with a trailing newline, no body yet.
# The IDE has not auto-indented or inserted a stub, so these should NOT match.
test_case_1 = """def main():\n"""
test_case_2 = """def foo():\n"""

test_case_3 = """def main(args):\n"""
test_case_4 = """def foo(bar):\n"""

# Completed stubs: `def` line followed by an indented `pass`.
# These represent IDE-completed function stubs and SHOULD match.
test_case_5 = """def main():\n\tpass"""
test_case_6 = """def foo():\n\tpass"""

test_case_7 = """def main(argss):\n\tpass"""
test_case_8 = """def foo(baz):\n\tpass"""


@pytest.mark.parametrize(
    "fragment",
    [test_case_1, test_case_2, test_case_3, test_case_4],
)
def test_incomplete_def_does_not_match(fragment):
    assert CREATE_FUNCTION_PATTERN.search(fragment) is None


@pytest.mark.parametrize(
    "fragment",
    [test_case_5, test_case_6, test_case_7, test_case_8],
)
def test_completed_stub_matches(fragment):
    assert CREATE_FUNCTION_PATTERN.search(fragment) is not None
