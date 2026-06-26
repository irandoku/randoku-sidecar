# Audit Plan: workspace_run_test

## Purpose

Audit the existing `hermes_workspace_run_test` implementation against the proposed `workspace_run_test` capability contract before entering coding.

This audit is intentionally read-first and review-first. The goal is to identify behavioral gaps, safety gaps, compatibility risks, and a minimal implementation path. No source code changes should be made until this audit plan is reviewed and approved.

## Scope

This audit covers the current `workspace_run_test` behavior in `randoku-sidecar`.

Primary files:

- `server.py`
- `operator_workspace.py`
- `operator_policy.py`
- `docs/capabilities/workspace_apply_diff.md`
- planned: `docs/capabilities/workspace_run_test.md`

Related existing tools:

- `hermes_workspace_run_test`
- `hermes_workspace_apply_diff`
- `hermes_workspace_read`
- `hermes_git_status`
- `hermes_git_diff`
- `hermes_owner_run_command`

Out of scope for this audit:

- arbitrary terminal execution
- owner-mode command execution
- package installation
- service restart
- network policy enforcement
- changing MCP connector schema unless explicitly approved later

## Existing Implementation Summary

Current public wrapper:

```python
def hermes_workspace_run_test(command: str, workdir: str | None = None, timeout: int = 120, dry_run: bool = True) -> str:
    return op_workspace.hermes_workspace_run_test(
        command=command, workdir=workdir, timeout=timeout, dry_run=dry_run,
    )
```

Current core behavior in `operator_workspace.py`:

- requires operator level `workspace`
- requires non-empty `command`
- parses command with `shlex.split`
- checks argv against `_TEST_COMMAND_ALLOWLIST`
- rejects dangerous substrings through `_DANGEROUS_PATTERNS`
- checks `workdir` against `allowed_paths` when provided
- supports dry-run planning
- executes through `op.run_argv`
- relies on `op.run_argv` for `shell=False`, timeout clamp, output capture, and output truncation
- redacts stdout/stderr using `op.redact_output`
- writes audit records

Current shared subprocess helper in `operator_policy.py`:

- uses `subprocess.run(..., shell=False)`
- clamps timeout to 1..600 seconds
- captures stdout/stderr
- returns `(124, stdout, stderr)` on timeout
- returns `(127, "", stderr)` on file-not-found
- truncates stdout/stderr
- does not raise on test failure

## Contract Baseline

The intended contract says `workspace_run_test` may:

- run an allowlisted test or verification command
- run inside an allowed workspace directory
- capture stdout and stderr
- return exit code, status, and concise diagnostic output

It must not:

- execute arbitrary shell commands
- use `shell=True`
- run destructive commands
- run git commit, git push, or git index mutations
- access denied paths or secret-like files
- run outside allowed workspace paths
- silently ignore timeout or command rejection

The v0.1 target should support:

- legacy allowlisted command mode
- intent-based `python_pytest` suite mode
- target validation inside workdir

## Audit Questions

### 1. Capability boundary

Determine whether `workspace_run_test` is currently a constrained verification tool or still too close to a general command runner.

Questions:

- Does every accepted command clearly fit test, lint, type-check, compile, or read-only verification?
- Should `git status` and `git diff` remain in `workspace_run_test`, or should they be removed because dedicated `hermes_git_status` and `hermes_git_diff` already exist?
- Should the allowlist be structured as semantic suites instead of raw command prefixes?

### 2. Command parsing and shell safety

Verify:

- `shell=True` is never used.
- commands are parsed to argv before execution.
- shell metacharacters are blocked before execution.
- quoting behavior is predictable across macOS/Linux and Windows.
- dangerous patterns cannot bypass checks through quoting, casing, or argument boundaries.

Items to inspect:

- `_DANGEROUS_PATTERNS`
- `_is_allowed_test_command`
- `shlex.split(command, posix=(os.name != "nt"))`
- `op.run_argv`

### 3. Allowlist correctness

Current allowlist:

```python
_TEST_COMMAND_ALLOWLIST = (
    (("pytest",), 8),
    (("python", "-m", "pytest"), 8),
    (("npm", "test"), 4),
    (("npm", "run", "test"), 4),
    (("npm", "run", "lint"), 4),
    (("ruff", "check"), 4),
    (("mypy",), 4),
    (("git", "status"), 4),
    (("git", "diff"), 4),
)
```

Audit tasks:

- classify each allowlisted command as test / lint / type-check / read-only inspection / questionable
- decide whether `git status` and `git diff` belong here
- decide whether `python -m compileall` should be included
- decide whether `python -m pytest` should become the preferred Python suite
- decide whether direct `pytest` should remain allowed or be treated as legacy
- check whether `npm test` and `npm run test` can trigger arbitrary package scripts, and whether that is acceptable inside allowed workspaces
- check whether extra args should be restricted to safe target paths and known flags

