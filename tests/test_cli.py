import pytest
from argparse import ArgumentParser
from pathlib import Path

from recan.main import add_common, _parse_stats, _parse_summary, _parse_view


def parser():
    parser = ArgumentParser()
    add_common(parser)
    return parser


def subcommand_parser(parse_command):
    parser = ArgumentParser()
    parse_command(parser.add_subparsers())
    return parser


def test_starter_code_accepts_multiple_files():
    args = parser().parse_args([
        "--starter-code", "template.py", "helpers.py",
    ])
    assert args.starter_code == [Path("template.py"), Path("helpers.py")]


def test_starter_code_without_files_defaults_to_empty_list():
    args = parser().parse_args(["--starter-code"])
    assert args.starter_code == []


def test_starter_code_when_omitted_defaults_to_empty_list():
    args = parser().parse_args([])
    assert args.starter_code == []


@pytest.mark.parametrize(
    ("parse_command", "command_args"),
    [
        (_parse_summary, ["summary", "one.jsonl", "two.jsonl"]),
        (_parse_view, ["view", "one.jsonl", "two.jsonl"]),
        (_parse_stats, ["stats", "submissions", "out.csv"]),
    ],
)
def test_each_subcommand_accepts_multiple_starter_files(parse_command, command_args):
    args = subcommand_parser(parse_command).parse_args(
        command_args + ["--starter-code", "template.py", "helpers.py"]
    )
    assert args.starter_code == [Path("template.py"), Path("helpers.py")]


def test_initial_char_limit_is_rejected():
    with pytest.raises(SystemExit):
        parser().parse_args(["--initial-char-limit", "10"])
