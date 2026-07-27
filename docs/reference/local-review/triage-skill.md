<!-- pyml disable md003,md022,md023,md041 -->

> **Non-normative reference capture.** The installed Codex skill outside this
> repository is authoritative. This copy records the workflow used during
> specification development and is not an executable repository instruction.

**Captured metadata:** Name `local-review-triage`; short description "Walk
through local-review findings"; no argument hint. The installed skill's full
description identifies this as interactive, one-finding-at-a-time triage that
automatically resolves findings with a single correct answer.

# Local Review Triage

Walk through each finding from a `local-review` run, presenting them as an
ordered task list and guiding the developer through each one sequentially.

## Prerequisites

This skill expects a `local-review` verdict to already be present in the
conversation.

If no review findings are in the conversation, look for the most recent pika
finalize output before giving up:

```bash
LATEST=$(ls -dt "${PIKA_SESSION_ROOT}"/*/finalize-output.json 2>/dev/null | head -1)
```

If that file exists, read it and use it as the finding source (bucket mapping
below). If neither source is available, tell the user to run the `local-review`
skill first and stop.

`finalize-output.json` bucket mapping:

| JSON path | Bucket |
| --- | --- |
| `.findings.agreed[]` | Agreed |
| `.findings.by_source["v3-reviewer"][]` | Claude-only |
| `.findings.by_source["codex-reviewer"][]` | Codex-only |
| `.findings.mechanical[]` | Mechanical (`go vet`) |
| `.findings.description[]` | Description-level |

## Environment notes

- Codex has no `Read` tool — read files with plain shell (`cat`, `sed -n`,
  `jq`).
- Stage 3 auto-fix and step 6 both edit repository source, so this skill needs
  write access to the workspace. `PIKA_SESSION_ROOT` above denotes the redacted
  machine-local session location; fallback discovery needs read access to it.

## Instructions

### 1. Extract findings

Parse all validated findings from the most recent `local-review` output in the
conversation (or from the `finalize-output.json` fallback). Collect every
finding regardless of bucket.

### 2. Build ordered task list

Order findings by priority:

1. **Agreed** findings first (found by both reviewers)
2. **Claude-only** findings second
3. **Codex-only** findings third

Any Mechanical or Description-level findings follow, in that order. Within each
group, order by severity: CRITICAL > HIGH > MEDIUM.

### 3. Verify, auto-skip, and auto-fix

Before presenting any tasks to the user, run every finding through a
three-stage pipeline. This eliminates false positives and resolves anything with
a single correct answer, so the user only sees findings that genuinely need
their judgment.

#### Stage 1 — Verify each finding

For every finding, read the cited file and surrounding context. Confirm all of
the following:

1. The issue **still exists** in the current code (not already fixed).
2. The issue was **introduced or modified by the current diff** (not
   pre-existing).
3. The claim is **accurate** — check callers, types, tests, and neighboring code
   to rule out false positives.

Mark failed findings as **auto-skipped** with a reason matching the failed
check:

- Check #1 fails → reason: `already-fixed`
- Check #2 fails → reason: `pre-existing`
- Check #3 fails → reason: `false-positive`

#### Stage 2 — Auto-skip

After verification, auto-skip remaining findings where the correct action is
clearly "do nothing":

- **Already handled** — the concern is addressed elsewhere in the diff or
  codebase (distinct from `already-fixed` above — this covers cases where a
  different part of the diff intentionally addresses the concern)
- **Negligible impact** — duplicate of another finding in this batch, pure
  formatting/whitespace already enforced by a project autoformatter, or a
  finding the reviewer itself flagged as low-confidence

Record each auto-skipped finding with a one-line reason.

#### Stage 3 — Auto-fix

Identify verified findings that have a **single correct fix** — no design
judgment, no trade-offs between valid approaches. The fix can span multiple
files (e.g., adding a test file) as long as there is only one right answer.

Examples of auto-fixable findings:

- Missing `nil` / bounds / error checks that would panic or silently corrupt
- Unused imports, variables, or dead code introduced in the current diff
- Typos in identifiers, strings, or comments
- Missing `defer` for cleanup (close, unlock, cancel) when the intent is obvious
- Off-by-one errors with a single correct resolution
- Incorrect format-string verbs or argument counts
- Missing tests for newly introduced functions when:
  - A test for a sibling function exists in the same file or package
  - The testing pattern is established (table-driven, individual functions,
    subtests, etc.)
  - No new test helpers, mocks, fixtures, or dependencies are required
  - The new test follows the established pattern with different
    inputs/assertions

  Bias toward auto-fix here. When the sibling pattern is clear, generate the
  test. Reserve user interaction for cases where no sibling test exists,
  multiple genuinely different testing strategies are viable, or new test
  infrastructure would be needed.