### 4. Workdir policy

Current behavior:

- if `workdir` is provided and `allowed_paths` is set, it must be under an allowed path
- if `workdir` is omitted, execution may happen with process default cwd
- denied path policy is not explicitly applied to `workdir`

Audit tasks:

- require `workdir` for direct execution
- normalize `workdir` before use
- require `workdir` to be under allowed workspace paths
- reject denied / secret-like workdir paths
- decide whether dry-run may allow missing workdir or should also require it
- ensure returned plan includes normalized workdir summary

Recommended direction:

- v0.1 should require `workdir`
- omission should return `status="blocked"`
- workdir should be normalized and policy-checked before command validation result is returned

### 5. Target path validation

The proposed contract mentions target validation inside workdir.

Audit tasks:

- define `target` concept for suite mode
- decide whether legacy command mode should inspect extra argv for path-like targets
- ensure targets cannot escape workdir using `..`, symlinks, absolute paths, or shell tricks
- ensure targets do not point to denied paths or secret-like files

Recommended direction:

- Phase 1: keep schema unchanged and only audit legacy behavior
- Phase 2: introduce suite mode with explicit `suite` and `target`
- target validation should be mandatory for suite mode
- legacy command mode should remain conservative and possibly deprecated later

### 6. Status model

Current behavior:

- dry-run returns `success=true`, `dry_run=true`, `plan`
- direct execution returns `success = rc == 0`
- blocked commands return `success=false`, `error`
- timeout returns `returncode=124`, but no explicit `status="timeout"`

Contract requires clear status values:

- `dry_run`
- `pass`
- `fail`
- `timeout`
- `blocked`

Audit tasks:

- map current return shapes to the desired status model
- ensure blocked commands are distinguishable from failing tests
- ensure timeout is distinguishable from failing tests
- include `exit_code` / `returncode` consistently
- decide whether `success` should mean tool call success or test pass

Recommended direction:

- keep `success` for backward compatibility
- add `status`
- add `exit_code`
- blocked: `success=false`, `status="blocked"`, no command executed
- timeout: `success=false`, `status="timeout"`, `exit_code=124`
- fail: `success=false`, `status="fail"`, command executed
- pass: `success=true`, `status="pass"`, command executed
- dry-run: `success=true`, `status="dry_run"`, no command executed

### 7. Timeout behavior

Current behavior:

- dry-run plan displays timeout clamped to 1..600
- direct execution passes timeout to `op.run_argv`
- `op.run_argv` internally clamps timeout to 1..600
- timeout returns rc 124

Audit tasks:

- decide contract-level timeout bounds
- ensure returned timeout value matches effective timeout
- ensure direct mode and dry-run report the same effective timeout
- convert rc 124 to `status="timeout"`
- include partial redacted/truncated output when available

### 8. Output handling

Current behavior:

- `op.run_argv` truncates output to 4096 chars
- `run_test` redacts stdout/stderr after truncation
- audit record includes redacted stderr on failure
- audit summary includes raw `argv`

Audit tasks:

- confirm whether truncation should happen before or after redaction
- confirm stdout/stderr max length
- avoid logging large or sensitive command output
- ensure audit does not include full stdout
- ensure stderr in audit is redacted and bounded

Recommended direction:

- keep stdout/stderr in response redacted and truncated
- audit should include only status, argv summary or hash, workdir summary, timeout, exit code, refusal reason
- avoid storing stdout in audit
- only store bounded redacted stderr when necessary

### 9. Audit record requirements

Contract requires audit record includes:

- tool name
- dry-run / direct mode
- workdir summary
- command mode: legacy command or suite
- argv or suite name
- timeout
- exit code or refusal reason
- success / failure

Current audit includes:

- tool
- level
- apply_mode
- dry_run
- success
- changed
- summary
- path summary
- error

Audit gaps:

- no explicit command mode
- no structured argv field
- no timeout field
- no exit code field
- no status field
- blocked reason is only in `error`

Recommended direction:

- use `extra` for sanitized structured fields:
  - `status`
  - `command_mode`
  - `argv`
  - `timeout`
  - `exit_code`
  - `refusal_reason`
- avoid full stdout/stderr in audit

### 10. Suite mode readiness

Proposed v0.1 suite:

- `python_pytest`

Possible future schema:

```python
command: str | None = None
suite: str | None = None
target: str | None = None
workdir: str | None = None
timeout: int = 120
dry_run: bool = True
```

Audit tasks:

- decide whether to change MCP tool schema now or later
- assess connector refresh cost
- define suite argv generation:
  - `python_pytest` -> `["python", "-m", "pytest", target]`
