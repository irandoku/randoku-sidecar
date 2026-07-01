# Operator Mode for randoku-sidecar

`randoku-sidecar` is a local MCP bridge for exposing selected Hermes Agent capabilities to trusted MCP clients. In practice it has two supported MCP entry points:

- stdio for local MCP clients that can launch `server.py` directly
- loopback HTTP behind HTTPS/tunnel infrastructure for hosted or remote clients that need a reachable URL

Operator Mode is the safer control plane inside `randoku-sidecar`. It exposes operator tools, but tool visibility does not mean mutation is allowed. Whether a call can change anything depends on:

- the operator level
- the server apply mode
- the tool’s own `dry_run` argument
- and, for owner tools, the exact break-glass acknowledgement

Default posture should stay `dry_run` for always-on hosted/remote use. Local stdio integrations may use a higher level when the client and machine are trusted, but direct mutation still requires the tool call to pass `dry_run=false`.

## New user quickstart

1. Install `randoku-sidecar`.
2. Pick the transport that matches the client:
   - local client: configure stdio so the client launches `server.py`
   - hosted/remote client: run loopback HTTP, then put HTTPS/tunnel infrastructure in front of it
3. Start with dry-run Operator Mode unless you are doing a deliberate local maintenance session.
4. Call:
   - `hermes_operator_policy`
   - `hermes_operator_status`
   - `hermes_cron_list`
5. Only switch to direct mode when you are doing a deliberate maintenance session.

## Four safety postures

### A. Read-only default

No environment variables are needed.

Behavior:

- status, list, basic file read (`hermes_read_file`), skill view/diff, and
  config get work with no configuration
- the workspace-scoped read and git tools (`hermes_workspace_read`,
  `hermes_git_status`, `hermes_git_diff`) are fail-closed: they refuse until
  `RANDOKU_OPERATOR_ALLOWED_PATHS` names the directory you want to inspect
- mutating tools refuse because Operator Mode is disabled

Example:

```powershell
randoku-sidecar
```

This is the safest starting point if you only want inspection.

### B. Dry-run Operator Mode

This is the recommended always-on hosted/remote posture and a safe starting posture for local stdio clients.

Behavior:

- operator tools are available
- mutating tools return plans or previews
- nothing actually changes
- safe default for hosted/remote client use and first-run local testing

Example:

```powershell
$env:HERMES_HOME="C:\Users\<YOU>\AppData\Local\hermes"
$env:RANDOKU_OPERATOR_ENABLED="1"
$env:RANDOKU_OPERATOR_LEVEL="skills_config"
$env:RANDOKU_OPERATOR_APPLY_MODE="dry_run"
$env:RANDOKU_OPERATOR_ALLOWED_PROFILES="default,hermes-researcher,hermes-trt-manager,hermes-nexus-wiki"

python server.py
```

For hosted/remote HTTPS mode, use the same environment variables with HTTP enabled:

```powershell
python server.py --http --host 127.0.0.1 --port 4750
```

### C. Direct Operator Mode

Use this only when you intentionally want writes.

Behavior:

- the server policy allows direct mutation
- individual tool calls still must pass `dry_run=false`
- mutation requires two gates:
  1. server apply mode must be `direct`
  2. the individual call must ask for `dry_run=false`

Example:

```powershell
$env:HERMES_HOME="C:\Users\<YOU>\AppData\Local\hermes"
$env:RANDOKU_OPERATOR_ENABLED="1"
$env:RANDOKU_OPERATOR_LEVEL="skills_config"
$env:RANDOKU_OPERATOR_APPLY_MODE="direct"
$env:RANDOKU_OPERATOR_ALLOWED_PROFILES="default,hermes-researcher,hermes-trt-manager,hermes-nexus-wiki"

python server.py
```

A mutating tool still needs:

```json
{
  "dry_run": false
}
```

### D. Owner Mode

Break-glass only.

Behavior:

- owner tools are visible but refuse unless the exact owner acknowledgement is set
- owner mode is not recommended for always-on use
- owner mode still denies secret paths

Example:

