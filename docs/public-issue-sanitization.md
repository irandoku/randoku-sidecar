# Public Issue Sanitization Policy

This document is a companion to
[`docs/owner-mode-governance.md`](owner-mode-governance.md) §5–§7. It defines
the contract a GitHub-issue-creation recipe (or any recipe that writes to a
public external system) must satisfy.

`hermes_owner_repo_issue_create` implements this contract end to end:
preflight (`git rev-parse`, `gh auth status`, `gh repo view`), unconditional
sanitization, a reviewable dry-run plan, and — when `apply_mode=direct` and
`dry_run=false` — direct creation via `gh issue create --body-file`. See the
scope list at the bottom of this document for what direct creation still
does *not* cover.

## Problem

Raw local/private analysis — the kind this session produces routinely while
auditing or debugging — may contain:

- absolute local paths (`/Users/<local-user>/...`)
- local usernames
- private workspace locations
- Obsidian vault paths
- local sidecar/runtime details (ports, hostnames, PIDs)
- private audit details
- memory-provider observations (Honcho, etc.)
- secret-like path references
- token/auth/cookie/password terminology, even when only naming a *kind* of
  file and not an actual secret value
- private agent/session names or internal workflow details

GitHub issues on a public repository are, by default, public. Raw
local/private analysis must never be used directly as an issue body — it
must go through a sanitization transform first.

## Public-safe transformation examples

| Raw (private)                                   | Sanitized (public)         |
| ------------------------------------------------ | --------------------------- |
| `/Users/<local-user>/Projects/randoku-sidecar`    | `<repo-root>`                |
| private Obsidian vault full path                 | `<private-notes-vault>`      |
| `~/.ssh/id_ed25519`                               | `<secret-like-path>`         |
| `.env` / token / credential file paths            | `<secret-like-path>`         |
| localhost internal runtime details (ports, PIDs)  | `local sidecar runtime`      |
| another private/local agent session name          | `another local agent session`|

The transform is a substitution, not a summary: it must preserve the
technical substance of the finding (which file, which function, which
behavior) while stripping anything that identifies the local machine, its
user, or private infrastructure.

## Allowed public details

These are generally safe to include in a public issue body as-is, because
they describe the project's own public source, not the local environment:

- repo-relative file paths — `operator_policy.py`, `operator_workspace.py`
- public function/tool names — `hermes_owner_run_command`
- public env var names — `RANDOKU_OPERATOR_LEVEL`, `RANDOKU_OWNER_ACK`
- the concepts `dry_run`, `audit`, `body-file`
- acceptance criteria and test names

## Recipe requirement

`hermes_owner_repo_issue_create` (built directly on `git`/`gh` via the same
`runner` pattern as the owner primitives, not on `hermes_owner_run_command`)
satisfies all of the following:

1. Distinguish private analysis input from the public issue body as two
   separate values — never pass raw analysis straight through as the body.
2. Sanitize the public issue body **before** the dry-run step, not after.
3. Show the sanitized preview in dry-run output, so the human reviews
   exactly what will be published, not the pre-sanitization draft.
4. Use `--body-file`, never raw body text in argv — argv can leak through
   process listings and shell history in a way a temp file does not. Direct
   creation writes the sanitized body to a `tempfile.NamedTemporaryFile`,
   passes its path to `gh issue create --body-file`, and deletes it in a
   `finally` block immediately after the call, success or failure.
5. Audit only body length and a SHA-256 hash, plus a structured
   sanitization summary (e.g. counts of paths/usernames/vault-refs
   replaced) — never the raw body text, consistent with the existing audit
   policy in `docs/operator-mode.md` (§ "Audit logs"). The returned
   `argv`/`would_run` fields also always show a `<tempfile>` placeholder,
   never the real (already-deleted) path.
6. Require human review before direct (non-dry-run) creation: direct
   creation uses the exact same gate as every other Owner direct mutation
   (`apply_mode=direct` + `dry_run=false`; `docs/owner-mode-governance.md`
   §4, §8) — there is no separate confirmation step beyond that, by design,
   since the dry-run plan is what the human is expected to review before
   flipping `dry_run` to `false`.

## Scope

- PR creation, git push, release/publish operations, a generic GitHub
  toolbox, and arbitrary `gh` command execution remain out of scope — this
  recipe only ever runs `git rev-parse`, `gh auth status`, `gh repo view`,
  and `gh issue create`.
- Tests never invoke real `gh`/`git`; every test fakes the `runner`
  parameter, so no test in this repository performs an external write.