- require target path inside workdir
- define allowed target defaults:
  - no target -> `.`
  - file or directory target -> normalized inside workdir

Recommended direction:

- do not change schema in the first coding pass unless explicitly approved
- first pass should harden current legacy command mode
- second pass should add suite mode after connector/schema implications are accepted

## Initial Risk Findings

### Risk 1: `npm test` can run arbitrary package scripts

Even if command is allowlisted, `npm test` delegates to `package.json` scripts. Inside an allowed workspace, this may be acceptable as test behavior, but it is not as constrained as `python -m pytest`.

Recommendation:

- document this as an intentional test-framework side effect
- optionally classify npm commands as higher-risk allowlist entries
- consider requiring explicit suite support for Node later

### Risk 2: `git status` and `git diff` are duplicated

Dedicated tools already exist:

- `hermes_git_status`
- `hermes_git_diff`

Recommendation:

- consider removing git commands from `workspace_run_test`
- or classify them as legacy read-only verification during transition

### Risk 3: blocked / fail / timeout are not first-class statuses

Current return shape can force caller-side guessing.

Recommendation:

- add explicit `status`
- preserve existing `success` for compatibility

### Risk 4: missing workdir may execute from unintended cwd

Current implementation allows `workdir=None`.

Recommendation:

- require explicit workdir for direct execution
- decide whether dry-run may still show a blocked plan or should refuse immediately

### Risk 5: target validation is not implemented

Current legacy command mode allows extra argv without checking whether those args are path targets.

Recommendation:

- implement target validation in suite mode first
- avoid over-parsing arbitrary legacy commands unless necessary

## Proposed Phase Plan

### Phase A: Documentation and audit only

Deliverables:

- review this audit plan
- create or approve `docs/capabilities/workspace_run_test.md`
- confirm accepted behavior changes

No code changes.

### Phase B: Minimal hardening without schema change

Keep existing MCP schema:

```python
command: str
workdir: str | None = None
timeout: int = 120
dry_run: bool = True
```

Implement:

- explicit `status`
- explicit `exit_code`
- explicit `blocked` refusal shape
- explicit `timeout` handling
- normalized/effective timeout reporting
- stronger workdir validation
- denied path check for workdir
- improved audit `extra`
- optionally remove or flag git commands in allowlist

No suite mode yet.

### Phase C: Tests for hardened legacy mode

Add or update tests for:

- dry-run accepted command
- direct pass command with fake runner
- direct fail command with fake runner
- timeout rc 124 maps to `status="timeout"`
- forbidden command maps to `status="blocked"`
- workdir outside allowed path blocked
- missing command blocked
- shell metacharacters blocked
- audit contains status and does not contain full output

### Phase D: Suite mode design

Only after Phase B is stable:

- decide schema change
- add `suite`
- add `target`
- implement `python_pytest`
- validate target inside workdir
- document suite mode

## Review Decisions Needed

Before coding, reviewer should decide:

1. Should `workdir` be required for `workspace_run_test`?
2. Should `git status` and `git diff` be removed from `_TEST_COMMAND_ALLOWLIST`?
3. Should `npm test` remain allowed in v0.1?
4. Should Phase B avoid MCP schema changes?
5. Should timeout max remain 600 seconds?
6. Should `status` be added while preserving `success`?
7. Should suite mode wait until a separate phase?

## Proposed Acceptance Criteria for First Coding Pass

First coding pass is accepted if:

- no `shell=True` is introduced
- existing dry-run semantics are preserved
- blocked command returns `status="blocked"`
- failing test returns `status="fail"`
- timeout returns `status="timeout"`
- passing test returns `status="pass"`
- dry-run returns `status="dry_run"`
- response includes argv, effective timeout, workdir, and exit code when applicable
- audit includes sanitized structured fields
- denied paths and allowed workspace paths remain enforced
- existing `workspace_apply_diff` behavior is untouched

## Proposed Rollback

If the implementation causes regressions:

- use `git restore operator_workspace.py`
- if tests were added, restore the affected test files
- restart `randoku-sidecar`
- refresh/reconnect connector if schema changed
- if schema did not change, connector refresh should not be required

## Non-Goals

This audit does not attempt to:

- turn `run_test` into a terminal
- support pipelines
- support arbitrary command composition
- install packages
- restart services
- enforce network isolation
- modify git index or remote state
- guarantee test frameworks have zero file side effects

## Recommended Next Step

After review approval:

1. inspect current tests for operator workspace tools
2. draft a minimal diff for Phase B
3. apply with `hermes_workspace_apply_diff` in `dry_run=true`
4. review diff
5. apply direct only after explicit approval
6. run tests through the hardened path where possible
