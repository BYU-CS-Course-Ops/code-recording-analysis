# Executor Handoff: `recan` Package Refactor

> **Read this top-to-bottom before doing anything.** This document is self-contained — it tells a fresh Claude Code session everything needed to drive the refactor to completion using subagent-driven development. It assumes zero prior conversation context.

---

## Your role

You are the **orchestrator**. You do not implement code yourself. You:

1. Read the plan and spec.
2. Dispatch one fresh subagent per task using the templates in this document.
3. Run a two-stage review on each task (spec-compliance reviewer, then code-quality reviewer).
4. Loop the implementer-fix → reviewer-recheck cycle until each task is approved.
5. Move to the next task. Don't pause to check in with the human between tasks.
6. After all tasks are approved, dispatch a final whole-implementation reviewer.
7. Hand off to `superpowers:finishing-a-development-branch`.

You are running on top of `superpowers:subagent-driven-development`. Follow that skill's red-flag list — in particular: never start work on `main` without explicit consent, never skip a review stage, never dispatch implementer subagents in parallel.

## Project context

- **Repo:** `/Users/robbykapua/Documents/github/beanlab-dev/code-recording-analysis` (Python 3.13+, Poetry, single package `recan`).
- **Current branch:** `main`. **Before dispatching Task 1, ask the human whether to switch to a feature branch or continue on `main`.** This is the one place you should pause.
- **Spec:** `docs/superpowers/specs/2026-05-08-recan-package-refactor-design.md`
- **Plan:** `docs/superpowers/plans/2026-05-08-recan-package-refactor.md` — eight tasks, full code in every step.
- **Verification model:** No new unit tests. Each task verifies via the CLI on a baseline recording (captured in Task 0) and diffs outputs. Expected differences are called out per task.

## Quick re-orientation (read the spec + plan summary)

The package currently has:
- `recan/main.py` — ~530 lines mixing CLI, walker, formatters, HTML rendering, helpers.
- Empty/stub files: `recan/utils.py`, `recan/structure.py` (partial), `recan/formaters.py`, `recan/viewer.py`, `recan/timeline.md.jinja`.
- `recan/analyze_assignment.py` — has a broken `from main import` (should be `from recan...`).

The refactor:
- One `Session` walker in `recan/session.py` collapses two overlapping functions (`analyze_inputs` and `_build_playback_bundle`) into one pass.
- File-type filtering moves to a new `utils.load_recording`.
- Pure helpers move to `recan/utils.py`.
- Output rendering moves to `recan/formaters.py` (Markdown via Jinja) and `recan/viewer.py` (HTML player).
- `main.py` shrinks to CLI parsing + ~10-line orchestration.

**Two intentional behavior changes:**
1. Default text output upgrades from the old `print_timeline` string format to a Markdown summary.
2. Paste-then-delete retroactive cancellation is **dropped**. Delete-then-paste move detection is **kept**.

Read the full spec and plan before dispatching the first subagent. The plan has the canonical task definitions; this document only tells you how to run them.

---

## Per-task workflow

For each task in the plan (Tasks 0 through 8):

### Step 1: Dispatch the implementer

Use the **Implementer Dispatch Template** below. Copy the full task text from the plan into the prompt — do **not** point the subagent at the plan file. The subagent must work from a self-contained brief.

Pick the model:
- **Task 0** (capture baselines, no code edits): `haiku` — purely mechanical shell commands.
- **Task 2** (TypedDicts only): `haiku` — copy-paste plus a single import check.
- **Task 1, 4, 5, 6, 7, 8** (single-module edits with clear specs): `sonnet`.
- **Task 3** (the unified walker — the heart of the refactor, multi-file integration): `sonnet`.

If a subagent returns `BLOCKED` or `NEEDS_CONTEXT`, follow the rules in `superpowers:subagent-driven-development`: provide context and re-dispatch on the same model, or escalate to a more capable model if it's a reasoning gap.

### Step 2: Spec-compliance review

After the implementer reports `DONE` or `DONE_WITH_CONCERNS`, dispatch a fresh subagent using the **Spec-Compliance Reviewer Template**. The reviewer's job is to confirm the implemented code matches the *task* (not the broader spec — the task is the contract).

