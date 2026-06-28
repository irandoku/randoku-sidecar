# Randoku Sidecar Roadmap

This roadmap tracks the transition from a local `hermes-gpt`-derived MCP sidecar toward `randoku-sidecar`: a policy-first AI workspace sidecar for controlled engineering workflows.

## Phase 0 — Project hygiene

- [x] Confirm upstream license allows fork / modification / redistribution.
- [x] Add attribution document.
- [x] Add architecture document.
- [x] Rename package / README references after the backup policy is fixed.
- [ ] Preserve upstream MIT license and copyright notices.
- [x] Add changelog for Randoku-specific changes.

## Phase 1 — Policy cleanup

Goal: make shared policy decisions reusable by every capability.

- [x] Add `should_backup(path)`. (`_should_backup_file` in `operator_workspace.py`)
- [x] Detect git worktrees. (`_is_git_worktree`)
- [x] Skip `.bak` creation inside git repositories by default.
- [x] Keep `.bak` backups for non-git workspaces such as notes and downloads.
- [x] Include backup policy in tool responses and audit records.

## Phase 2 — Patch capability

Goal: make code changes reviewable and robust.

- [x] Add `hermes_workspace_apply_diff`. (`operator_workspace.py`)
- [x] Support strict single-file unified diffs.
- [x] Reject fuzzy matches, renames, deletions, binary diffs, and multi-file diffs in v0.1.
- [x] Dry-run should return the resulting preview diff.
- [x] Direct apply should use shared policy, backup policy, atomic write, and audit.

## Phase 3 — Test capability

Goal: allow AI-assisted verification without opening arbitrary shell access.

- [ ] Add intent-mode `hermes_workspace_run_test` parameters.
- [ ] Start with `suite="python_pytest"`.
- [ ] Validate target paths inside workdir.
- [ ] Keep legacy command allowlist during transition.
- [ ] Return pass / fail / timeout / blocked status.

## Phase 4 — Audit and review refinement

- [ ] Normalize tool response fields: `success`, `dry_run`, `changed`, `mode`, `rollback_hint`.
- [ ] Add concise summaries for failed tests and failed patch attempts.
- [ ] Add audit records for backup policy and capability mode.

## Phase 5 — Memory integration repair

Deferred until the core tooling is stable.

- [x] Initialize `MemoryStore` when calling Hermes memory internals. (`server.py`)
- [x] Implement or clarify memory search behavior. (`hermes_memory(action="search")`)
- [x] Keep memory write guarded by explicit policy. (OperatorPolicy: level `skills_config` + `apply_mode=direct` + per-call `dry_run`)

## Non-goals for early versions

- No unrestricted terminal by default.
- No automatic git commit or push.
- No deployment or service restart workflow.
- No credential or secret file access.
- No fuzzy patching that guesses user intent.
