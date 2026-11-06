# Changelog

## Unreleased

- `recan/default_approved_pastes.txt` holds pastes that are always approved, with or without `--approved-pastes` (to start: `if __name__ == '__main__':` with `main()`, in both quote styles). Entries are separated by a line containing only `---`, and each entry is its own source. They get the same loose matching as any approved file, so a student pasting the block instead of using PyCharm's template isn't flagged.

## 0.2.27

- Approved pastes are matched loosely as a second chance: line markers (`#`, Markdown headings, list numbers and bullets), bold and code marks and typographic quotes are ignored, and line wraps and runs of whitespace don't matter. A paste of instructions copied from the rendered page now matches the Markdown source. Code is not made whitespace-insensitive (`x=1` still differs from `x = 1`).
- `--approved-pastes` accepts more than one file (e.g. the instructions and the expected test output). Each file is its own source: a paste must fall within one file to be approved.
- Selecting text and typing a quote or bracket (the IDE wraps the selection in one edit) is an IDE action, not a paste. Only an edit whose new text is exactly the old text plus a matching pair counts.
- A paste of earlier (internal) or approved text stays internal/approved when another edit in the same burst changes the pasted text afterwards (for example a space is added or a character deleted inside it). Previously only the final, edited text was compared, and that text never existed anywhere in the document.

## 0.2.26

- Recognize initial document snapshots after setting-change or other metadata events, avoiding false recovery, incomplete-analysis, and starter-code mismatch warnings.

## 0.2.25

- Classify empty and whitespace-only pastes as approved.

## 0.2.24

- Handle IDE auto-indented pastes; this addresses false-positive unapproved pastes where the paste site has different indentation from the copy site.

## 0.2.22

- Classify and count generated edits after applying recorded IDE fixups.

## 0.2.21

- Ignore unchanged recorder snapshots so they are not classified as paste events.

## 0.2.20

- Major refactor in underlying data model.
- Removed `num_initial_chars` statistic.
- Misc formatting improvements to `summary` and `view`
- Gracefully handle wall-clock jitter in recording timestamps


## 0.2.19

- Support an empty list to `--starter-code` so expressions like `--starter-code code/*.py` don't fail when there are no files matched by the glob. 

## 0.2.18

- Add `--starter-code <1 or more files>` to all commands. Recordings are expected to start with the corresponding content; if a recording does not, it is flagged.
- Starter files are independent approved-paste sources.
- removed `--initial-char-limit`; use `--start-code` to track initial content discrepancies.

## 0.2.17

- Flag excessive initial content with `--initial-char-limit <n>`
- Minor adjustments to `view` UI

## 0.2.15

### Changed

- Changed the `view --auto-open` option to default to `false`. Pass `--auto-open` to open the generated HTML automatically.