If the reviewer reports issues, dispatch the **same** implementer subagent again with the issues (use `Agent` `SendMessage` if available, otherwise dispatch fresh with the original task text + the reviewer's findings). Loop until spec-compliant.

### Step 3: Code-quality review

Only after spec compliance is ✅, dispatch a fresh subagent using the **Code-Quality Reviewer Template**. Their job is to look at what was written and flag real problems (not nits): correctness bugs, security issues, dead code, leaky abstractions, surprising behavior.

Loop the implementer-fix → reviewer-recheck cycle until approved.

### Step 4: Mark task complete and move on

Mark the task complete in your TodoWrite list and dispatch the next task's implementer. Do **not** pause to check in with the human.

### Step 5 (after the last task): Whole-implementation review

After Task 8 is approved, dispatch one more reviewer using the **Final Reviewer Template** to look at the full diff against `main` (or against the pre-refactor commit). Their job is to catch anything the per-task reviewers missed because they only saw one task at a time.

Then invoke `superpowers:finishing-a-development-branch` to figure out merge / PR / cleanup.

---

## Task list at a glance

| # | Task                                          | Files touched                                     | Model  |
| - | --------------------------------------------- | ------------------------------------------------- | ------ |
| 0 | Capture pre-refactor baselines                | (no repo edits — captures `/tmp/recan-baseline/`) | haiku  |
| 1 | Move pure helpers + `load_recording` to `utils.py` | `recan/utils.py`, `recan/main.py`            | sonnet |
| 2 | Fill in `Session` types in `structure.py`     | `recan/structure.py`                              | haiku  |
| 3 | Create `session.py` with the unified walker   | `recan/session.py`, `recan/main.py`               | sonnet |
| 4 | Move text rendering to `formaters.py` + `timeline.md.jinja` | `recan/formaters.py`, `recan/timeline.md.jinja`, `recan/main.py` | sonnet |
| 5 | Move HTML player rendering to `viewer.py`     | `recan/viewer.py`, `recan/main.py`                | sonnet |
| 6 | Slim `main.py` to CLI + orchestration         | `recan/main.py`                                   | sonnet |
| 7 | Update `analyze_assignment.py`                | `recan/analyze_assignment.py`                     | sonnet |
| 8 | Final cleanup and verification sweep          | (no edits — verification only)                    | haiku  |

---

## Implementer Dispatch Template

```
You are implementing one task from a Python package refactor. Stay strictly within the task — do not refactor unrelated code, do not add features, do not rename things outside the diff this task describes.

## Repo context

- Working directory: /Users/robbykapua/Documents/github/beanlab-dev/code-recording-analysis
- Python 3.13+ via Poetry. Run things with `poetry run ...`.
- Single package: `recan/`. Sample recordings live under `samples/`.
- The CLI entry point is `recan` (defined in `pyproject.toml`, points at `recan.main:entry`).
- Two behavior changes are *intentional* in this overall refactor and are flagged in the task that introduces them: (a) default text output becomes Markdown, (b) the paste-then-delete retroactive cancellation is dropped. Do not preserve those behaviors.

## How verification works in this refactor

This refactor adds **no new unit tests**. Each task has explicit verification steps that run the CLI on a sample recording and diff against baselines captured in `/tmp/recan-baseline/`. Follow them exactly. Expected diffs (e.g. burst-count differences from dropping paste-then-delete) are called out in the task — confirm only those differences appear, then proceed.

## Your task

<<INSERT FULL TASK TEXT FROM THE PLAN HERE — every step, every code block, every verification command. Do not abbreviate. Do not link to the plan file.>>

## What to do

1. Read the task in full.
2. If anything is unclear or seems wrong, **ask before implementing**. Return status `NEEDS_CONTEXT` with your question. Do not guess.
3. Implement each step in order.
4. Run every verification command listed in the task. Compare output to what the task says to expect.
5. Self-review your diff before reporting done — check for: copied-but-not-updated identifiers, dead imports, references to deleted symbols, off-by-one in line ranges.
6. Commit using the exact message in the task's Commit step (or skip committing if the task says "no commit").

## Reporting back

End your response with one of these statuses on its own line:

- `STATUS: DONE` — task complete, all verification passed, committed.
- `STATUS: DONE_WITH_CONCERNS` — completed but with caveats; list them.
- `STATUS: NEEDS_CONTEXT` — you need information before continuing; list specific questions.
- `STATUS: BLOCKED` — you cannot complete the task; explain why.

Above the status line, include a short summary: what you changed, what verification you ran, what the verification showed, and the commit SHA if you committed.
```

## Spec-Compliance Reviewer Template

```
You are reviewing whether one task of a Python refactor was implemented exactly as specified. You are NOT doing a general code review — that's a separate stage. Your only job: did the implementer do what the task said, no more and no less?

## Repo context

- Working directory: /Users/robbykapua/Documents/github/beanlab-dev/code-recording-analysis
- The implementer has already committed. The most recent commit (or two) on the current branch is theirs.

## The task they were given

<<INSERT FULL TASK TEXT FROM THE PLAN HERE>>

## What to check

1. **Files** listed in the task header — were exactly those files created/modified, and only those?
2. **Steps** — was each step executed? Look at the diff (`git show HEAD` or `git log -p -1`) and walk through it against the steps.
3. **Code blocks** — when a step shows code to write, does the committed code match? Minor formatting differences are fine; structural / behavioral differences are not.
4. **Commit message** — does it match the task's commit step?
5. **Scope creep** — did they touch files outside the task header? Did they "improve" things that weren't asked for?
6. **Skipped work** — did they skip a verification step, a step that says "delete X", or one of the file edits in a multi-step task?

## Out of scope for you

- "Is this code high quality?" — not your job. The next reviewer handles that.
- "Could this be more elegant?" — not your job.
- "Should the design be different?" — out of scope; the design is locked.

## Reporting back

End with one status line:

- `STATUS: SPEC_COMPLIANT` — implementation matches the task; nothing missing, nothing extra.
- `STATUS: SPEC_DEVIATIONS` — list each deviation as a bullet: what the task said vs. what was committed, and which (missing, extra, or wrong).

Be specific. Quote the task and the commit. Don't say "doesn't match the spec" — say "Step 4 says delete _format_entry; the function is still in main.py at lines 462–471."
```

## Code-Quality Reviewer Template

```
You are doing a focused code-quality review of one task's commit(s) in a Python refactor. The implementation has already passed spec-compliance review, so the *what* is correct — your job is to look for problems with the *how*.

## Repo context

- Working directory: /Users/robbykapua/Documents/github/beanlab-dev/code-recording-analysis
- Most recent commit (or two) on this branch is the implementer's. Diff with `git show HEAD` or `git diff HEAD~1`.

## What to flag

Only call out things that are real problems. Skip nits.

- **Bugs:** off-by-one, wrong operator, missing edge case the task didn't mention, swallowed exception, wrong path resolution.
- **Correctness gaps:** a function whose behavior doesn't match its docstring, a TypedDict shape that disagrees with what callers read.
- **Dead code:** imports, constants, or functions that are no longer reachable after this task's deletions.
- **Leaky abstractions:** module A reaching into module B's privates; cycles in the dependency graph (`main → utils, session, formaters, viewer; session → utils, structure; formaters → utils, structure; viewer → utils, structure; structure → stdlib; utils → stdlib`).
- **Surprises:** behavior that future maintainers will trip on (e.g. a function that mutates an input, a regex that's wrong on edge cases, a Jinja template that breaks on empty input).

## What NOT to flag

- "I'd name it differently" — naming was decided in the spec.
- "This could be a list comprehension" — fine either way.
- "Add a docstring" — only if its absence creates a real ambiguity.
- "Could be tested" — this refactor has no unit tests by design.
- Style / formatting nits.

## Reporting back

End with one status line:

- `STATUS: APPROVED` — no real issues found.
- `STATUS: ISSUES_FOUND` — list each issue with file:line, what's wrong, and what to change.
```

## Final Reviewer Template (after Task 8)

```
You are doing a final review of an entire Python package refactor. All eight tasks have already passed per-task review. Your job: look at the *cumulative* diff and catch anything the per-task reviewers couldn't see because they only saw one task at a time.

## Repo context

- Working directory: /Users/robbykapua/Documents/github/beanlab-dev/code-recording-analysis
- The refactor's first commit is the one *before* commit `<<INSERT THE PRE-REFACTOR SHA HERE>>`. Diff with: `git diff <<PRE_REFACTOR_SHA>> HEAD -- recan/` and `git diff <<PRE_REFACTOR_SHA>> HEAD -- docs/`.

## The shape of the refactor

Spec: `docs/superpowers/specs/2026-05-08-recan-package-refactor-design.md`
Plan: `docs/superpowers/plans/2026-05-08-recan-package-refactor.md`

Goal: split `recan/main.py` into `utils.py`, `structure.py`, `session.py`, `formaters.py`, `viewer.py` (+ `timeline.md.jinja`), with one unified `Session` walker and main.py reduced to CLI + ~10-line orchestration.

Two intentional behavior changes:
- Default text output upgrades to Markdown via Jinja.
- Paste-then-delete retroactive cancellation is dropped.

## What to check

- **Cross-module consistency:** TypedDict field names actually match what every reader uses. Function signatures match call sites.
- **Dead code across the diff:** imports that became unused after the moves. Constants that exist in two places. Helpers that nothing imports.
- **Public surface:** what's exported from each module, what's a `_private` underscore name. Are imports in `main.py` clean?
- **Verification reality check:** run all three CLI modes against the sample recording and confirm they work. Run `analyze_assignment.py` against a sample folder and confirm CSVs are produced.
- **Spec coverage gaps:** read the spec end-to-end. Is anything it specified actually missing?

## Reporting back

End with one status line:

- `STATUS: READY_TO_MERGE` — refactor is complete and clean.
- `STATUS: ISSUES_FOUND` — list each issue with file:line, what's wrong, and a recommended fix.
```

---

## After the last review

Once the final reviewer reports `READY_TO_MERGE`:

1. Print a short summary of all eight tasks (one line each: subject + commit SHA).
2. Invoke `superpowers:finishing-a-development-branch` to determine the merge/PR/cleanup path.

---

## If you're a fresh Claude Code session reading this cold

Welcome. Quick start:

1. `cd /Users/robbykapua/Documents/github/beanlab-dev/code-recording-analysis`
2. Read this whole document.
3. Read `docs/superpowers/specs/2026-05-08-recan-package-refactor-design.md`.
4. Read `docs/superpowers/plans/2026-05-08-recan-package-refactor.md`.
5. Confirm the plan is on `main` and ask the human whether to branch.
6. Create a TodoWrite list with all 9 tasks (Tasks 0–8).
7. Begin the per-task workflow at Task 0.
