# Owner Mode Governance

This document governs Owner Mode as a privilege-escalation layer, not a
workflow guide. For setup instructions, safety postures, and troubleshooting,
see [`docs/operator-mode.md`](operator-mode.md). This document exists to
answer a narrower question: *when is it correct to reach for an Owner
primitive, and what must wrap around it before its output leaves the
machine.*

## 1. Purpose

Owner Mode is explicit, temporary, human-approved privilege escalation. It is
not the default mode, and it is not intended for routine model autonomy. A
session should enter Owner Mode for a specific, bounded task, then leave it.
Owner Mode should not be the standing configuration of an always-on hosted or
remote connector.

## 2. Capability levels

`randoku-sidecar` uses an ascending, superset ladder (`has_level` in
`operator_policy.py`):

```text
read_only
cron
skills
skills_config
workspace
owner
```

Each level includes every capability of the levels below it. `owner` is the
top of the ladder and is the only level that also requires the separate owner
acknowledgement described below — reaching `owner` via
`RANDOKU_OPERATOR_LEVEL` is necessary but not sufficient.

## 3. Owner Mode activation requirements

All three of the following must be true before an Owner tool call is even
considered:

```text
RANDOKU_OPERATOR_ENABLED=1
RANDOKU_OPERATOR_LEVEL=owner
RANDOKU_OWNER_ACK=I_UNDERSTAND_THIS_CAN_MUTATE_MY_MACHINE
```

This is enforced by `OperatorPolicy.require_owner`, which checks
`require_level("owner")` and then `owner_mode_ready` (level + enabled + exact
ack string). Getting the ack string wrong, or omitting it, refuses the call
with the same message as not being in Owner Mode at all — there is no partial
credit.

Passing that gate only unlocks **dry-run** Owner calls. Direct mutation (an
actual write, patch, or command execution) additionally requires:

```text
RANDOKU_OPERATOR_APPLY_MODE=direct
dry_run=false
```

If apply mode is `dry_run` and a call still passes `dry_run=false`,
`hermes_owner_run_command` silently downgrades to a dry-run plan instead of
executing (`test_owner_run_command_in_apply_mode_dry_run_returns_dry_run_plan`
pins this). That is deliberate: the safer failure mode is "show the plan"
over "refuse loudly," since the caller already demonstrated it wanted the
action to happen.

## 4. Dry-run first principle

Every governed Owner workflow should be dry-run first. The model's job is to
produce a reviewable plan — the exact argv, the exact diff, the exact file
write — not to reason its way to skipping the preview. The human owner
reviews target, account/repo, command, and blast radius before flipping to
`dry_run=false`. This applies even when the model is confident; confidence is
not a substitute for the human seeing the plan.

## 5. Owner primitives vs governed recipes

**Owner primitives** are the raw, general-purpose escape hatch:

- `hermes_owner_run_command`
- `hermes_owner_patch`
- `hermes_owner_write_file`

These are intentionally low-level and unopinionated about *what* they're
used for. They know how to gate (level, ack, apply mode, dry-run, secret
paths, catastrophic patterns) but nothing about the semantics of any
particular external system.

**Governed recipes** are higher-level workflows built on top of Owner
primitives for a specific, recurring, external-write task — for example:
public-safe GitHub issue creation, PR creation, a git push, a service
restart. A recipe adds the domain-specific safety logic that a raw command
call cannot know on its own: what's safe to make public, what the audit
record should summarize instead of dumping raw content, what confirmation
shape the human needs to see for *this* kind of action.

Any workflow that writes to a system outside the local machine — a public
GitHub issue, a PR, a remote push — should become a governed recipe rather
than a one-off `hermes_owner_run_command` invocation, once that workflow
recurs. A single ad hoc command is fine for a true one-off; a recipe is
worth building the moment the same class of action is likely to happen
again, because that's where sanitization logic belongs (see §7) rather than
being re-derived by the model each time from raw analysis.

No governed recipe exists yet in this codebase. This document defines the
policy a future recipe must satisfy; it does not implement one. See
[`docs/public-issue-sanitization.md`](public-issue-sanitization.md) for the
first such recipe's contract (GitHub issue creation).

