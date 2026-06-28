# Changelog

## [Unreleased]

- Added test coverage for `hermes_workspace_apply_diff`, the core single-file
  unified-diff mutation capability: policy gates (allowed paths, denied secret
  paths, missing files, empty diffs), dry-run vs direct apply, strict-parser
  refusals (multi-file, rename, binary, new-file-mode, context mismatch),
  multi-hunk application, and the git vs non-git backup policy.
- Completed the operator tool-surface test to assert `hermes_workspace_apply_diff`
  is registered.
- Updated `ROADMAP.md` to mark Phase 1 (backup policy) and Phase 2 (patch
  capability) complete, matching the shipped and now-tested implementation.
- Added a `Verification` section to the `workspace_apply_diff` capability
  contract mapping each clause to its covering tests.

## 0.2.0 - 2026-06-21

- Added tiered Operator / Owner Mode tooling for trusted MCP clients.
- Kept the default posture read-only or dry-run, with direct mutation gated by explicit server and per-call opt-in.
- Added operator policy, status, audit, cron, config, env, gateway, workspace, and owner-scope tools.
- Fixed data-root normalization so operator profile operations resolve back to the Hermes data root.
- Updated packaging to include operator modules and release docs.
- Added a new Operator Mode guide, quickstart, and troubleshooting for new users.

## 0.1.0 - 2026-06-18

- Initial local-dev release.
- Added FastMCP stdio and streamable HTTP server.
- Added Hermes file read/search, memory search, skill list/view, and optional gated write/patch/session/terminal capabilities.
- Added release safety gates for write tools, memory writes, session search, terminal execution, and remote no-auth mode.
- Added pytest coverage for default tool surface, auth metadata, safety gates, timeout capping, remote profile blocking, and HTTP initialize.
