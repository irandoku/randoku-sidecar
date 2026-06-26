# Capability Contract: workspace_run_test

## Purpose

Run a constrained test or verification command inside an allowed workspace.

This capability exists to let AI-assisted engineering workflows verify changes without granting unrestricted terminal access. It should turn routine verification into a safe capability, not a shell-shaped trapdoor wearing a nice hat.

## Authority

`workspace_run_test` may:

- run an allowlisted test, lint, type-check, or verification command
- run inside an allowed workspace directory
- capture stdout and stderr
- return exit code, structured status, and concise diagnostic output
- record a sanitized audit entry for each dry-run, executed test, or refusal

`workspace_run_test` must not:

- execute arbitrary shell commands
- use `shell=True`
- run destructive commands
- run git commit, git push, or git index mutations
- access denied paths or secret-like paths
- run outside allowed workspace paths
- silently ignore timeout or command rejection
- treat a blocked command as a failing test

## Command Mode

Version 0.1 supports legacy allowlisted command mode.

The public tool schema remains:

```python
command: str
workdir: str | None = None
timeout: int = 120
dry_run: bool = True
```

The command string is parsed into argv with `shlex.split`. The command is then checked against a conservative allowlist. Execution always uses argv with `shell=False`.

Version 0.1 does not yet support suite mode. Future suite mode may introduce explicit fields such as `suite` and `target`, but that is a separate design phase.

## Preconditions

Before running a test, all of the following must be true:

- operator level allows workspace test execution
- `command` is present and parseable
- parsed argv matches the test / lint allowlist
- forbidden shell or destructive substrings are not present
- `workdir` is provided
- `workdir` is not denied by path safety policy
- `workdir` is under an allowed workspace path
- `workdir` exists and is a directory
- timeout can be normalized to the bounded range
- dry-run mode is respected unless direct execution is explicitly allowed

## Status Model

Responses include a structured `status` field.

Supported statuses:

- `dry_run`: command was validated and planned, but not executed
- `pass`: command executed and returned exit code `0`
- `fail`: command executed and returned a non-zero exit code other than timeout
- `timeout`: command execution reached the timeout path and returned exit code `124`
- `blocked`: command was refused before execution

`success` is preserved for compatibility:

- `true` for `dry_run` and `pass`
- `false` for `fail`, `timeout`, and `blocked`

Blocked commands must be distinguishable from failing tests.

## Dry-run Response

On dry-run success, the response includes:

```json
{
  "success": true,
  "dry_run": true,
  "status": "dry_run",
  "plan": {
    "would_run": true,
    "status": "dry_run",
    "command_mode": "legacy_command",
    "argv": ["pytest", "-q"],
    "shell": false,
    "workdir": "/path/to/workspace",
    "timeout": 120
  }
}
```

No command is executed during dry-run.

## Execution Response

On direct execution, the response includes:

```json
{
  "success": true,
  "dry_run": false,
  "status": "pass",
  "exit_code": 0,
  "returncode": 0,
  "argv": ["pytest", "-q"],
  "workdir": "/path/to/workspace",
  "timeout": 120,
  "stdout": "...",
  "stderr": ""
}
```

For compatibility, both `exit_code` and `returncode` are returned.

## Failure Contract

On capability refusal:

- no command is executed
- response status is `blocked`
- response includes a clear refusal reason in `error`
- the refusal is recorded in audit

Example:

```json
{
  "success": false,
  "dry_run": false,
  "status": "blocked",
  "error": "Command not in the test/lint allowlist."
}
```

On test failure:

- command was executed without a shell
- response status is `fail`
- response includes `exit_code` / `returncode`
- stdout and stderr are redacted and truncated

On timeout:

- command was executed without a shell
- response status is `timeout`
- response includes `exit_code` / `returncode` value `124`
- partial stdout and stderr may be returned after redaction and truncation

## Timeout Policy

Timeout is bounded to the same range as `op.run_argv`:

- minimum: 1 second
- maximum: 600 seconds

The effective timeout is returned in both dry-run plans and direct execution responses.

## Output Handling

The capability returns captured stdout and stderr from the subprocess helper.

Output must be:

- bounded
- redacted for common secret patterns
- safe to show to the caller as diagnostic output

Raw command output should not be logged in full to audit.

## Audit Record

The audit record should include:

- tool name
- dry-run / direct mode
- workdir summary
- command mode: `legacy_command`
- argv
- timeout
- status
- exit code when applicable
- refusal reason when blocked
- success / failure

Audit records must not include full raw stdout.

## Behavior Preservation Requirements

Implementation must preserve:

- workspace path policy
- denied path policy
- dry-run semantics
- direct mutation / execution gate
- no-shell rule
- command allowlist behavior
- bounded timeout
- redaction and truncation
- audit logging
- compatibility with existing `success` and `returncode` fields

## Initial Scope

Version 0.1 supports:

- legacy allowlisted command mode
- explicit `workdir` validation
- structured status model
- pass / fail / timeout / blocked distinction
- redacted stdout / stderr response
- structured audit fields

Version 0.1 explicitly does not support:

- arbitrary terminal execution
- shell pipelines
- shell redirection
- shell command composition
- network policy enforcement
- generic package installation
- service restart
- git commit / push
- git index mutation
- suite mode
- target path validation beyond current legacy command constraints

## Rollback Strategy

This capability should not intentionally mutate files.

Some test frameworks may create caches or build artifacts. Those are treated as test side effects and should be documented later by suite-specific contracts when suite mode is introduced.

For implementation rollback:

```bash
git restore operator_workspace.py test_operator_workspace.py
```

For the contract document itself:

```bash
git restore docs/capabilities/workspace_run_test.md
```

## Verification

The Phase B implementation should be considered verified when the following pass through `workspace_run_test`:

```bash
pytest test_operator_workspace.py -q
pytest -q
```

Expected response for passing tests:

```json
{
  "success": true,
  "status": "pass",
  "exit_code": 0
}
```
