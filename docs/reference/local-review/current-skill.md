<!-- pyml disable md003,md022,md023,md041 -->

> **Non-normative reference capture.** The installed Codex skill outside this
> repository is authoritative. This copy records the workflow used during
> specification development and contains machine-specific example paths; do not
> invoke it as repository documentation.

**Captured metadata:** Name `local-review`; short description "Peer-review
current branch changes"; optional argument hint `[focus area]`. The installed
skill's full description identifies this as a read-only dual-model review whose
remediation belongs to `local-review-triage`.

# Local Peer Review

**Scope: review-only.** Reviewers read only, and this orchestrator never edits
source — remediation is the `local-review-triage` skill's job. Writes under the
pika session directory (reviewer outputs, salvage results) are expected workflow
writes, not source edits.

## Reviewer roster

Exactly two reviewers, one per vendor:

| Reviewer | Launched by | Where it appears in the manifest |
| --- | --- | --- |
| Claude Code (`v3-reviewer`) | this skill, via `claude -p` | `manifest.agents[]` |
| Codex (`codex-reviewer`) | `pika review run`, detached | `manifest.go_launched_agents[]` |

`pika review run` launches the Codex reviewer itself as a hermetic,
watchdog-bounded background process, and `pika review finalize` waits on it and
folds in its structured findings. **Never launch a Codex reviewer yourself** —
doing so would double-review and corrupt the source counts that
cross-source agreement depends on.

Both reviewers write into the pika session directory; finalize is
vendor-agnostic and reads whatever is there.

## Absolute tool paths

Do not rely on `PATH`. Use these:

- pika: `/Users/jsquyres/git/pika/pika`
- claude: `/Users/jsquyres/.local/bin/claude`
- codex: `/opt/homebrew/bin/codex` (pika resolves this itself; recorded here for
  diagnostics only)

If any is missing, surface the error and stop.

## Preconditions

- pika writes its artifacts to `~/.pika/sessions/<id>/`, which is **outside the
  repo workspace**, and both reviewer subprocesses need network access. A
  `read-only` or default `workspace-write` sandbox will therefore break the run.
  Start the driving session with full access, or approve the escalation when
  prompted, or grant `~/.pika` in `sandbox_workspace_write.writable_roots`
  together with `sandbox_workspace_write.network_access = true`.
- Codex has no `Read` tool: read manifests and JSON with `cat` / `jq`.
- Codex has no `Task` tool: the Claude reviewer is a `claude -p` subprocess.

## Instructions

1. Determine the diff base:

   ```bash
   BASE=$(gh repo view --json defaultBranchRef -q '.defaultBranchRef.name')
   git fetch origin --quiet
   BASE_REF=$(git merge-base "origin/$BASE" HEAD)
   ```

2. Parse `$ARGUMENTS` for an optional focus area (free text; there are no mode
   flags).

3. Run:

   ```bash
   /Users/jsquyres/git/pika/pika review run --branch --base "$BASE_REF" --focus "<focus>"
   ```

   Output is one line:
   `manifest=<path> tier=N agents=N codex=N shards=N files=N`.
   `codex=1` means the Codex reviewer launched; `codex=0` means the `codex`
   binary was not usable and the review will degrade to Claude-only — say so in
   the final summary. Read the manifest JSON at `<path>` with `cat`/`jq`.

4. Launch every entry in `manifest.agents` — the Claude reviewers — as
   background `claude -p` processes, then wait. Each prompt file already
   contains the output path and the `REVIEW_COMPLETE` reply contract, so pass it
   verbatim with no wrapper text.

   ```bash
   MANIFEST=<path>
   SESSION=$(jq -r '.session_dir' "$MANIFEST")

   i=0
   while read -r agent; do
     PROMPT_PATH=$(printf '%s' "$agent" | jq -r '.prompt_path')
     /Users/jsquyres/.local/bin/claude -p "$(cat "$PROMPT_PATH")" \
       --model opus \
       --permission-mode acceptEdits \
       --add-dir "$SESSION" \
       > "$SESSION/claude-agent-$i.log" 2>&1 &
     i=$((i + 1))
   done < <(jq -c '.agents[]' "$MANIFEST")
   wait
   ```

   Then confirm each agent's `output_path` exists and its log ends with a
   `REVIEW_COMPLETE:` line. Do not read the reviewers' findings yourself —
   finalize parses them.

   Meanwhile the Codex reviewer is already running detached. Do nothing for it:
   step 5 blocks on it.

5. Run:

   ```bash
   /Users/jsquyres/git/pika/pika review finalize --manifest <path> --diff "$SESSION/review.diff"
   ```

   This waits for the Codex reviewer's run-status before returning. If stdout
   contains `"artifact_kind"`, read the full JSON from its `path` field;
   otherwise parse stdout as the finalize JSON.

   If the JSON carries `degradations`, the Codex reviewer did not complete
   cleanly (timeout, stall, workspace-access failure). That is a Claude-only
   review, not an approval — report it verbatim to the user.

6. If `verdict_mismatch` is non-empty: execute the finalize JSON's
   `repair_instructions`. They are written for a Claude subagent — run each one
   as `claude -p "<instruction text>" --permission-mode acceptEdits --add-dir
   "$SESSION"`. Then re-run step 5 ONCE. If the mismatch persists, surface the
   error and stop — do NOT treat it as approved.

7. If `salvage_queue` is non-empty: run one salvage pass. `finalize.salvage_model`
   and `finalize.salvage_effort` name a **Claude** model (default `sonnet`), so
   salvage runs through `claude -p`, never `codex exec`:

   ```bash
   /Users/jsquyres/.local/bin/claude -p "<salvage instructions>" \
     --model "$(jq -r '.salvage_model' "$SESSION/finalize-output.json")" \
     --permission-mode acceptEdits --add-dir "$SESSION"
   ```

   The salvage prompt must tell it to read each cited file ±30 lines, check
   quote-to-code match, and write `$SESSION/salvage-results.json` as
   `[{"index":N,"verdict":"YES"|"NO"}]`. Then run:

   ```bash
   /Users/jsquyres/git/pika/pika review salvage-merge \
     --finalize "$SESSION/finalize-output.json" \
     --results "$SESSION/salvage-results.json"
   ```

8. Present the `summary` to the user.

## Rules

- Exactly one Claude reviewer entry and one Codex reviewer per review (more only
  when pika shards a large diff). Never add reviewers of your own.
- Salvage checks quote-to-code match only — do not re-evaluate the finding.
- If any CLI step fails, surface the error and stop.
- Never report APPROVED when `degradations`, `agents_failed`, or
  `verdict_mismatch` is non-empty.
- If there are findings, mention the `local-review-triage` skill for triage.
