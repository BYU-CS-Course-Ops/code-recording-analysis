from argparse import ArgumentParser
from pathlib import Path

from recan.formaters import render_json, render_markdown
from recan.session import analyze_inputs
from recan.utils import load_recording
from recan.viewer import write_player_html


def main(
    recording_file: Path,
    excluded_file_types: list[str],
    output: Path | None = None,
    _json: bool = False,
    view: bool = False,
) -> None:
    inputs = load_recording(recording_file, excluded_file_types)
    session = analyze_inputs(inputs)

    text = render_json(session) if _json else render_markdown(session)
    if output:
        output.write_text(text, encoding="utf-8")
    else:
        print(text)

    if view:
        html_path = write_player_html(session, recording_file)
        print(f"Wrote playback HTML to {html_path}")


def entry() -> None:
    parser = ArgumentParser(description="Analyze IDE recording files.")
    parser.add_argument("recording_file", type=Path,
                        help="Path to the recording file (JSONL or gzipped JSONL).")
    parser.add_argument("--exclude", nargs="*", default=[],
                        help="List of file extensions to exclude (e.g. .html .md).")
    parser.add_argument("--output", type=Path,
                        help="Path to write the analysis results (defaults to stdout).")
    parser.add_argument("--json", action="store_true",
                        help="Output the analysis results as JSON instead of human-readable Markdown.")
    parser.add_argument("--view", action="store_true",
                        help="Generate a self-contained HTML player next to the recording.")

    args = parser.parse_args()
    main(args.recording_file, args.exclude, args.output, args.json, args.view)


if __name__ == "__main__":
    entry()
