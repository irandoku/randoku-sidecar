# Public Issue Sanitization Policy

This document is a companion to
[`docs/owner-mode-governance.md`](owner-mode-governance.md) §5–§7. It defines
the contract a future GitHub-issue-creation recipe (or any recipe that
writes to a public external system) must satisfy. **No such recipe is
implemented yet** — see the constraint list at the bottom of this document.

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

## Future recipe requirement

A future GitHub issue creation recipe (e.g. a `hermes_owner_repo_issue_create`
built on top of `hermes_owner_run_command`, or an equivalent) must:

1. Distinguish private analysis input from the public issue body as two
   separate values — never pass raw analysis straight through as the body.
2. Sanitize the public issue body **before** the dry-run step, not after.
3. Show the sanitized preview in dry-run output, so the human reviews
   exactly what will be published, not the pre-sanitization draft.
4. Use `--body-file` (a temp file passed to `gh issue create --body-file`),
   never raw body text in argv — argv can leak through process listings and
   shell history in a way a temp file does not.
5. Audit only body length and a SHA-256 hash, plus a structured
   sanitization summary (e.g. counts of paths/usernames/vault-refs
   replaced) — never the raw body text, consistent with the existing audit
   policy in `docs/operator-mode.md` (§ "Audit logs").
6. Require human review before direct (non-dry-run) creation, same as any
   other Owner direct mutation (`docs/owner-mode-governance.md` §4, §8).

## Explicit non-goals of this pass

This document defines the policy only. Per the governing issue for this
work:

- `hermes_owner_repo_issue_create` / `hermes_owner_github_issue_create` are
  **not implemented** in this pass.
- Owner Mode is **not enabled** as part of this work.
- No `gh issue create` (or any external write) is executed as part of this
  work.