- Missing error-return checks where the calling convention is established by
  surrounding code

**Do NOT auto-fix** findings that:

- Involve choosing between multiple valid approaches (an established testing
  pattern in the same file or package is the answer, not a choice among
  approaches)
- Change public API surface or observable behavior
- Require new dependencies or architectural decisions
- Touch unrelated production code (adding tests, docs, or support files for the
  changed code is fine)

For each auto-fixable finding:

1. Read the relevant file and confirm the issue still exists.
2. Implement the fix.
3. Record the finding as **auto-fixed**.

#### Pipeline summary

After all three stages complete, print a single summary. Omit any section with
zero entries.

```text
## Auto-resolved {resolved_count} finding(s)

### Auto-fixed ({fixed_count})

| # | Category | Severity | Bucket | File | Summary |
|---|----------|----------|--------|------|---------|
| 1 | ...      | ...      | ...    | ...  | ...     |

### Auto-skipped ({skipped_count})

| # | Category | Severity | Bucket | File | Summary | Reason |
|---|----------|----------|--------|------|---------|--------|
| 1 | ...      | ...      | ...    | ...  | ...     | ...    |

Remaining findings require your input — see below.
```

Remove auto-fixed and auto-skipped findings from the task list before assigning
sequential task numbers.

### 4. Present overview

Print a summary table:

```text
## Review Findings: {N} tasks

| # | Category | Severity | Bucket | File | Summary |
|---|----------|----------|--------|------|---------|
| 1 | ...      | ...      | ...    | ...  | ...     |
```

Then begin walking through tasks one at a time.

### 5. Walk through each task

For each task, print the following, then **end your turn and wait for the
user's reply** before moving to the next task:

```text
---
## Task {X} of {Y}: {Short finding title}

**Category:** {category}
**Severity:** {severity}
**Bucket:** {Agreed / Claude-only / Codex-only}
**File:** {file_path}:{line_range}

### Finding

{Full description of the issue, including the verbatim quoted code from the review.}

### Context

{Read the cited file around the relevant lines. Provide additional context about why this code exists, what it interacts with, and any constraints that affect the decision. Reference specific callers, tests, or related code paths where relevant.}

### Recommendation

{Your recommended course of action as the developer who would implement the fix.}

**When a single fix exists:** Describe the fix inline, then proceed to "Decision needed".

**When multiple options exist:** Present each option as its own section with a description paragraph followed by a pros/cons table. Then state your recommended option with a brief justification. Format:

#### Option A: {Short title}

{1-2 sentence description of what this option does.}

| Pro | Con |
|-----|-----|
| ... | ... |
| ... | ... |

#### Option B: {Short title}

{1-2 sentence description of what this option does.}

| Pro | Con |
|-----|-----|
| ... | ... |
| ... | ... |

**My recommendation: Option {X}.** {Brief justification for why this option is preferred.}

### Decision needed

**When multiple options exist:**

- **A** — {short title} *(recommended)*
- **B** — {short title}
- **(S)kip** — leave as-is
- Or type a response

Mark the recommended option with *(recommended)*. Only one option should be marked.

**When a single fix exists:**

- **(Y)es** — implement the fix *(recommended)*
- **(S)kip** — leave as-is
- Or type a response
```

**Print the task block as ordinary text.** Do NOT use any structured
question/option-picker tool, plan-mode prompt, or approval dialog to collect the
decision — the user answers in chat.

**Do NOT batch tasks.** One task per turn, then stop. Do not continue to the
next task on your own initiative.

**Accept single-character responses:** `a`, `b`, `c`, etc. (case-insensitive) to
implement that option, `y` to implement a single-option fix, `s` to skip. Any
other text is treated as a free-form response (question, alternative proposal,
etc.) — answer it, then re-present the decision prompt when ready.

### 6. Act on the user's decision

- **Option letter / Y**: Implement the change, run tests if applicable, then
  present the next task.
- **S**: Acknowledge and move to the next task.
- **Free-form text**: Answer questions, provide more detail, and re-present the
  decision prompt when ready.

### 7. Summary

After all tasks have been addressed, print a summary:

```text
## Review Response Complete

- **Auto-fixed:** {count}
- **Auto-skipped:** {count}
- **Fixed (interactive):** {count}
- **Skipped (interactive):** {count}
- **Total:** {count}

{List each finding with its disposition: auto-fixed / auto-skipped (reason) / fixed / skipped}
```

If any fixes were made, remind the user to run the `local-review` skill again
before committing.