```powershell
$env:RANDOKU_OPERATOR_ENABLED="1"
$env:RANDOKU_OPERATOR_LEVEL="owner"
$env:RANDOKU_OPERATOR_APPLY_MODE="direct"
$env:RANDOKU_OWNER_ACK="I_UNDERSTAND_THIS_CAN_MUTATE_MY_MACHINE"
```

Do not use Owner Mode for public, shared, or always-on connectors.

For the governance model behind Owner Mode — when it's appropriate to reach
for an owner primitive, and how output destined for a public system (like a
GitHub issue) must be sanitized before it leaves the machine — see
[`docs/owner-mode-governance.md`](owner-mode-governance.md).

## Operator levels

Higher levels include the lower levels before them.

| Level | What it unlocks |
| --- | --- |
| `read_only` | status, policy, audit tail, cron list/status, skill list/view/diff, config get, env status, gateway status, git status/diff |
| `cron` | plus cron run, pause, copy, move |
| `skills` | plus skill create, edit, patch, write_file, copy, sync_to_default, delete |
| `skills_config` | plus config set/patch and non-secret env set/copy |
| `workspace` | plus scoped workspace read/patch/write/test and gateway restart under allowed paths |
| `owner` | break-glass raw command and raw file patch/write; still denies secret paths and requires exact owner acknowledgement |

`skills_config` is a good normal operator level for trusted dry-run usage.
`workspace` is for scoped workspace file operations only under allowed paths.
`owner` is break-glass.

## Dry-run vs direct: the important bit

`RANDOKU_OPERATOR_APPLY_MODE=dry_run` means mutating tools only preview.
`RANDOKU_OPERATOR_APPLY_MODE=direct` means the server permits direct mutation.

But every mutating call still defaults to `dry_run=true`.
Actual mutation requires both:

- `RANDOKU_OPERATOR_APPLY_MODE=direct`
- tool argument `dry_run=false`

Dry-run cron move:

```json
{
  "source_profile": "hermes-researcher",
  "target_profile": "default",
  "job_id": "example-job-id",
  "pause_source": true,
  "test_run_target": false,
  "dry_run": true
}
```

Direct cron move:

```json
{
  "source_profile": "hermes-researcher",
  "target_profile": "default",
  "job_id": "example-job-id",
  "pause_source": true,
  "test_run_target": false,
  "dry_run": false
}
```

The direct version only mutates if the server is already running with apply mode `direct`.

## Local stdio setup

For local MCP clients such as desktop apps, CLIs, IDEs, and custom local tools,
prefer stdio. It avoids a listening socket, avoids a tunnel, and lets the
client manage the server process lifecycle directly.

Client configuration shape:

```json
{
  "command": "C:\\Users\\<YOU>\\randoku-sidecar\\.venv\\Scripts\\python.exe",
  "args": ["C:\\Users\\<YOU>\\randoku-sidecar\\server.py"]
}
```

Or point the client at a wrapper script that sets the environment first, as
long as the script writes human-readable output to stderr and leaves stdout for
MCP JSON-RPC.

Codex CLI example:

```bash
codex mcp add randoku-sidecar \
  --env HERMES_HOME="$HOME/.hermes" \
  --env RANDOKU_OPERATOR_ENABLED=1 \
  --env RANDOKU_OPERATOR_LEVEL=workspace \
  --env RANDOKU_OPERATOR_APPLY_MODE=direct \
  --env RANDOKU_OPERATOR_ALLOWED_PROFILES=default \
  --env RANDOKU_OPERATOR_ALLOWED_PATHS="$HOME/Projects,$HOME/Downloads" \
  --env RANDOKU_ENABLE_SESSION_SEARCH=1 \
  -- /absolute/path/to/randoku-sidecar/venv/bin/python /absolute/path/to/randoku-sidecar/server.py
```

Do not use `start.sh` for stdio clients; it is the loopback HTTP/tunnel
launcher for hosted or remote clients.

## Hosted/remote HTTPS setup

Keep the MCP server bound to `127.0.0.1`.
Put HTTPS/tunnel infrastructure in front of loopback only.
Keep always-on hosted/remote mode in `dry_run`.
Switch to `direct` only for a deliberate maintenance session.
Switch back to `dry_run` afterward.
Never enable Owner Mode on an always-on hosted/remote endpoint.