## 6. Public or external output has a different safety boundary

Content written to local notes, Honcho memory, local audit logs, or Obsidian
notes stays on infrastructure the owner controls. Content sent to an
external system — most importantly a **public** GitHub issue — leaves that
boundary permanently and may be indexed, cached, or seen by anyone. These
are not the same trust tier, and a tool that's safe for the former is not
automatically safe for the latter.

## 7. Public-safe issue body principle

Raw local/private analysis — the kind of thing this session produces
routinely (file paths, audit excerpts, memory-provider observations) — must
never be published directly to a public GitHub issue. It must first be
transformed into a sanitized public issue body. The full transformation
rules and examples live in
[`docs/public-issue-sanitization.md`](public-issue-sanitization.md).

## 8. Human review

For Owner Mode direct operations, the human owner retains final review
authority. Dry-run plans exist so that authority has something concrete to
review. No governed recipe should attempt to bypass or shorten that review
step (e.g., by defaulting to direct execution, or by omitting the sanitized
preview from dry-run output).

## 9. Current Owner primitive behavior (pinned, intentional)

The following behaviors were audited against `operator_policy.py` and
`operator_workspace.py` and are **intentional** — Owner Mode is designed as
a high-privilege escape hatch that trades scope restriction for the owner
acknowledgement gate, not as a stricter version of Workspace Mode:

1. **`hermes_owner_patch` and `hermes_owner_write_file` bypass
   `RANDOKU_OPERATOR_ALLOWED_PATHS`.** They still call `is_denied_path`
   (secret-path denial), but never call `require_workspace_path` /
   `_require_allowed_path`. This is unlike the Workspace-level equivalents
   (`hermes_workspace_patch`, `hermes_workspace_write_file`), which are
   fail-closed on scope. Pinned by
   `test_owner_patch_allows_normal_path_outside_allowed_paths_if_owner_ready`
   and
   `test_owner_write_file_allows_normal_path_outside_allowed_paths_if_owner_ready`.
2. **`hermes_owner_run_command` does not require `workdir`.** `workdir` is
   an optional pass-through to the runner, unlike
   `hermes_workspace_run_test`, which raises if `workdir` is missing. Pinned
   by `test_owner_run_command_dry_run_returns_plan` (no `workdir` passed)
   and `test_owner_run_command_direct_runs` (no `workdir` passed).
3. **`hermes_owner_run_command` does not require `workdir` under
   `RANDOKU_OPERATOR_ALLOWED_PATHS`**, even when that variable is set to an
   unrelated directory. Pinned by
   `test_owner_run_command_allows_workdir_outside_allowed_paths_if_owner_ready`.
4. **`hermes_owner_run_command` is denylist-based, not allowlist-based.**
   It blocks a fixed set of catastrophic patterns
   (`_CATASTROPHIC_PATTERNS`) and secret-touching substrings
   (`_command_touches_secrets`), and otherwise runs whatever `argv` it was
   given via `shlex.split` + `shell=False`. There is no allowlist of
   permitted binaries the way `hermes_workspace_run_test` has
   (`_is_allowed_test_command`). Pinned by
   `test_owner_run_command_is_denylist_based_for_non_dangerous_command`.
5. **Owner Mode does not override secret-path denial.** `is_denied_path`
   still applies to `hermes_owner_patch` and `hermes_owner_write_file`, and
   `_command_touches_secrets` still applies to `hermes_owner_run_command`,
   regardless of level or ack. Pinned by
   `test_owner_patch_still_denies_secret_paths`,
   `test_owner_write_file_still_denies_secret_paths`, and
   `test_owner_run_command_blocks_catastrophic_or_secret_touching`.

Because this is intentional, no behavior change is planned. The mitigating
controls are: the owner-ack gate (§3), the catastrophic-pattern and
secret-path denylists (still enforced per item 5 above), output redaction
(`redact_output`), and audit logging of every call. A governed recipe built
on these primitives (§5) is expected to add its own scope discipline (e.g.,
pinning a repo, a command shape, or a target path) on top, rather than
relying on the primitive to restrict itself.
