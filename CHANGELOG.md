# Changelog

## [Unreleased]

- Added governed, provider-neutral semantic memory write-back (write-back phase
  2): a new `hermes_memory_provider_writeback(tool, args, dry_run=True)` tool
  that proxies an allowlisted subset of the configured memory provider's own
  write tools (e.g. honcho conclusions) through the neutral `MemoryManager`
  interface. Disabled by default — only provider-native tool names listed in the
  `RANDOKU_MEMORY_WRITEBACK_TOOLS` env allowlist are permitted; a direct write
  requires operator level `skills_config` + `apply_mode=direct` + `dry_run=false`
  and emits an audit record (provider, tool, arg keys, args length/sha256, never
  raw content). The provider's own success/failure is verified and surfaced
  (never a silent success/orphan), the per-call manager is torn down in a
  `finally` path, and a transient session-init error is retried. The sidecar
  names no provider in code, so swapping providers is a config change. Live
  verified end-to-end against honcho. See `docs/memory-writeback-audit.md` §14.

- Fixed `hermes_gateway_status` to parse current Hermes gateway runtime files:
  JSON `gateway.pid`, `gateway_state.json["pid"]`, and
  `gateway_state.json["platforms"]`, while keeping compatibility with the legacy
  plain PID file and top-level adapter schema.

- Governed `hermes_memory` writes under OperatorPolicy (memory write-back v1).
  `add` / `replace` / `remove` now require operator level `skills_config` plus
  the mutation gate, take `dry_run` (default `True` → returns a plan with target
  file and content length/sha256, writing nothing), and emit an audit record.
  Direct write needs `apply_mode=direct` and `dry_run=false`. The legacy
  `RANDOKU_ENABLE_MEMORY_WRITE` env flag was removed in favor of the tiered
  operator model. `search` stays read-only and always available. No new tool was
  added (the existing `hermes_memory` was extended); no `allowed_paths` check
  applies since memory targets the fixed Hermes memory dir.

- Established the project identity as `randoku-sidecar`, including README,
  package metadata, console script, environment variable names, example scripts,
  operator docs, site copy, and attribution notes. This preserves the original
  `hermes-gpt` lineage while making the fork independently branded.
- Replaced the inherited branding image with a new randoku-sidecar visual asset,
  updated README to reference `assets/randoku-sidecar-branding.png`, and included
  the asset in packaging metadata.
- Added read-only CodeGraph operator tools for status, file listing, search,
  overview, and inspect workflows under the same workspace path policy.
- Made the read-only operator tools fail-closed to match the write tools.
  `hermes_workspace_read`, `hermes_git_status`, and `hermes_git_diff` now
  require `RANDOKU_OPERATOR_ALLOWED_PATHS` to be set and the target path/workdir
  under it, and apply the denied secret-path check (previously these were
  fail-open: any non-denied path was reachable, and the git tools did not
  check denied paths at all). `hermes_git_diff` additionally refuses a
  secret-like `pathspec`. Read/write path gating is now a single shared
  `OperatorPolicy` helper. This is a behavior change: operator read/git tools
  require an allow-list; the basic `hermes_read_file` tool is unaffected.
- Allowed `workspace_run_test` to run repo-local virtualenv pytest launchers
  such as `./venv/bin/python -m pytest` while continuing to reject system Python,
  pip, shell metacharacters, and non-allowlisted commands.
- Wired Hermes' file-backed `MemoryStore` into `hermes_memory`, so search and
  gated write actions operate on the same loaded MEMORY.md / USER.md store used
  by Hermes internals.
- Added external memory context recall through Hermes' `MemoryManager`, defaulting
  the sidecar wrapper to the `cli` platform for cross-entry-point recall.
- Added a read-only memory write-back audit document covering flat-file memory,
  provider proxy feasibility, Honcho conclusion scope, session-key collision
  behavior, and lifecycle risks.
- Added test coverage for `hermes_workspace_apply_diff`, the core single-file
  unified-diff mutation capability: policy gates (allowed paths, denied secret
  paths, missing files, empty diffs), dry-run vs direct apply, strict-parser
  refusals (multi-file, rename, binary, new-file-mode, context mismatch),
  multi-hunk application, and the git vs non-git backup policy.
- Completed the operator tool-surface test to assert `hermes_workspace_apply_diff`
  is registered.
- Updated `ROADMAP.md` to mark the completed identity, backup policy, patch
  capability, and memory initialization/search/gating work, matching the shipped
  implementation.
- Added a `Verification` section to the `workspace_apply_diff` capability
  contract mapping each clause to its covering tests.
- Updated the release checklist to use the repo-local `./venv/bin/python`
  interpreter and explicitly confirm Python 3.10+.

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