Safe tunnel posture example:

```powershell
$env:HERMES_HOME="C:\Users\<YOU>\AppData\Local\hermes"
$env:RANDOKU_OPERATOR_ENABLED="1"
$env:RANDOKU_OPERATOR_LEVEL="skills_config"
$env:RANDOKU_OPERATOR_APPLY_MODE="dry_run"
$env:RANDOKU_OPERATOR_ALLOWED_PROFILES="default,hermes-researcher,hermes-trt-manager,hermes-nexus-wiki"

python server.py --http --host 127.0.0.1 --port 4750
```

On this repository, `start.sh` is the local loopback HTTP launcher for
hosted/remote clients and keeps the server on `http://127.0.0.1:4750/mcp`.
ChatGPT developer connectors are one example of this pattern; a custom hosted
app or remote agent can use the same `/mcp` endpoint shape.

`start.sh` defaults to `RANDOKU_OPERATOR_APPLY_MODE=dry_run`. For an
intentional development or maintenance session, override it without editing the
script:

```bash
RANDOKU_OPERATOR_APPLY_MODE=direct ./start.sh
```

## Profile root normalization

Hermes data root is usually:

- Windows: `C:\Users\<YOU>\AppData\Local\hermes`
- Unix/macOS style: `~/.hermes`

If `HERMES_HOME` points to a named profile or `hermes-agent`, `randoku-sidecar` normalizes back to the data root for operator profile operations.
The default profile maps to the data root.
Named profiles map to `profiles/<profile-name>`.

## Audit logs

Audit log path:

- `%USERPROFILE%\AppData\Local\hermes\logs\randoku_operator_audit.jsonl` (preferred)
- `<randoku-sidecar>\logs\randoku_operator_audit.jsonl` (fallback)

What is logged:

- timestamp
- tool name
- level
- apply mode
- dry_run flag
- success / changed / summary
- error summary when a call fails
- profile or profiles involved
- path summary
- job id, skill name, or key when relevant
- prompt/content length plus SHA-256 for content-bearing calls

What is never logged:

- raw `.env` values
- full prompts
- full config values when they may contain secrets
- vault contents
- command output likely to contain secrets

Prompt/content is represented by length and hash only, not raw text.

## What is still denied

The server still refuses or redacts access to:

- `.env`
- auth files
- token files
- vault files
- SSH keys
- OAuth files
- cookies
- MCP token files
- secret-looking filenames

That denial applies even in higher modes.

Separately from secret denial, the workspace read/write and git status/diff
tools are **fail-closed on scope**: they refuse unless
`RANDOKU_OPERATOR_ALLOWED_PATHS` is set and the target path or git workdir is
under it. `hermes_git_diff` also refuses a secret-like `pathspec`.

## Troubleshooting

### I only see 5 tools

- Reconnect the connector.
- Create a new connector name if the old one is cached.
- Verify `/mcp` directly with list-tools.
- If direct list-tools shows 39 tools, the server is fine and the connector registration is stale.

### Profile appears missing

- Check `HERMES_HOME`.
- Confirm root normalization back to the data root.
- Remember that default resolves to the data root, while named profiles map under `profiles/<profile-name>`.

### Mutating tools refuse

Check all of these:

- `RANDOKU_OPERATOR_ENABLED`
- `RANDOKU_OPERATOR_LEVEL`
- `RANDOKU_OPERATOR_APPLY_MODE`
- the tool call’s `dry_run` argument

A refusal here is usually correct behavior, not a bug.

### Owner tools refuse

That is expected unless the exact owner acknowledgement is set:

```powershell
$env:RANDOKU_OWNER_ACK="I_UNDERSTAND_THIS_CAN_MUTATE_MY_MACHINE"
```

If the string differs, owner tools should still refuse.

## Keep in mind

- Operator Mode is not a sandbox.
- Public exposure is not safe without real auth.
- Direct mode is not the default.
- Owner Mode is not safe for always-on use.
- Use OS isolation for untrusted input.
