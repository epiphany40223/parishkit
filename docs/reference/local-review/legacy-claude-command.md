<!-- pyml disable md003,md022,md032,md041 -->

> **Historical legacy capture.** This predates the current local-review skill,
> intentionally contains obsolete pika flags and orchestration behavior, and is
> retained only as development provenance. Do not use it as instructions.

**Captured metadata:** Description "Dual-subagent AI-to-AI peer review of
current branch changes"; model `opus`.

# Local Peer Review

**Scope: review-only.** Subagents review read-only and the parent never edits source — remediation is `/pika:local-review-triage`'s job. Session-directory review artifacts (reviewer outputs, salvage results) are expected workflow writes, not source edits.

## Instructions

1. Determine diff base: `BASE=$(gh repo view --json defaultBranchRef -q '.defaultBranchRef.name') && git fetch origin --quiet && BASE_REF=$(git merge-base origin/$BASE HEAD)`

2. Parse `$ARGUMENTS` for mode flags (`--no-prefix`, `--ref-prefix`, `--prefer-tier-1`) and optional focus area.

3. Run `pika review run --branch --base $BASE_REF <mode-flags> --focus "<focus>"`. The output is a line `manifest=<path> tier=N agents=N files=N`. Read the manifest JSON at `<path>` with the `Read` tool.

4. Spawn ALL agents in a single message — one `Task` subagent per entry in `manifest.agents`. Branch on `manifest.tier` and per-agent scope: for tier 1/2, agents that have no `shard_files` read the shared prefix first — pick ONE tool from `manifest.read_strategy` for all agents (`ctx_read` → `ctx_read(path, mode='full', fresh=true)`, `read` → native `Read`, full content; do NOT let the agent choose); at tier 3, or for ANY agent that has `shard_files` (a shard reviewer already reads only its own per-shard diff per its prompt), do NOT inject the shared prefix. Give each subagent this prompt (substitute the paths): `[only when manifest.tier is 1/2 AND the agent has no shard_files: Read every shared-prefix file with that tool — read all of them: {manifest.shared_prefix_parts}. ]Read {agent.prompt_path} and execute its instructions exactly. Write your findings to {agent.output_path}. Reply ONLY with: REVIEW_COMPLETE: findings=N files_reviewed=M verdict=APPROVED|ISSUES_FOUND`. Codex (when the `codex` CLI is installed) runs automatically in the background — `pika review run` launches it detached and `pika review finalize` waits for it and folds in its findings; do NOT spawn a codex subagent.

5. Run `pika review finalize --manifest <path> --diff {manifest.session_dir}/review.diff`. If stdout contains `"artifact_kind"`, read the full JSON from its `path` field with `Read`; otherwise parse stdout as the finalize JSON.

6. If `verdict_mismatch` is non-empty: execute the finalize JSON's `repair_instructions`, then re-run step 5 ONCE. If the mismatch persists, surface the error and stop — do NOT treat as approved.

7. If `salvage_queue` is non-empty: spawn a subagent (model/effort from `finalize.salvage_model`/`salvage_effort`) that reads each cited file ±30 lines, checks quote-to-code match, and writes `{session}/salvage-results.json` (`[{"index":N,"verdict":"YES"|"NO"}]`). Then run `pika review salvage-merge --finalize {session}/finalize-output.json --results {session}/salvage-results.json`.

8. Present the `summary` to the user.

## Rules
- Do NOT read shared_prefix files yourself — only the subagents read them.
- Salvage checks quote-to-code match only — do not re-evaluate the finding.
- If any CLI step fails, surface the error and stop.
- If there are findings, mention `/pika:local-review-triage` for triage.
