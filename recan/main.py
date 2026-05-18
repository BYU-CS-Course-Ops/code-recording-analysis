import logging
import webbrowser
from pathlib import Path
from argparse import ArgumentParser, Namespace

from .formaters import render_json, render_markdown
from .viewer import write_player_html
from .stats import generate_stat_csvs
from .utils import load_session, is_gradescope_export, generate_submission_student_map, generate_problem_set


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
    session = load_session(args.recording_file, args.additional_recordings, args.exclude, args.approved_pastes)

    if args.json:
        content = render_json(session)
    else:
        content = render_markdown(session)

    if args.output:
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content)


def _parse_summary(subparsers):
    """
    Generate a summary of a recording, either as human-readable Markdown or structured JSON.

    Usage: `recan summary
                <recording_file>                                             - Required
                [--exclude .ext1 .ext2 ...]                                  - Optional
                [--approved-pastes approved.txt]                             - Optional
                [--additional-recordings add1.jsonl.gz add2.jsonl.gz ...]    - Optional
                [--output summary.md]                                        - Optional
                [--json]`                                                    - Optional
    """
    summary = subparsers.add_parser("summary", help="Analyze a recording and print or write a JSON/Markdown summary.")

    summary.add_argument("recording_file", type=Path, help="Path to the recording file (JSONL or gzipped JSONL).")

    add_common(summary)

    summary.add_argument("--additional-recordings", nargs="*", type=Path, default=None, help="Additional .jsonl.gz recordings to include. Accepts a folder (all .jsonl.gz files inside), a list of files, or omit. The primary recording_file is skipped if present.")
    summary.add_argument("--output", type=Path, help="Path to write the summary (defaults to stdout).")
    summary.add_argument("--json", action="store_true", help="Output as JSON instead of human-readable Markdown.")

    summary.set_defaults(func=_handle_summary)


def _handle_view(args: Namespace) -> None:
    session = load_session(args.recording_file, args.additional_recordings, args.exclude, args.approved_pastes)

    html_path = write_player_html(session, args.recording_file)

    if args.auto_open:
        webbrowser.open(html_path.resolve().as_uri())


def _parse_view(subparsers):
    """
    Generate a self-contained HTML player for viewing a recording.

    Usage: `recan view
                <recording_file>                                             - Required
                [--exclude .ext1 .ext2 ...]                                  - Optional
                [--approved-pastes approved.txt]                             - Optional
                [--additional-recordings add1.jsonl.gz add2.jsonl.gz ...]    - Optional
                [--auto-open]`                                               - Optional (defaults to true)
    """
    view = subparsers.add_parser("view", help="Generate a self-contained HTML player for a recording.")

    view.add_argument("recording_file", type=Path, help="Path to the recording file (JSONL or gzipped JSONL).")

    add_common(view)

    view.add_argument("--additional-recordings", nargs="*", type=Path, default=None, help="Additional .jsonl.gz recordings to include. Accepts a folder (all .jsonl.gz files inside), a list of files, or omit. The primary recording_file is skipped if present.")
    view.add_argument("--auto-open", action="store_true", default=True, help="Open the generated HTML in the default web browser (default: true).")

    view.set_defaults(func=_handle_view)


def _handle_stats(args: Namespace) -> None:
    if not is_gradescope_export(args.folder):
        logging.error("Error: The specified folder does not appear to be a Gradescope export. Please check the folder structure and try again.")
        return

    submission_student_map = generate_submission_student_map(args.folder)

    problems = {}
    if args.problems:
        problems = generate_problem_set(args.problems)

    generate_stat_csvs(Path(args.folder), Path(args.output_path), problems, args.exlude, args.approved_pastes, submission_student_map)


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
