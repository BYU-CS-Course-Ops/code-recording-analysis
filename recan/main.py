import logging
import webbrowser
from pathlib import Path
from argparse import ArgumentParser, Namespace

from .formaters import render_json, render_markdown
from .viewer import write_player_html
from .stats import generate_stat_csvs
from .session import load_sessions
from .utils import is_gradescope_export, generate_submission_student_map, generate_problem_set


def add_common(sp: ArgumentParser) -> None:
    """
    Add common arguments for both `summary`, `view`, and `stats` subcommands.

    Arguments:
        - `--exclude .ext1 .ext2 ...`: List of file extensions to exclude (e.g. ".html" ".md").
        - `--approved-pastes approved.txt`: Path to a file containing all approved fragments.
    """
    sp.add_argument("--exclude", nargs="*", default=[], help="List of file extensions to exclude (e.g. .html .md).")
    sp.add_argument("--approved-pastes", type=Path, help="Path to a file containing all approved fragments. E.g. given blocks of code")


def _handle_summary(args: Namespace) -> None:
    sessions = load_sessions(args.recording_files, args.approved_pastes, args.exclude)

    if args.json:
        content = render_json(sessions)
    else:
        content = render_markdown(sessions)

    if args.output:
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content)


def _parse_summary(subparsers):
    """
    Generate a summary of a recording, either as human-readable Markdown or structured JSON.

    Usage: `recan summary
                <recording_file(s)>                                          - Required
                [--exclude .ext1 .ext2 ...]                                  - Optional
                [--approved-pastes approved.txt]                             - Optional
                [--output summary.md]                                        - Optional
                [--json]`                                                    - Optional
    """
    summary = subparsers.add_parser("summary", help="Analyze a recording and print or write a JSON/Markdown summary.")

    summary.add_argument("recording_files", type=Path, nargs="+", help="One or more recording files or glob patterns (JSONL or gzipped JSONL).")

    add_common(summary)

    summary.add_argument("--output", type=Path, help="Path to write the summary (defaults to stdout).")
    summary.add_argument("--json", action="store_true", help="Output as JSON instead of human-readable Markdown.")

    summary.set_defaults(func=_handle_summary)


def _handle_view(args: Namespace) -> None:
    sessions = load_sessions(args.recording_files, args.approved_pastes, args.exclude)
    session = sessions[0]

    html_path = write_player_html(session, args.recording_files[0])

    if args.auto_open:
        webbrowser.open(html_path.resolve().as_uri())


def _parse_view(subparsers):
    """
    Generate a self-contained HTML player for viewing a recording.

    Usage: `recan view
                <recording_file(s)>                                          - Required
                [--exclude .ext1 .ext2 ...]                                  - Optional
                [--approved-pastes approved.txt]                             - Optional
                [--auto-open]`                                               - Optional (defaults to true)
    """
    view = subparsers.add_parser("view", help="Generate a self-contained HTML player for a recording.")

    view.add_argument("recording_files", type=Path, nargs="+", help="One or more recording files or glob patterns (JSONL or gzipped JSONL).")

    add_common(view)

    view.add_argument("--auto-open", action="store_true", default=True, help="Open the generated HTML in the default web browser (default: true).")

    view.set_defaults(func=_handle_view)


def _handle_stats(args: Namespace) -> None:
    raise NotImplementedError("The `stats` command is not yet implemented. This will be added in a future release.")

    if not is_gradescope_export(args.folder):
        logging.error("Error: The specified folder does not appear to be a Gradescope export. Please check the folder structure and try again.")
        return

    submission_student_map = generate_submission_student_map(args.folder)

    problems = {}
    if args.problems:
        problems = generate_problem_set(args.problems)

    generate_stat_csvs(Path(args.folder), Path(args.output), problems, args.exclude, args.approved_pastes, submission_student_map)


def _parse_stats(subparsers):
    """
    Generate a CSV file of problems in a folder of recordings, assumes Gradescope export structure.

    Usage: `recan stats
                <folder>                            - Required
                <output>                            - Required
                [--exclude .ext1 .ext2 ...]         - Optional
                [--approved-pastes approved.txt]    - Optional
                [--problems problems.yaml]          - Optional (defaults to all)
    """
    stats = subparsers.add_parser("stats", help="Generate CSV files of problems in a folder of recordings")

    stats.add_argument("folder", type=Path, help="Path to a folder of recordings to analyze (Expects to be a Gradescope export).")
    stats.add_argument("output", type=Path, help="Path to write the generated CSV file")

    add_common(stats)

    stats.add_argument("--problems", help="yaml file specifying which problems to include, e.g. by name. If omitted, includes all problems in the folder.")

    stats.set_defaults(func=_handle_stats)


def entry() -> None:
    """
    Entry point for `recan`.

    Usage: `recan <command> [options]`

    Commands:
        - `summary`: Analyze a recording and print or write a JSON/Markdown summary.
        - `view`: Generate a self-contained HTML player for a recording.
        - `stats`: Generate CSV files of problems in a folder of recordings.
    """
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    parser = ArgumentParser(description="Analyze IDE recording files.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    _parse_summary(subparsers)
    _parse_view(subparsers)
    _parse_stats(subparsers)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    entry()
