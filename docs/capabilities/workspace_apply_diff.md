# Capability Contract: workspace_apply_diff

## Purpose

Apply a reviewed single-file unified diff inside an allowed workspace.

This capability exists because string replacement is too fragile for function-level changes. Engineering work needs reviewable diffs, not copy-paste surgery disguised as tooling.

## Authority

`workspace_apply_diff` may:

- read one target file
- validate one unified diff
- modify exactly one non-secret file under allowed workspace paths
- perform an atomic write after validation succeeds

`workspace_apply_diff` must not:

- execute shell commands
- modify multiple files
- rename files
- delete files
- create binary patches
- touch denied paths or secret-like files
- apply fuzzy or guessed patches
- commit, stage, push, or otherwise operate on git state

## Preconditions

Before applying a diff, all of the following must be true:

- operator level allows workspace mutation
- target path is under an allowed workspace path
- target path is not denied by secret/path policy
- target file exists and is a regular text file
- diff is a single-file unified diff
- all hunks are parseable
- all context and removed lines match the current file exactly
- no fuzzy matching is required
- dry-run mode is respected unless direct mutation is explicitly allowed

## Postconditions

On success:

- exactly one file is modified
- write is atomic
- file content equals the validated diff result
- response includes hunk count and preview diff
- response includes backup policy
- response includes rollback hint

## Failure Contract

On failure:

- no file is modified
- no partial write is allowed
- the error explains the first blocking reason
- mismatch errors should include enough context for review without dumping excessive file content

Refusal is preferred over guessing.

## Rollback Strategy

For git worktrees:

```bash
git restore <path>
```

For non-git workspaces:

- use the existing file-level backup policy when available
- return the backup path if one is created

## Audit Record

The audit record should include:

- tool name
- dry-run / direct mode
- target path summary
- hunk count
- backup policy
- success / failure
- concise error if failed

Raw diff content should not be logged in full if it may be large. A length and hash may be used for large inputs.

## Behavior Preservation Requirements

Implementation must preserve:

- workspace path policy
- denied secret path policy
- dry-run semantics
- direct mutation gate
- no-shell rule
- atomic write behavior
- audit logging
- git-aware backup policy

## Initial Scope

Version 0.1 supports only:

- one target file
- standard unified diff hunks
- exact context matching
- UTF-8 text files

Version 0.1 explicitly does not support:

- multi-file patches
- fuzzy apply
- binary patches
- file rename
- file delete
- chmod / mode changes
- git index operations
