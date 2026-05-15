import logging
import webbrowser
from argparse import ArgumentParser, Namespace
from pathlib import Path

from recan.formaters import render_json, render_markdown
from recan.session import analyze_inputs
from recan.utils import load_recording, load_approved_fragments
from recan.viewer import write_player_html
from recan.stats import generate_stats
from recan.structure import Session


def _load_session(recording_file: Path, excluded_file_types: list[str],
                  approved_fragments_path: Path | None) -> Session:
    approved_fragments = load_approved_fragments(approved_fragments_path)
    inputs = load_recording(recording_file, excluded_file_types)
    return analyze_inputs(inputs, approved_fragments)


def cmd_summary(args: Namespace) -> None:
    session = _load_session(args.recording_file, args.exclude, args.approved_fragments)
    text = render_json(session) if args.json else render_markdown(session)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"Wrote summary to {args.output}")
    else:
        print(text)


def cmd_view(args: Namespace) -> None:
    session = _load_session(args.recording_file, args.exclude, args.approved_fragments)
    html_path = write_player_html(session, args.recording_file)
    print(f"Wrote playback HTML to {html_path}")
    if args.auto_open:
        webbrowser.open(html_path.resolve().as_uri())


def cmd_stats(args: Namespace) -> None:
    generate_stats(args.folder, args.output, args.include, args.exclude, args.approved_fragments)


def entry() -> None:
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    # Top-level parser: `recan <command> ...`
    parser = ArgumentParser(description="Analyze IDE recording files.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Args shared by every subcommand: the recording path and the exclude filter.
    def add_common(sp: ArgumentParser) -> None:
        sp.add_argument("--exclude", nargs="*", default=[],
                        help="List of file extensions to exclude (e.g. .html .md).")
        sp.add_argument("--approved-fragments", type=Path,
                        help="Path to a file containing all approved fragments. E.g. given blocks of code")

    # `recan summary` — analysis output as Markdown (default) or JSON, to stdout or a file.
    summary = subparsers.add_parser(
        "summary", help="Analyze a recording and print or write a JSON/Markdown summary."
    )
    # Positional arg for the recording file
    summary.add_argument("recording_file", type=Path,
                         help="Path to the recording file (JSONL or gzipped JSONL).")
    # Subcommand-specific args: output path and format (JSON vs Markdown).
    add_common(summary)
    summary.add_argument("--output", type=Path,
                         help="Path to write the summary (defaults to stdout).")
    summary.add_argument("--json", action="store_true",
                         help="Output as JSON instead of human-readable Markdown.")
    summary.set_defaults(func=cmd_summary)

    # `recan view` — emits a self-contained HTML player and (by default) opens it.
    view = subparsers.add_parser(
        "view", help="Generate a self-contained HTML player for a recording."
    )
    # Positional arg for the recording file
    view.add_argument("recording_file", type=Path,
                         help="Path to the recording file (JSONL or gzipped JSONL).")
    # Subcommand-specific args: whether to open the generated HTML in the default web browser.
    add_common(view)
    view.add_argument("--auto-open", action="store_true", default=True,
                      help="Open the generated HTML in the default web browser (default: true).")
    view.set_defaults(func=cmd_view)

    # `recan stats` — generates CSV files of problem-level stats for a folder of recordings (e.g. a Gradescope export).
    stats = subparsers.add_parser(
        "stats", help="Generate CSV files of problems in a folder of recordings"
    )
    stats.add_argument("folder", type=Path,
                       help="Path to a folder of recordings to analyze (Expects to be a Gradescope export).")
    stats.add_argument("output", type=Path,
                       help="Path to write the generated CSV file")
    # Subcommand-specific args: a filter to specify which recordings to include, e.g. by problem name.
    add_common(stats)
    stats.add_argument("--include",
                       help="A filter to specify which recordings to include, e.g. by problem name. Takes list of strings or json file with list of strings.")
    stats.set_defaults(func=cmd_stats)

    # Dispatch: each subparser attaches its handler via set_defaults(func=...).
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    entry()
