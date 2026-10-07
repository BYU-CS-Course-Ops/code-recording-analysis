# Changelog

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
