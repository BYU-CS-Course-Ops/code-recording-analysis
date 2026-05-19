**Document:** `C:\Users\rache\OneDrive\Documents\cs110\final\analyze_logs.py`

**Recording Started:** 2026-04-14 21:25:30

## Executive Summary

| Metric            | Value                                                                    |
|-------------------|--------------------------------------------------------------------------|
| Total Time        | 01:13:32                                |
| Time Focused      | 01:13:32 |
| Time Unfocused    | 00:00                      |
| Focus Events      | 0                                       |
| IDE Actions       | 3                                          |
| Unapproved Pastes | 2                                    |
| Approved Pastes   | 0                                      |
| Edits             |                                                  |

## Timeline

- **2026-04-14 21:26:41** — Unapproved Paste (3 lines, 90 chars)

```python
def read_lines(filename):
    with open(filename) as file:
        return file.readlines()
```

- **2026-04-14 21:28:21** — Ide_action (2 lines, 25 chars)

```python
def make_dict():
    pass
```

- **2026-04-14 21:31:21** — Internal Paste (1 lines, 36 chars)

```python
        lines_dictionary[line[1]]= 0
```

- **2026-04-14 21:32:31** — Ide_action (2 lines, 40 chars)

```python
def find_max(lines_dictionary):
    pass
```

- **2026-04-14 21:35:56** — Ide_action (2 lines, 45 chars)

```python
def print_it(lines_dictionary, max):
    pass
```

- **2026-04-14 21:37:17** — Unapproved Paste (34 lines, 765 chars)

```python

def read_lines(filename):
    with open(filename) as file:
        return file.readlines()


def make_dict(lines):
    lines_dictionary = {}
    for line in lines.split("."):
        if line[1] not in lines_dictionary:
            lines_dictionary[line[1]] = 0
        lines_dictionary[line[1]] += 1
    return lines_dictionary


def find_max(lines_dictionary):
    max = 0
    for key in lines_dictionary:
        if lines_dictionary[key] > max:
            max = key
    return max


def print_it(lines_dictionary, max):
    print(f"Region {max} had {lines_dictionary[max]} connections.")


def main(input_file):
    lines = read_lines(input_file)
    lines_dictionary = make_dict(lines)
    max = find_max(lines_dictionary)
    print_it(lines_dictionary, max)


```

- **2026-04-14 22:36:35** — Internal Paste (1 lines, 34 chars)

```python
right_line = split_line.split(".")
```

- **2026-04-14 22:36:41** — Internal Paste (2 lines, 35 chars)

```python

        if split_line[1] not in li
```

- **2026-04-14 22:36:42** — Internal Paste (2 lines, 35 chars)

```python

        if split_line[1] not in li
```

