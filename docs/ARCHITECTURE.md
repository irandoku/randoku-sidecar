# Randoku Sidecar Architecture

`randoku-sidecar` is a policy-aware AI workspace sidecar.

The project goal is not to give an AI unrestricted shell access. The goal is to expose a small set of explicit, auditable capabilities that let an AI help with engineering work while staying inside predictable safety boundaries.

## Core idea

```text
AI client
  -> MCP / future transports
  -> Randoku Sidecar
  -> Policy Engine
  -> Capability Tools
  -> Workspace
```

The AI client may be ChatGPT, Claude, Gemini, Hermes, Cursor, or another MCP-capable system. The sidecar should not be designed around one model vendor.

## Design principles

1. Policy first

   Every mutating capability must pass a shared policy layer before touching the workspace.

2. Capabilities over shell commands

   Prefer intent-based tools such as `apply_diff` and `run_test` over arbitrary command execution.

3. Least privilege by default

   Read-only should be the default posture. Workspace mutation requires explicit environment configuration and direct tool calls.

4. Human approval boundary

   High-risk actions such as terminal execution, service restart, deployment, git push, credential handling, or production changes must stay outside routine automation.

5. Auditable execution

   Tools should record what they planned, what they changed, whether the call was dry-run, and how to rollback.

6. Git-aware behavior

   In git repositories, git is the rollback mechanism. File-level `.bak` backups should be avoided by default inside git worktrees to prevent repository pollution.

7. Refuse instead of guessing

   If a patch, diff, test target, or path does not match strict expectations, the tool should fail safely instead of attempting fuzzy recovery.

## Layers

### Policy Engine

Responsible for:

- allowed paths
- denied secret paths
- operator level
- dry-run vs direct mode
- owner acknowledgement
- backup policy
- audit records

### Capability Engine

Initial capabilities:

- read workspace files
- patch small text snippets
- apply strict unified diffs
- run allowlisted tests
- inspect git status and diff

Future capabilities may include formatting, linting, build checks, and workflow orchestration.

### Workflow Layer

A safe AI engineering workflow should look like:

```text
Recall / inspect
  -> analyze
  -> propose patch
  -> dry-run diff
  -> human approval
  -> apply patch
  -> run tests
  -> review result
  -> human commit / publish
```

The sidecar helps with execution, but it should not silently take ownership of decisions that carry operational risk.

## Near-term implementation focus

1. Git-aware backup policy
2. `workspace_apply_diff`
3. intent-based `workspace_run_test`
4. improved audit and rollback hints
5. memory integration repair later, after the tool foundation is stable
