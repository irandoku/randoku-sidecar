# randoku-sidecar

![Randoku Sidecar branding](assets/randoku-sidecar-branding.png)

`randoku-sidecar` is a policy-first MCP sidecar for [Hermes Agent](https://github.com/NousResearch/hermes-agent). It imports selected local Hermes Agent internals at runtime and exposes them to MCP clients (Claude, ChatGPT, Cursor, Codex, Gemini, opencode, custom apps) **without modifying Hermes Agent source files**.

It is a **local-dev tool**: not a hosted service, not a fork of Hermes Agent itself, not a generic remote dev container, and not a replacement for any deployment tool. It supports two practical MCP entry points: **stdio** for local clients that can launch a subprocess, and loopback **HTTP behind HTTPS/tunnel infrastructure** for hosted or remote clients that need a reachable URL.

> **Origin & attribution.** `randoku-sidecar` began as a fork of [`hermes-gpt`](https://github.com/asimons81/hermes-gpt) by asimons81 (MIT) and has since diverged toward a policy-first, capability-based design. The original MIT license and copyright are preserved. See [`docs/ATTRIBUTION.md`](docs/ATTRIBUTION.md).

## What it does

The sidecar exposes a tiered, auditable set of operator tools so a trusted MCP client can drive Hermes safely: read files and skills, run cron and skill operations, wire profile config, edit scoped workspace files, apply reviewed unified diffs, run an allowlisted test/lint suite, and — behind an explicit break-glass acknowledgement — owner-level command and file access.

The guiding principle is **safe by default, mutation by explicit opt-in**:

| Mode | Env posture | What happens |
| --- | --- | --- |
| Read-only | no operator env vars | read/list/status tools only; mutations refuse |
| Dry-run Operator | operator enabled + `apply_mode=dry_run` | mutating tools return plans/previews only |
| Direct Operator | operator enabled + `apply_mode=direct` | writes allowed only when the tool call also sets `dry_run=false` |
| Owner Mode | `level=owner` + exact owner ack | break-glass local owner tools; still denies secret paths |

For the full Operator Mode guide, new-user quickstart, and stdio/tunnel safety model, see [`docs/operator-mode.md`](docs/operator-mode.md).

## Security posture

By default, `randoku-sidecar` is designed for a single trusted local machine:

- Stdio is the default transport and does not open a listening socket.
- HTTP binds to `127.0.0.1` by default; binding elsewhere in the `local-dev` profile prints a not-release-safe warning.
- Tools advertise `noauth` only for local-dev MCP clients.
- Write, patch, terminal execution, memory writes, and session search are disabled or hidden by default.
- Remote/public exposure is not supported until a real authentication layer (OAuth or equivalent) is added.
- Operator Mode is **not a sandbox**. Use OS-level isolation (container, VM) for untrusted input — the operator gates are defense-in-depth, not a security boundary.

Do not expose this server publicly without authentication. A temporary tunnel is acceptable only for hosted/remote-client testing, and only when you understand that any enabled tool is reachable through that URL.

## Prerequisites

- Python **3.10+**
- A local Hermes Agent install
- MCP Python SDK and Uvicorn (installed via `requirements.txt`)

## Install

```bash
git clone https://github.com/irandoku/randoku-sidecar.git
cd randoku-sidecar
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
```

All commands below use the repo-local interpreter `./venv/bin/python` so they are unambiguous regardless of what `python` points to on your system. On Windows PowerShell, use `.\venv\Scripts\python.exe`.

## Running

### Local stdio clients

Use stdio when the MCP client runs locally and can launch a subprocess server.
This includes local desktop apps, CLIs, IDEs, and custom local tools.

```bash
./venv/bin/python server.py
```

Example client configuration:

```json
{
  "command": "/absolute/path/to/randoku-sidecar/venv/bin/python",
  "args": ["/absolute/path/to/randoku-sidecar/server.py"]
}
```

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

Do not use `start.sh` for stdio clients; that script is the loopback
HTTP/tunnel launcher for hosted or remote clients.

### Loopback HTTP

HTTP mode is available for manual debugging, local HTTP clients, or as the
loopback server behind an HTTPS tunnel. It uses FastMCP streamable HTTP:

```bash
./venv/bin/python server.py --http --host 127.0.0.1 --port 7677
```

Local endpoint:

```text
http://127.0.0.1:7677/mcp
```

### Hosted or remote HTTPS clients

Hosted or remote clients need a reachable HTTPS MCP endpoint. They cannot
usually see your machine's `http://127.0.0.1:...`, and they usually cannot
launch a local subprocess. For this path, run the server on loopback and put
HTTPS/tunnel infrastructure in front of it. `start.sh` is this repository's
local launcher for that pattern:

```bash
./start.sh

# Equivalent server command:
./venv/bin/python server.py --http --host 127.0.0.1 --port 4750

# in another terminal:
cloudflared tunnel --url http://127.0.0.1:4750 --http-host-header 127.0.0.1:4750
```

`start.sh` defaults to `RANDOKU_OPERATOR_APPLY_MODE=dry_run`. During an
intentional development or maintenance session, run it with a direct override:

```bash
RANDOKU_OPERATOR_APPLY_MODE=direct ./start.sh
```

Configure the hosted/remote client for Streaming HTTP at
`https://<your-trycloudflare-host>/mcp`. Use No Authentication only for
temporary private testing until a real auth layer is added. ChatGPT developer
connectors are one example of this pattern; a custom hosted app or remote agent
can use the same `/mcp` endpoint shape. If a client only shows the old read-only
tool surface, reconnect or recreate the connector. Example setup scripts live
under [`examples/`](examples/).

## Default tool gates

Always-visible tools:

- `hermes_read_file(path, offset=1, limit=500)`
- `hermes_search_files(pattern, target="content", path=".", file_glob=None, limit=50)`
- `hermes_memory(action="search", target="memory", content=None, old_text=None)`
- `hermes_skill_list()`
- `hermes_skill_view(name)`

Opt-in capabilities (off by default):

| Capability | Env var | Default |
| --- | --- | --- |
| Write file and patch tools | `RANDOKU_ENABLE_WRITE=1` | Hidden |
| Session search | `RANDOKU_ENABLE_SESSION_SEARCH=1` | Hidden |
| Terminal command execution | `RANDOKU_ENABLE_TERMINAL=1` | Hidden |

Terminal execution timeout is capped at 120 seconds even when enabled. For tiered, auditable mutation, prefer the **Operator / Owner Mode** tools below over the broad enable flags.

`hermes_memory` search is always available. Its write actions (`add` / `replace` /
`remove`) are governed by Operator Mode — level `skills_config` with
`apply_mode=direct` and a per-call `dry_run=false`, the same tiered model as the
workspace mutation tools — and default to a dry-run plan. There is no separate
memory-write env flag.

### Semantic (external provider) memory

`hermes_memory` above is the flat-file `MEMORY.md` / `USER.md` layer. Separately,
the sidecar can reach the configured external memory provider
(`memory.provider`, e.g. honcho) through Hermes' neutral `MemoryManager` — the
sidecar names no provider in code, so swapping providers is a config change.

- `hermes_external_context_recall(query, ...)` — read-only auto-context prefetch
  from the provider.
- `hermes_memory_provider_writeback(tool, args, dry_run=True)` — governed,
  allowlisted proxy that persists a caller-distilled write (e.g. a conclusion)
  via the provider's own write tools. **Disabled by default**: only
  provider-native tool names listed in `RANDOKU_MEMORY_WRITEBACK_TOOLS`
  (comma-separated) are permitted, and a direct write requires operator level
  `skills_config` + `apply_mode=direct` + `dry_run=false`, emitting an audit
  record (provider, tool, arg keys, args length/sha256 — never raw content). The
  sidecar executes the caller's explicit write only; it does not decide what is
  worth remembering. See [`docs/memory-writeback-audit.md`](docs/memory-writeback-audit.md).

## Operator / Owner Mode

Operator / Owner Mode is a tiered control plane. Levels are ordered; each level includes the capabilities of every level above it.

| Level | Capabilities |
| --- | --- |
| `read_only` | status, policy, audit tail, cron list/status, skill diff/list/view, config get, env status, gateway status, git status/diff |
| `cron` | + cron run, pause, copy, move |
| `skills` | + skill create, edit, patch, write_file, copy, sync_to_default, delete |
| `skills_config` | + config set/patch, env set/copy (non-secret keys only) |
| `workspace` | + scoped workspace patch/write/apply_diff, test/lint allowlist, gateway restart |
| `owner` | + raw command, raw file patch/write — gated by explicit owner ack, still denies secret paths |

### Safety model

- **Read-only by default.** Mutating operator tools refuse unless operator mode is explicitly enabled.
- **Dry-run by default.** Even with operator mode enabled, every mutating tool defaults to `dry_run=True` and returns a plan. To mutate you must set `RANDOKU_OPERATOR_APPLY_MODE=direct` **and** pass `dry_run=False` to the call.
- **Owner Mode needs a second acknowledgement.** `RANDOKU_OPERATOR_LEVEL=owner` is not enough; you must also set `RANDOKU_OWNER_ACK=I_UNDERSTAND_THIS_CAN_MUTATE_MY_MACHINE` exactly, or owner tools refuse.
- **No secrets exposed.** Config `get` redacts secret-looking keys; `env` tools never return values; skill/cron prompts are surfaced only as `prompt_len` + `prompt_sha256`.
- **No raw secret-path access.** The denied-path policy refuses `.env`, `auth.json`, `mcp-tokens/`, `.ssh/`, `.aws/`, `vault/`, and any secret-looking filename — even in Owner Mode.
- **Fail-closed path scoping.** Workspace reads, writes, and the git status/diff tools all require `RANDOKU_OPERATOR_ALLOWED_PATHS` to be set and the target under it; an empty allow-list refuses uniformly. `git diff` additionally refuses a secret-like `pathspec`.
- **No `shell=True` anywhere.** Every subprocess uses `shell=False` with a fixed argv.
- **No destructive git/filesystem ops.** Workspace `run_test` allows only a conservative allowlist (pytest, ruff, mypy, npm test/lint, git status/diff). Owner `run_command` blocks catastrophic patterns (`rm -rf /`, `del /s`, `format`, `curl | bash`, `git push --force`, `git add -A/.`, anything touching `.env`/`vault`/`token`/`.ssh`).

### Env flags

| Env var | Default | Purpose |
| --- | --- | --- |
| `RANDOKU_OPERATOR_ENABLED` | unset (false) | Enable operator mode |
| `RANDOKU_OPERATOR_LEVEL` | `read_only` | Operator level (see table above) |
| `RANDOKU_OPERATOR_APPLY_MODE` | `dry_run` | `dry_run` returns plans; `direct` allows mutation |
| `RANDOKU_OPERATOR_ALLOWED_PROFILES` | `default` | Comma-separated profile names, or `*` for all existing |
| `RANDOKU_OPERATOR_ALLOWED_PATHS` | empty | Comma-separated workspace root paths; empty disables workspace reads, writes, and git tools (fail-closed) |
| `RANDOKU_OPERATOR_DENIED_PATHS` | built-in defaults | Extra denied paths (additions only; cannot weaken defaults) |
| `RANDOKU_OWNER_ACK` | unset | Must equal `I_UNDERSTAND_THIS_CAN_MUTATE_MY_MACHINE` for owner tools |

### Examples

Read-only default (no env vars needed):

```bash
randoku-sidecar
```

Skills/config dry-run:

```bash
export RANDOKU_OPERATOR_ENABLED=1
export RANDOKU_OPERATOR_LEVEL=skills_config
export RANDOKU_OPERATOR_APPLY_MODE=dry_run
export RANDOKU_OPERATOR_ALLOWED_PROFILES=default
randoku-sidecar
```

Workspace direct with an allowed path:

```bash
export RANDOKU_OPERATOR_ENABLED=1
export RANDOKU_OPERATOR_LEVEL=workspace
export RANDOKU_OPERATOR_APPLY_MODE=direct
export RANDOKU_OPERATOR_ALLOWED_PATHS="$HOME/Projects/randoku-sidecar"
randoku-sidecar
```

Owner Mode (**WARNING: can mutate your machine**):

```bash
export RANDOKU_OPERATOR_ENABLED=1
export RANDOKU_OPERATOR_LEVEL=owner
export RANDOKU_OPERATOR_APPLY_MODE=direct
export RANDOKU_OWNER_ACK=I_UNDERSTAND_THIS_CAN_MUTATE_MY_MACHINE
randoku-sidecar
```

On Windows PowerShell, set variables with `$env:RANDOKU_OPERATOR_ENABLED="1"` etc.

### Audit log

Every mutating tool call appends a JSONL record. The preferred location is the Hermes data-root logs directory; if that is not writable, it falls back to `<repo>/logs/randoku_operator_audit.jsonl`.

Each record contains: `timestamp`, `tool`, `level`, `apply_mode`, `dry_run`, `success`, `changed`, `summary`, `error`, profile(s), path summary, job_id / skill_name / key (when relevant), and `prompt_len` + `prompt_sha256` / `content_len` + `content_sha256` for skill/cron content. The audit log **never** records full prompts, full config values, raw `.env` contents, vault contents, or command output likely to contain secrets. Read it with the `hermes_operator_audit_tail` tool.

## Remote profile

`--profile remote` is intentionally blocked because authentication is not implemented:

```bash
./venv/bin/python server.py --http --profile remote
```

For temporary experiments only, you can bypass the block with both an env flag and an explicit CLI ack:

```bash
RANDOKU_UNSAFE_REMOTE_NOAUTH=1 \
  ./venv/bin/python server.py --http --profile remote --i-understand-this-is-unsafe
```

Do not use this bypass for anything but throwaway local testing.

## Development & release checklist

```bash
./venv/bin/python -m pytest          # full suite
./venv/bin/python -m py_compile server.py
```

Before publishing:

- `./venv/bin/python -m pytest` passes.
- `./venv/bin/python -m py_compile server.py` passes.
- Interpreter is Python 3.10+ (matches `requires-python`).
- No `*.pem`, `*.log` / `*.err.log`, `__pycache__/`, or `*.pyc` files.
- Server binds to loopback by default.
- Terminal, write tools, memory writes, and session search are disabled by default.

## Capability notes

Capability contracts for the workspace tools live under [`docs/capabilities/`](docs/capabilities/), each paired with tests in `test_operator_*.py`. See [`FEASIBILITY.md`](FEASIBILITY.md) for the original probe details and exact Hermes Agent signatures, and [`ROADMAP.md`](ROADMAP.md) for the phased plan.

## License

MIT. The original `hermes-gpt` copyright and this project's copyright are both retained — see [`LICENSE`](LICENSE) and [`docs/ATTRIBUTION.md`](docs/ATTRIBUTION.md).
