"""Workspace, gateway, git, and owner-mode tools for randoku-sidecar.

Tools:
- ``hermes_gateway_status``        : read_only  — gateway / ticker / adapter health
- ``hermes_gateway_restart``       : workspace  — fixed-argv gateway restart
- ``hermes_workspace_read``        : read_only  — read file with operator path policy
- ``hermes_workspace_patch``       : workspace  — find-and-replace within an allowed path
- ``hermes_workspace_write_file``  : workspace  — write file within an allowed path
- ``hermes_workspace_run_test``    : workspace  — conservative allowlist of test/lint commands
- ``hermes_codegraph_status``      : read_only  — CodeGraph index status for an allowed repo
- ``hermes_codegraph_files``       : read_only  — CodeGraph indexed file structure
- ``hermes_codegraph_query``       : read_only  — CodeGraph symbol search
- ``hermes_codegraph_explore``     : read_only  — CodeGraph source + call-path exploration
- ``hermes_codegraph_node``        : read_only  — CodeGraph symbol/file inspection
- ``hermes_git_status``            : read_only  — git status in a workdir
- ``hermes_git_diff``              : read_only  — git diff in a workdir
- ``hermes_owner_run_command``     : owner      — arbitrary command (with catastrophic blocks)
- ``hermes_owner_patch``           : owner      — arbitrary file patch (still denies secret paths)
- ``hermes_owner_write_file``      : owner      — arbitrary file write (still denies secret paths)
- ``hermes_owner_repo_issue_create``: owner      — governed recipe: dry-run plan for a public-safe
  GitHub issue (see docs/owner-mode-governance.md, docs/public-issue-sanitization.md).
  Phase 4A: dry-run plan only, no ``gh issue create`` execution yet.

Safety rules:
- No shell=True anywhere. Ever.
- Workspace path tools require path under allowed_paths AND not denied.
- Owner tools require explicit owner ack AND direct mode AND dry_run=false.
- Owner tools still deny secret paths (no secret override in this PR).
- Workspace run_test only allows a conservative command allowlist. Rejects
  git add/commit/push, rm, del, powershell, curl, wget, bash -c, cmd /c,
  pipes, redirects, semicolons, ampersands, encoded commands.
- Owner run_command blocks obvious catastrophic patterns: rm -rf /, del /s,
  format, powershell -EncodedCommand, curl|bash, wget|bash, git push --force,
  git add -A, anything touching .env/vault/token/ssh paths.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import time
from pathlib import Path
from typing import Any, Optional

import operator_policy as op


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp, path)


def _is_git_worktree(path: Path) -> bool:
    """Return True when path is inside a git worktree.

    This intentionally uses filesystem markers only. It avoids invoking git so
    the backup policy stays cheap, deterministic, and shell-free.
    """
    start = path if path.is_dir() else path.parent
    for candidate in (start, *start.parents):
        dotgit = candidate / ".git"
        if dotgit.exists():
            return True
    return False


def _should_backup_file(path: Path) -> tuple[bool, str]:
    """Decide whether a file-level .bak backup should be created.

    Git worktrees already provide rollback through git diff/restore/reflog, and
    extra .bak files pollute repository status. Non-git workspaces keep the old
    conservative backup behavior.
    """
    if _is_git_worktree(path):
        return (False, "git_worktree")
    return (True, "non_git_workspace")


def _backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = path.with_name(f"{path.name}.bak.{ts}")
    try:
        shutil.copy2(path, bak)
        return bak
    except OSError:
        return None


def _maybe_backup_file(path: Path) -> tuple[Path | None, str]:
    should_backup, reason = _should_backup_file(path)
    if not should_backup:
        return (None, reason)
    return (_backup_file(path), reason)


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


def _gateway_pid_path(profile_home: Path) -> Path:
    return profile_home / "gateway.pid"


def _gateway_state_path(profile_home: Path) -> Path:
    return profile_home / "gateway_state.json"


def _parse_gateway_pid_text(raw: str) -> tuple[int | None, str | None]:
    text = raw.strip()
    if not text:
        return (None, None)
    try:
        return (int(text), "pid_file_text")
    except ValueError:
        pass
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return (None, None)
    if not isinstance(payload, dict):
        return (None, None)
    pid = payload.get("pid")
    if isinstance(pid, int):
        return (pid, "pid_file_json")
    if isinstance(pid, str):
        try:
            return (int(pid.strip()), "pid_file_json")
        except ValueError:
            return (None, None)
    return (None, None)


def _pid_exists(pid: int) -> bool:
    try:
        import psutil  # type: ignore

        return psutil.pid_exists(pid)
    except ImportError:
        # Fall back to OS kill 0 probe.
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False
        except Exception:
            return False


def _state_pid(state: dict[str, Any]) -> int | None:
    pid = state.get("pid")
    if isinstance(pid, int):
        return pid
    if isinstance(pid, str):
        try:
            return int(pid.strip())
        except ValueError:
            return None
    return None


def _gateway_adapters_summary(state: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    # Current Hermes schema: {"platforms": {"telegram": {"state": "connected"}}}
    platforms = state.get("platforms")
    if isinstance(platforms, dict):
        adapters: list[dict[str, Any]] = []
        for name, entry in sorted(platforms.items()):
            if not isinstance(entry, dict):
                continue
            platform_state = entry.get("state")
            adapters.append(
                {
                    "name": str(name),
                    "connected": platform_state == "connected",
                    "state": platform_state,
                    "updated_at": entry.get("updated_at"),
                    "error_code": entry.get("error_code"),
                }
            )
        return (adapters, "platforms")

    # Legacy upstream-derived schema: {"telegram": {"connected": true}}
    adapters = []
    for key in ("telegram", "discord", "slack", "signal", "whatsapp", "api_server"):
        entry = state.get(key)
        if isinstance(entry, dict):
            adapters.append(
                {
                    "name": key,
                    "connected": bool(entry.get("connected", False)),
                }
            )
    return (adapters, "legacy_top_level")


def hermes_gateway_status(
    profile: str = "default",
    hermes_root: Path | None = None,
) -> str:
    try:
        policy = op.OperatorPolicy()
        policy.require_profile(profile, hermes_root)
        profile_home = op.resolve_profile_home(profile, hermes_root)

        pid_path = _gateway_pid_path(profile_home)
        state_path = _gateway_state_path(profile_home)
        pid = None
        pid_source = None
        if pid_path.exists():
            try:
                pid, pid_source = _parse_gateway_pid_text(pid_path.read_text(encoding="utf-8"))
            except OSError:
                pid = None

        state: dict[str, Any] = {}
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = {}

        if pid is None:
            pid = _state_pid(state)
            if pid is not None:
                pid_source = "gateway_state"

        running = _pid_exists(pid) if pid is not None else False

        # Cron ticker heartbeat.
        cron_dir = profile_home / "cron"
        ticker_heartbeat = None
        hb_path = cron_dir / "ticker_heartbeat"
        if hb_path.exists():
            try:
                ticker_heartbeat = hb_path.stat().st_mtime
            except OSError:
                ticker_heartbeat = None

        # Adapter connection info: surface status booleans and non-secret
        # runtime metadata only, no tokens.
        adapters_summary, state_schema = _gateway_adapters_summary(state)

        result = {
            "success": True,
            "profile": profile,
            "gateway_pid": pid,
            "pid_source": pid_source,
            "gateway_running": running,
            "gateway_state": state.get("gateway_state"),
            "state_updated_at": state.get("updated_at"),
            "exit_reason": state.get("exit_reason"),
            "state_schema": state_schema,
            "ticker_heartbeat_mtime": ticker_heartbeat,
            "adapters": adapters_summary,
        }
        return json.dumps(result, indent=2)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


def _hermes_argv(profile: str, sub: list[str]) -> list[str]:
    if profile == "default":
        return ["hermes", *sub]
    return ["hermes", "-p", profile, *sub]


def hermes_gateway_restart(
    profile: str = "default",
    dry_run: bool = True,
    hermes_root: Path | None = None,
    runner=None,
) -> str:
    try:
        policy = op.OperatorPolicy()
        policy.require_level("workspace")
        policy.require_profile(profile, hermes_root)
        argv = _hermes_argv(profile, ["gateway", "restart"])

        if policy.effective_dry_run(dry_run):
            plan = {
                "would_restart": True,
                "argv": argv,
                "shell": False,
                "profile": profile,
            }
            op.audit_record(
                tool="hermes_gateway_restart",
                level=policy.level,
                apply_mode=policy.apply_mode,
                dry_run=True,
                success=True,
                changed=False,
                summary="dry-run plan",
                profile=profile,
            )
            return json.dumps({"success": True, "dry_run": True, "plan": plan}, indent=2)

        policy.require_mutation(dry_run)
        run_fn = runner or op.run_argv
        rc, out, err = run_fn(argv, timeout=120, workdir=None)
        result = {
            "success": rc == 0,
            "dry_run": False,
            "returncode": rc,
            "stdout": op.redact_output(out),
            "stderr": op.redact_output(err),
            "note": (
                "If hermes does not support 'gateway restart', this command "
                "may have failed. Try 'hermes gateway stop' + 'hermes gateway start' "
                "manually."
            ),
        }
        op.audit_record(
            tool="hermes_gateway_restart",
            level=policy.level,
            apply_mode=policy.apply_mode,
            dry_run=False,
            success=rc == 0,
            changed=True,
            summary=f"rc={rc}",
            profile=profile,
            error=op.redact_output(err) if rc != 0 else "",
        )
        return json.dumps(result, indent=2)
    except Exception as exc:
        op.audit_record(
            tool="hermes_gateway_restart",
            level="unknown",
            apply_mode="unknown",
            dry_run=dry_run,
            success=False,
            error=str(exc),
            profile=profile,
        )
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


# ---------------------------------------------------------------------------
# Workspace read / patch / write_file / run_test
# ---------------------------------------------------------------------------


def hermes_workspace_read(
    path: str,
    offset: int = 1,
    limit: int = 500,
) -> str:
    """Read a file. Read-only but applies operator path policy (deny secrets)."""
    try:
        policy = op.OperatorPolicy()
        # Fail-closed: a workspace read requires a configured allow-list and a
        # non-denied path, identical to the write tools. The basic
        # hermes_read_file tool remains available for unscoped reads.
        policy.require_workspace_read_path(path)
        p = op._normalize_path(path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        start = max(1, int(offset)) - 1
        end = start + max(1, int(limit))
        chunk = "".join(lines[start:end])
        result = {
            "success": True,
            "path": str(p),
            "offset": start + 1,
            "limit": end - start,
            "total_lines": len(lines),
            "content": chunk,
        }
        return json.dumps(result, indent=2)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


def _parse_hunk_header(header: str) -> tuple[int, int, int, int]:
    """Parse a unified diff hunk header.

    Returns (old_start, old_count, new_start, new_count), using 1-based line
    numbers from the diff format.
    """
    match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header)
    if not match:
        raise ValueError(f"Invalid unified diff hunk header: {header!r}")
    old_start = int(match.group(1))
    old_count = int(match.group(2) or "1")
    new_start = int(match.group(3))
    new_count = int(match.group(4) or "1")
    return (old_start, old_count, new_start, new_count)


def _extract_unified_diff_hunks(diff_text: str) -> list[list[str]]:
    """Extract strict unified diff hunks for a single-file patch.

    File headers are ignored. Multi-file diffs and unsupported metadata are
    rejected by refusing a second ---/+++ file header pair after hunks begin.
    """
    if not diff_text or not diff_text.strip():
        raise ValueError("diff is required.")
    lines = diff_text.splitlines(keepends=True)
    hunks: list[list[str]] = []
    current: list[str] | None = None
    seen_hunk = False
    file_header_pairs = 0
    git_diff_headers = 0
    saw_minus_header = False

    for line in lines:
        if line.startswith("diff --git "):
            if seen_hunk:
                raise ValueError("multi-file diffs are not supported.")
            git_diff_headers += 1
            if git_diff_headers > 1:
                raise ValueError("multi-file diffs are not supported.")
            continue
        if line.startswith("index "):
            if seen_hunk:
                raise ValueError("unsupported git diff metadata after hunks.")
            continue
        if line.startswith(("new file mode ", "deleted file mode ", "rename from ", "rename to ", "Binary files ")):
            raise ValueError("file creation, deletion, rename, and binary diffs are not supported.")
        if line.startswith("--- "):
            if seen_hunk:
                raise ValueError("multi-file diffs are not supported.")
            saw_minus_header = True
            continue
        if line.startswith("+++ "):
            if seen_hunk:
                raise ValueError("multi-file diffs are not supported.")
            if saw_minus_header:
                file_header_pairs += 1
                saw_minus_header = False
                if file_header_pairs > 1:
                    raise ValueError("multi-file diffs are not supported.")
            continue
        if line.startswith("@@ "):
            seen_hunk = True
            if current is not None:
                hunks.append(current)
            current = [line]
            continue
        if current is not None:
            if line.startswith((" ", "+", "-", "\\")):
                current.append(line)
                continue
            raise ValueError(f"Unsupported unified diff line: {line!r}")
        if line.strip():
            # Permit only empty preamble before the first hunk.
            raise ValueError(f"Unsupported unified diff preamble line: {line!r}")

    if current is not None:
        hunks.append(current)
    if not hunks:
        raise ValueError("diff does not contain any unified diff hunks.")
    return hunks


def _apply_unified_diff_to_text(content: str, diff_text: str) -> tuple[str, int]:
    """Apply a strict single-file unified diff to text.

    Context and removal lines must match exactly. No fuzzy matching is allowed.
    """
    source = content.splitlines(keepends=True)
    output: list[str] = []
    cursor = 0
    hunks = _extract_unified_diff_hunks(diff_text)

    for hunk in hunks:
        old_start, _old_count, _new_start, _new_count = _parse_hunk_header(hunk[0].rstrip("\n"))
        hunk_index = max(0, old_start - 1)
        if hunk_index < cursor:
            raise ValueError("overlapping or out-of-order hunks are not supported.")
        output.extend(source[cursor:hunk_index])
        cursor = hunk_index

        for line in hunk[1:]:
            if line.startswith("\\"):
                # "\\ No newline at end of file" marker. Keep behavior strict and
                # ignore the marker; the adjacent line content carries the state.
                continue
            marker = line[:1]
            text = line[1:]
            if marker == " ":
                if cursor >= len(source) or source[cursor] != text:
                    raise ValueError(f"context mismatch near hunk starting at line {old_start}.")
                output.append(source[cursor])
                cursor += 1
            elif marker == "-":
                if cursor >= len(source) or source[cursor] != text:
                    raise ValueError(f"removal mismatch near hunk starting at line {old_start}.")
                cursor += 1
            elif marker == "+":
                output.append(text)
            else:
                raise ValueError(f"Unsupported unified diff marker: {marker!r}")

    output.extend(source[cursor:])
    return ("".join(output), len(hunks))


def hermes_workspace_apply_diff(
    path: str,
    diff: str,
    dry_run: bool = True,
) -> str:
    try:
        policy = op.OperatorPolicy()
        policy.require_level("workspace")
        policy.require_workspace_path(path)
        if not diff or not diff.strip():
            raise ValueError("diff is required.")

        p = op._normalize_path(path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        content = p.read_text(encoding="utf-8", errors="replace")
        new_content, hunk_count = _apply_unified_diff_to_text(content, diff)
        preview_diff = op.unified_diff(content, new_content, label=p.name)
        backup_would_create, backup_policy = _should_backup_file(p)
        rollback_hint = f"git restore {p}" if backup_policy == "git_worktree" else "restore from backup"

        if policy.effective_dry_run(dry_run):
            plan = {
                "would_apply": True,
                "path": str(p),
                "hunk_count": hunk_count,
                "diff": preview_diff,
                "backup_policy": backup_policy,
                "backup_would_create": backup_would_create,
                "rollback_hint": rollback_hint,
            }
            op.audit_record(
                tool="hermes_workspace_apply_diff",
                level=policy.level,
                apply_mode=policy.apply_mode,
                dry_run=True,
                success=True,
                changed=False,
                summary=f"dry-run apply_diff hunks={hunk_count}",
                path=str(p),
                content=diff,
                extra={"hunk_count": hunk_count, "backup_policy": backup_policy},
            )
            return json.dumps({"success": True, "dry_run": True, "plan": plan}, indent=2)

        policy.require_mutation(dry_run)
        backup, backup_policy = _maybe_backup_file(p)
        _atomic_write_text(p, new_content)
        result = {
            "success": True,
            "dry_run": False,
            "path": str(p),
            "hunk_count": hunk_count,
            "backup": str(backup) if backup else None,
            "backup_policy": backup_policy,
            "rollback_hint": rollback_hint,
        }
        op.audit_record(
            tool="hermes_workspace_apply_diff",
            level=policy.level,
            apply_mode=policy.apply_mode,
            dry_run=False,
            success=True,
            changed=True,
            summary=f"applied diff to {p.name} ({hunk_count} hunk(s))",
            path=str(p),
            content=diff,
            extra={"hunk_count": hunk_count, "backup_policy": backup_policy},
        )
        return json.dumps(result, indent=2)
    except Exception as exc:
        op.audit_record(
            tool="hermes_workspace_apply_diff",
            level="unknown",
            apply_mode="unknown",
            dry_run=dry_run,
            success=False,
            error=str(exc),
            path=path,
            content=diff,
        )
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


def hermes_workspace_patch(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    dry_run: bool = True,
) -> str:
    try:
        policy = op.OperatorPolicy()
        policy.require_level("workspace")
        policy.require_workspace_path(path)
        if not old_string:
            raise ValueError("old_string is required.")
        if new_string is None:
            raise ValueError("new_string is required.")

        p = op._normalize_path(path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        content = p.read_text(encoding="utf-8", errors="replace")
        if old_string not in content:
            raise ValueError("old_string not found in file.")
        if not replace_all and content.count(old_string) > 1:
            raise ValueError(
                "old_string matches multiple locations. Provide more context "
                "or set replace_all=True."
            )
        if replace_all:
            new_content = content.replace(old_string, new_string)
            match_count = content.count(old_string)
        else:
            new_content = content.replace(old_string, new_string, 1)
            match_count = 1

        if policy.effective_dry_run(dry_run):
            plan = {
                "would_patch": True,
                "path": str(p),
                "match_count": match_count,
                "diff": op.unified_diff(content, new_content, label=p.name),
            }
            op.audit_record(
                tool="hermes_workspace_patch",
                level=policy.level,
                apply_mode=policy.apply_mode,
                dry_run=True,
                success=True,
                changed=False,
                summary="dry-run plan",
                path=str(p),
            )
            return json.dumps({"success": True, "dry_run": True, "plan": plan}, indent=2)

        policy.require_mutation(dry_run)
        backup, backup_policy = _maybe_backup_file(p)
        _atomic_write_text(p, new_content)
        result = {
            "success": True,
            "dry_run": False,
            "path": str(p),
            "match_count": match_count,
            "backup": str(backup) if backup else None,
            "backup_policy": backup_policy,
        }
        op.audit_record(
            tool="hermes_workspace_patch",
            level=policy.level,
            apply_mode=policy.apply_mode,
            dry_run=False,
            success=True,
            changed=True,
            summary=f"patched {p.name} ({match_count} replacement(s))",
            path=str(p),
        )
        return json.dumps(result, indent=2)
    except Exception as exc:
        op.audit_record(
            tool="hermes_workspace_patch",
            level="unknown",
            apply_mode="unknown",
            dry_run=dry_run,
            success=False,
            error=str(exc),
            path=path,
        )
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


def hermes_workspace_write_file(
    path: str,
    content: str,
    dry_run: bool = True,
) -> str:
    try:
        policy = op.OperatorPolicy()
        policy.require_level("workspace")
        policy.require_workspace_path(path)
        if content is None:
            raise ValueError("content is required.")

        p = op._normalize_path(path)
        if policy.effective_dry_run(dry_run):
            plan = {
                "would_write": True,
                "path": str(p),
                "exists": p.exists(),
                "content_len": len(content),
            }
            op.audit_record(
                tool="hermes_workspace_write_file",
                level=policy.level,
                apply_mode=policy.apply_mode,
                dry_run=True,
                success=True,
                changed=False,
                summary="dry-run plan",
                path=str(p),
            )
            return json.dumps({"success": True, "dry_run": True, "plan": plan}, indent=2)

        policy.require_mutation(dry_run)
        backup, backup_policy = _maybe_backup_file(p) if p.exists() else (None, "new_file")
        _atomic_write_text(p, content)
        result = {
            "success": True,
            "dry_run": False,
            "path": str(p),
            "backup": str(backup) if backup else None,
            "backup_policy": backup_policy,
        }
        op.audit_record(
            tool="hermes_workspace_write_file",
            level=policy.level,
            apply_mode=policy.apply_mode,
            dry_run=False,
            success=True,
            changed=True,
            summary=f"wrote {p.name}",
            path=str(p),
        )
        return json.dumps(result, indent=2)
    except Exception as exc:
        op.audit_record(
            tool="hermes_workspace_write_file",
            level="unknown",
            apply_mode="unknown",
            dry_run=dry_run,
            success=False,
            error=str(exc),
            path=path,
        )
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


# Allowlist for run_test. Each entry is a tuple of (argv_prefix, max_args).
# The prefix must match exactly; the rest is bounded.
_TEST_COMMAND_ALLOWLIST: tuple[tuple[tuple[str, ...], int], ...] = (
    (("pytest",), 8),
    (("python", "-m", "pytest"), 8),
    (("npm", "test"), 4),
    (("npm", "run", "test"), 4),
    (("npm", "run", "lint"), 4),
    (("ruff", "check"), 4),
    (("mypy",), 4),
    (("git", "status"), 4),
    (("git", "diff"), 4),
)

# Substrings that mark a command as dangerous and must be refused.
_DANGEROUS_PATTERNS: tuple[str, ...] = (
    "rm ",
    "del ",
    "format ",
    "powershell",
    "curl ",
    "wget ",
    "bash -c",
    "cmd /c",
    "git add",
    "git commit",
    "git push",
    "|",
    ">",
    "<",
    ";",
    "&",
    "EncodedCommand",
    "||",
    "&&",
    "`",
    "$(",
)


def _is_repo_local_python(argv0: str) -> bool:
    """Return true for repo-local virtualenv Python launchers only."""
    normalized = argv0.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if (
        normalized.startswith("/")
        or normalized.startswith("../")
        or ":" in normalized
    ):
        return False
    return normalized in {
        "venv/bin/python",
        "venv/bin/python3",
        ".venv/bin/python",
        ".venv/bin/python3",
        "venv/Scripts/python.exe",
        ".venv/Scripts/python.exe",
    }


def _is_allowed_test_command(argv: list[str]) -> tuple[bool, str]:
    """Check whether argv matches the test/lint allowlist."""
    if not argv:
        return (False, "Empty command.")
    cmd = " ".join(argv)
    cmd_lower = cmd.lower()
    # Reject dangerous substrings first.
    for needle in _DANGEROUS_PATTERNS:
        if needle.lower() in cmd_lower:
            return (False, f"Command contains forbidden substring {needle!r}.")
    if len(argv) >= 3 and _is_repo_local_python(argv[0]) and argv[1:3] == ["-m", "pytest"]:
        extra = argv[3:]
        max_extra = 8
        if len(extra) > max_extra:
            return (False, "Too many arguments for repo-local virtualenv pytest.")
        return (True, "")
    for prefix, max_extra in _TEST_COMMAND_ALLOWLIST:
        if len(argv) >= len(prefix) and tuple(argv[: len(prefix)]) == prefix:
            extra = argv[len(prefix) :]
            if len(extra) > max_extra:
                return (False, f"Too many arguments for {prefix!r}.")
            return (True, "")
    return (False, "Command not in the test/lint allowlist.")


def _workspace_test_status(returncode: int) -> str:
    """Return the capability status for a completed test process."""
    if returncode == 0:
        return "pass"
    if returncode == 124:
        return "timeout"
    return "fail"


def _effective_test_timeout(timeout: int) -> int:
    """Clamp test timeout to the same bound used by op.run_argv."""
    return max(1, min(int(timeout), 600))


def hermes_workspace_run_test(
    command: str,
    workdir: str | None = None,
    timeout: int = 120,
    dry_run: bool = True,
    runner=None,
) -> str:
    try:
        policy = op.OperatorPolicy()
        policy.require_level("workspace")
        command_mode = "legacy_command"
        if not command or not command.strip():
            raise ValueError("command is required.")
        effective_timeout = _effective_test_timeout(timeout)

        # Parse with shlex so we never invoke a shell.
        try:
            argv = shlex.split(command, posix=(os.name != "nt"))
        except ValueError as exc:
            raise ValueError(f"Could not parse command: {exc}") from exc

        allowed, reason = _is_allowed_test_command(argv)
        if not allowed:
            raise PermissionError(reason)

        if not workdir:
            raise PermissionError("workdir is required for workspace test execution.")
        if op.is_denied_path(workdir):
            raise PermissionError(
                f"workdir {workdir!r} is denied by the operator path safety policy."
            )
        if not policy.allowed_paths:
            raise PermissionError(
                "Workspace test execution is disabled because "
                f"{op.OPERATOR_ALLOWED_PATHS_ENV} is empty. Set it to one or more "
                "workspace root directories."
            )
        if not op.path_under_allowed(workdir, policy.allowed_paths):
            raise PermissionError(
                f"workdir {workdir!r} is not under any allowed path."
            )
        normalized_workdir = op._normalize_path(workdir)
        if not normalized_workdir.exists() or not normalized_workdir.is_dir():
            raise FileNotFoundError(f"workdir not found or not a directory: {workdir}")
        workdir_text = str(normalized_workdir)

        if policy.effective_dry_run(dry_run):
            plan = {
                "would_run": True,
                "status": "dry_run",
                "command_mode": command_mode,
                "argv": argv,
                "shell": False,
                "workdir": workdir_text,
                "timeout": effective_timeout,
            }
            op.audit_record(
                tool="hermes_workspace_run_test",
                level=policy.level,
                apply_mode=policy.apply_mode,
                dry_run=True,
                success=True,
                changed=False,
                summary="dry-run plan",
                path=workdir_text,
                extra={
                    "status": "dry_run",
                    "command_mode": command_mode,
                    "argv": argv,
                    "timeout": effective_timeout,
                },
            )
            return json.dumps({"success": True, "dry_run": True, "status": "dry_run", "plan": plan}, indent=2)

        policy.require_mutation(dry_run)
        run_fn = runner or op.run_argv
        rc, out, err = run_fn(argv, timeout=effective_timeout, workdir=workdir_text)
        status = _workspace_test_status(rc)
        redacted_stdout = op.redact_output(out)
        redacted_stderr = op.redact_output(err)
        result = {
            "success": status == "pass",
            "dry_run": False,
            "status": status,
            "exit_code": rc,
            "returncode": rc,
            "argv": argv,
            "workdir": workdir_text,
            "timeout": effective_timeout,
            "stdout": redacted_stdout,
            "stderr": redacted_stderr,
        }
        op.audit_record(
            tool="hermes_workspace_run_test",
            level=policy.level,
            apply_mode=policy.apply_mode,
            dry_run=False,
            success=status == "pass",
            changed=False,  # test runs are not file mutations
            summary=f"status={status} rc={rc} argv={argv}",
            path=workdir_text,
            error=redacted_stderr if status != "pass" else "",
            extra={
                "status": status,
                "command_mode": command_mode,
                "argv": argv,
                "timeout": effective_timeout,
                "exit_code": rc,
            },
        )
        return json.dumps(result, indent=2)
    except Exception as exc:
        op.audit_record(
            tool="hermes_workspace_run_test",
            level="unknown",
            apply_mode="unknown",
            dry_run=dry_run,
            success=False,
            error=str(exc),
            path=workdir or "",
            extra={
                "status": "blocked",
                "command_mode": "legacy_command",
                "refusal_reason": str(exc),
            },
        )
        return json.dumps({"success": False, "dry_run": dry_run, "status": "blocked", "error": str(exc)}, indent=2)


# ---------------------------------------------------------------------------
# CodeGraph
# ---------------------------------------------------------------------------

_CODEGRAPH_READ_COMMANDS = {"status", "files", "query", "explore", "node"}
_CODEGRAPH_MAX_OUTPUT_CHARS = 50000


def _truncate_tool_output(text: str, limit: int = _CODEGRAPH_MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return (text, False)
    return (text[:limit] + "\n... <truncated>", True)


def _require_codegraph_workdir(workdir: str) -> Path:
    if not workdir:
        raise ValueError("workdir is required.")
    if op.is_denied_path(workdir):
        raise PermissionError(
            f"workdir {workdir!r} is denied by the operator path safety policy."
        )
    policy = op.OperatorPolicy()
    if not policy.allowed_paths:
        raise PermissionError(
            "CodeGraph tools are disabled because "
            f"{op.OPERATOR_ALLOWED_PATHS_ENV} is empty. Set it to one or more "
            "workspace root directories."
        )
    if not op.path_under_allowed(workdir, policy.allowed_paths):
        raise PermissionError(f"workdir {workdir!r} is not under any allowed path.")
    normalized = op._normalize_path(workdir)
    if not normalized.exists() or not normalized.is_dir():
        raise FileNotFoundError(f"workdir not found or not a directory: {workdir}")
    return normalized


def _codegraph_executable() -> str:
    exe = shutil.which("codegraph")
    if not exe:
        raise FileNotFoundError("codegraph executable not found in PATH.")
    return exe


def _run_codegraph(argv: list[str], workdir: Path, timeout: int = 60, runner=None) -> str:
    command = argv[1] if len(argv) > 1 else ""
    if command not in _CODEGRAPH_READ_COMMANDS:
        raise PermissionError(f"CodeGraph command {command!r} is not allowed.")
    effective_timeout = max(1, min(int(timeout), 120))
    run_fn = runner or op.run_argv
    rc, out, err = run_fn(argv, timeout=effective_timeout, workdir=str(workdir))
    stdout, stdout_truncated = _truncate_tool_output(op.redact_output(out))
    stderr, stderr_truncated = _truncate_tool_output(op.redact_output(err))
    return json.dumps(
        {
            "success": rc == 0,
            "command": command,
            "argv": argv,
            "workdir": str(workdir),
            "timeout": effective_timeout,
            "exit_code": rc,
            "returncode": rc,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": stdout_truncated or stderr_truncated,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        },
        indent=2,
    )


def hermes_codegraph_status(workdir: str, timeout: int = 60, runner=None) -> str:
    try:
        normalized = _require_codegraph_workdir(workdir)
        argv = [_codegraph_executable(), "status", str(normalized)]
        return _run_codegraph(argv, normalized, timeout=timeout, runner=runner)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


def hermes_codegraph_files(
    workdir: str,
    filter: str | None = None,
    pattern: str | None = None,
    format: str = "tree",
    max_depth: int | None = None,
    no_metadata: bool = False,
    json_output: bool = False,
    timeout: int = 60,
    runner=None,
) -> str:
    try:
        normalized = _require_codegraph_workdir(workdir)
        if format not in {"tree", "flat", "grouped"}:
            raise ValueError("format must be one of: tree, flat, grouped.")
        argv = [_codegraph_executable(), "files", "-p", str(normalized), "--format", format]
        if filter:
            argv.extend(["--filter", filter])
        if pattern:
            argv.extend(["--pattern", pattern])
        if max_depth is not None:
            argv.extend(["--max-depth", str(max(1, min(int(max_depth), 20)))])
        if no_metadata:
            argv.append("--no-metadata")
        if json_output:
            argv.append("--json")
        return _run_codegraph(argv, normalized, timeout=timeout, runner=runner)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


def hermes_codegraph_query(
    workdir: str,
    search: str,
    limit: int = 10,
    kind: str | None = None,
    json_output: bool = False,
    timeout: int = 60,
    runner=None,
) -> str:
    try:
        normalized = _require_codegraph_workdir(workdir)
        if not search or not search.strip():
            raise ValueError("search is required.")
        argv = [
            _codegraph_executable(),
            "query",
            "-p",
            str(normalized),
            "-l",
            str(max(1, min(int(limit), 100))),
        ]
        if kind:
            argv.extend(["-k", kind])
        if json_output:
            argv.append("--json")
        argv.append(search)
        return _run_codegraph(argv, normalized, timeout=timeout, runner=runner)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


def hermes_codegraph_explore(
    workdir: str,
    query: str,
    max_files: int = 5,
    timeout: int = 60,
    runner=None,
) -> str:
    try:
        normalized = _require_codegraph_workdir(workdir)
        if not query or not query.strip():
            raise ValueError("query is required.")
        argv = [
            _codegraph_executable(),
            "explore",
            "-p",
            str(normalized),
            "--max-files",
            str(max(1, min(int(max_files), 20))),
            query,
        ]
        return _run_codegraph(argv, normalized, timeout=timeout, runner=runner)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


def hermes_codegraph_node(
    workdir: str,
    name: str,
    file: str | None = None,
    offset: int | None = None,
    limit: int | None = None,
    symbols_only: bool = False,
    timeout: int = 60,
    runner=None,
) -> str:
    try:
        normalized = _require_codegraph_workdir(workdir)
        if not name or not name.strip():
            raise ValueError("name is required.")
        argv = [_codegraph_executable(), "node", "-p", str(normalized)]
        if file:
            argv.extend(["-f", file])
        if offset is not None:
            argv.extend(["--offset", str(max(1, int(offset)))])
        if limit is not None:
            argv.extend(["--limit", str(max(1, min(int(limit), 1000)))])
        if symbols_only:
            argv.append("--symbols-only")
        argv.append(name)
        return _run_codegraph(argv, normalized, timeout=timeout, runner=runner)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------


def _git(argv: list[str], workdir: str, runner=None) -> tuple[int, str, str]:
    run_fn = runner or op.run_argv
    return run_fn(["git", *argv], timeout=60, workdir=workdir)


def hermes_git_status(workdir: str, runner=None) -> str:
    try:
        policy = op.OperatorPolicy()
        if not workdir:
            raise ValueError("workdir is required.")
        # Fail-closed: the git workdir must be under a configured allowed path
        # and must not be a denied secret path.
        policy.require_workspace_read_path(workdir)
        rc, out, err = _git(["status", "--porcelain=v1"], workdir, runner=runner)
        result = {
            "success": rc == 0,
            "workdir": workdir,
            "stdout": op.redact_output(out),
            "stderr": op.redact_output(err),
        }
        return json.dumps(result, indent=2)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


def hermes_git_diff(
    workdir: str,
    pathspec: str | None = None,
    stat: bool = False,
    runner=None,
) -> str:
    try:
        policy = op.OperatorPolicy()
        if not workdir:
            raise ValueError("workdir is required.")
        # Fail-closed: the git workdir must be under a configured allowed path
        # and must not be a denied secret path.
        policy.require_workspace_read_path(workdir)
        # A pathspec can scope the diff to a secret-like file; refuse those so
        # a diff cannot surface .env / vault / token / .ssh contents.
        if pathspec and op.is_denied_path(pathspec):
            raise PermissionError(
                f"pathspec {pathspec!r} is denied by the operator path safety policy "
                "(secret / credential / vault / token / .env)."
            )
        argv: list[str] = ["diff"]
        if stat:
            argv.append("--stat")
        if pathspec:
            argv.append("--")
            argv.append(pathspec)
        rc, out, err = _git(argv, workdir, runner=runner)
        result = {
            "success": rc == 0,
            "workdir": workdir,
            "stdout": op.redact_output(out),
            "stderr": op.redact_output(err),
        }
        return json.dumps(result, indent=2)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


# ---------------------------------------------------------------------------
# Owner Mode
# ---------------------------------------------------------------------------

# Catastrophic command patterns that even Owner Mode refuses.
_CATASTROPHIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brm\s+-rf\s+/(\s|$)"),
    re.compile(r"\brm\s+-rf\s+/\*"),
    re.compile(r"(?i)\bdel\s+/s\b"),
    re.compile(r"(?i)\bformat\b"),
    re.compile(r"(?i)powershell.*-EncodedCommand"),
    re.compile(r"(?i)\b(curl|wget)\b[^|]*\|\s*(bash|sh)"),
    re.compile(r"(?i)\bgit\s+push\b.*--force"),
    re.compile(r"(?i)\bgit\s+push\b.*\s-f\b"),
    re.compile(r"(?i)\bgit\s+add\s+-A\b"),
    re.compile(r"(?i)\bgit\s+add\s+\.\s*$"),
)


def _command_touches_secrets(command: str) -> bool:
    """Heuristic: does the command mention secret/vault/token/.env/ssh paths?"""
    lower = command.lower()
    needles = (
        ".env",
        "vault",
        "mcp-tokens",
        "auth.json",
        ".ssh",
        "id_rsa",
        "id_ed25519",
        "authorized_keys",
        ".aws",
        ".gnupg",
        ".kube",
        "webhook_subscriptions.json",
        "oauth",
        "token",
        "secret",
        "credential",
        "cookie",
        "password",
    )
    return any(n in lower for n in needles)


def hermes_owner_run_command(
    command: str,
    timeout: int = 120,
    workdir: str | None = None,
    dry_run: bool = True,
    runner=None,
) -> str:
    try:
        policy = op.OperatorPolicy()
        policy.require_owner(dry_run)
        if not command or not command.strip():
            raise ValueError("command is required.")

        # Catastrophic pattern block.
        for pattern in _CATASTROPHIC_PATTERNS:
            if pattern.search(command):
                raise PermissionError(
                    f"Command blocked by catastrophic-pattern guard: {command!r}"
                )
        if _command_touches_secrets(command):
            raise PermissionError(
                "Command touches secret-like paths (.env / vault / token / ssh). "
                "Owner Mode does not permit secret access. Edit the file directly "
                "on a trusted shell."
            )

        try:
            argv = shlex.split(command, posix=(os.name != "nt"))
        except ValueError as exc:
            raise ValueError(f"Could not parse command: {exc}") from exc
        if not argv:
            raise ValueError("Empty command after parse.")

        if policy.effective_dry_run(dry_run):
            plan = {
                "would_run": True,
                "argv": argv,
                "shell": False,
                "workdir": workdir,
                "timeout": max(1, min(int(timeout), 600)),
                "owner_mode": True,
            }
            op.audit_record(
                tool="hermes_owner_run_command",
                level=policy.level,
                apply_mode=policy.apply_mode,
                dry_run=True,
                success=True,
                changed=False,
                summary="dry-run plan",
                extra={"argv": argv, "workdir": workdir or ""},
            )
            return json.dumps({"success": True, "dry_run": True, "plan": plan}, indent=2)

        policy.require_mutation(dry_run)
        run_fn = runner or op.run_argv
        rc, out, err = run_fn(argv, timeout=timeout, workdir=workdir)
        result = {
            "success": rc == 0,
            "dry_run": False,
            "owner_mode": True,
            "returncode": rc,
            "argv": argv,
            "stdout": op.redact_output(out),
            "stderr": op.redact_output(err),
        }
        op.audit_record(
            tool="hermes_owner_run_command",
            level=policy.level,
            apply_mode=policy.apply_mode,
            dry_run=False,
            success=rc == 0,
            changed=True,
            summary=f"rc={rc} argv={argv}",
            error=op.redact_output(err) if rc != 0 else "",
            extra={"workdir": workdir or ""},
        )
        return json.dumps(result, indent=2)
    except Exception as exc:
        op.audit_record(
            tool="hermes_owner_run_command",
            level="owner",
            apply_mode="unknown",
            dry_run=dry_run,
            success=False,
            error=str(exc),
        )
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


def hermes_owner_patch(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    dry_run: bool = True,
) -> str:
    try:
        policy = op.OperatorPolicy()
        policy.require_owner(dry_run)
        if not old_string:
            raise ValueError("old_string is required.")
        if new_string is None:
            raise ValueError("new_string is required.")

        # Owner mode still denies secret paths.
        if op.is_denied_path(path):
            raise PermissionError(
                f"Path {path!r} is denied by the operator path safety policy "
                "(secret / credential / vault / token / .env). Owner Mode "
                "does not override this in the current release."
            )

        p = op._normalize_path(path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        content = p.read_text(encoding="utf-8", errors="replace")
        if old_string not in content:
            raise ValueError("old_string not found in file.")
        if not replace_all and content.count(old_string) > 1:
            raise ValueError(
                "old_string matches multiple locations. Provide more context "
                "or set replace_all=True."
            )
        if replace_all:
            new_content = content.replace(old_string, new_string)
            match_count = content.count(old_string)
        else:
            new_content = content.replace(old_string, new_string, 1)
            match_count = 1

        if policy.effective_dry_run(dry_run):
            plan = {
                "would_patch": True,
                "path": str(p),
                "match_count": match_count,
                "diff": op.unified_diff(content, new_content, label=p.name),
                "owner_mode": True,
            }
            op.audit_record(
                tool="hermes_owner_patch",
                level=policy.level,
                apply_mode=policy.apply_mode,
                dry_run=True,
                success=True,
                changed=False,
                summary="dry-run plan",
                path=str(p),
            )
            return json.dumps({"success": True, "dry_run": True, "plan": plan}, indent=2)

        policy.require_mutation(dry_run)
        backup, backup_policy = _maybe_backup_file(p)
        _atomic_write_text(p, new_content)
        result = {
            "success": True,
            "dry_run": False,
            "owner_mode": True,
            "path": str(p),
            "match_count": match_count,
            "backup": str(backup) if backup else None,
            "backup_policy": backup_policy,
        }
        op.audit_record(
            tool="hermes_owner_patch",
            level=policy.level,
            apply_mode=policy.apply_mode,
            dry_run=False,
            success=True,
            changed=True,
            summary=f"owner patched {p.name}",
            path=str(p),
        )
        return json.dumps(result, indent=2)
    except Exception as exc:
        op.audit_record(
            tool="hermes_owner_patch",
            level="owner",
            apply_mode="unknown",
            dry_run=dry_run,
            success=False,
            error=str(exc),
            path=path,
        )
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


def hermes_owner_write_file(
    path: str,
    content: str,
    dry_run: bool = True,
) -> str:
    try:
        policy = op.OperatorPolicy()
        policy.require_owner(dry_run)
        if content is None:
            raise ValueError("content is required.")
        if op.is_denied_path(path):
            raise PermissionError(
                f"Path {path!r} is denied by the operator path safety policy "
                "(secret / credential / vault / token / .env). Owner Mode "
                "does not override this in the current release."
            )

        p = op._normalize_path(path)
        if policy.effective_dry_run(dry_run):
            plan = {
                "would_write": True,
                "path": str(p),
                "exists": p.exists(),
                "content_len": len(content),
                "owner_mode": True,
            }
            op.audit_record(
                tool="hermes_owner_write_file",
                level=policy.level,
                apply_mode=policy.apply_mode,
                dry_run=True,
                success=True,
                changed=False,
                summary="dry-run plan",
                path=str(p),
            )
            return json.dumps({"success": True, "dry_run": True, "plan": plan}, indent=2)

        policy.require_mutation(dry_run)
        backup, backup_policy = _maybe_backup_file(p) if p.exists() else (None, "new_file")
        _atomic_write_text(p, content)
        result = {
            "success": True,
            "dry_run": False,
            "owner_mode": True,
            "path": str(p),
            "backup": str(backup) if backup else None,
            "backup_policy": backup_policy,
        }
        op.audit_record(
            tool="hermes_owner_write_file",
            level=policy.level,
            apply_mode=policy.apply_mode,
            dry_run=False,
            success=True,
            changed=True,
            summary=f"owner wrote {p.name}",
            path=str(p),
        )
        return json.dumps(result, indent=2)
    except Exception as exc:
        op.audit_record(
            tool="hermes_owner_write_file",
            level="owner",
            apply_mode="unknown",
            dry_run=dry_run,
            success=False,
            error=str(exc),
            path=path,
        )
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


# ---------------------------------------------------------------------------
# Governed Owner recipe: public-safe GitHub issue creation (Phase 4A)
#
# Unlike the raw owner primitives above, this recipe is scope-restricted: the
# workdir must be a real git repository under RANDOKU_OPERATOR_ALLOWED_PATHS,
# and the issue body is always sanitized before it appears in output or
# audit. Phase 4A only produces a reviewable dry-run plan; it never invokes
# ``gh issue create``. Direct execution is Phase 4B, not implemented here.
# ---------------------------------------------------------------------------

_ISSUE_CREATE_RECIPE = "repo_issue_create"

# Markers that, combined with a path-shaped token, mark that token as
# secret-like for the purposes of a *public issue body* (narrower than
# op.is_denied_path, which gates local file access — this gates what a
# human reviewer sees in a dry-run preview and what may reach GitHub).
#
# Matched against the token's final path segment only (like
# op.is_denied_path matches SECRET_PATH_SUBSTRINGS against ``resolved.name``,
# not the whole path) so that generic markers such as "private" don't fire
# on every path under a platform temp dir (e.g. macOS's /private/var/...).
_SECRET_BODY_MARKERS: tuple[str, ...] = op.SECRET_PATH_SUBSTRINGS + (".env", ".ssh")

_LOCAL_ENDPOINT_RE = re.compile(r"(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?", re.IGNORECASE)
_NOTES_VAULT_RE = re.compile(r"obsidian|\bvault\b", re.IGNORECASE)

# A path "segment" between slashes may itself contain one embedded space
# (e.g. macOS's literal "Mobile Documents" under ~/Library, or a two-word
# vault folder name) — ponytail: caps each segment at two words rather than
# building a full path grammar; a vault folder name with three+ words can
# still leak its tail, add a stricter grammar if that surfaces.
_PATH_SEGMENT = r"[^\s/]+(?:\ [^\s/]+)?"
# ``~/`` (tilde immediately followed by a slash) is the common case for a
# home-relative path — the root must consume both characters together, or
# the mandatory first segment right after a bare "~" fails to match (no
# segment starts with "/") and the whole span match slides one character
# to the right, stranding the "~" outside the redacted span.
_VAULT_SPAN_RE = re.compile(rf"(?:~/?|/)(?:{_PATH_SEGMENT}/)*{_PATH_SEGMENT}")


def _looks_path_like(token: str) -> bool:
    return token.startswith("/") or token.startswith("~") or token.startswith(".") or "/" in token


def _sanitize_public_issue_body(
    text: str, *, repo_root: Path, allowed_paths: list[Path],
) -> tuple[str, dict[str, Any]]:
    """Deterministically strip local/private details from an issue body.

    Runs unconditionally (Phase 4A: always sanitize, regardless of repo
    visibility — simpler and safer than trusting visibility detection).

    Redaction order, most sensitive first, so nothing partially redacted
    leaks a prefix/suffix fragment of something more sensitive:
    1. notes-vault paths (whole span, see _VAULT_SPAN_RE — these can contain
       embedded spaces, so they must be found before whitespace-tokenizing)
    2. secret-like paths and local endpoints (per whitespace token)
    3. repo-root / other allowed paths / home (whole-string substitution)
    """
    original = text or ""
    vault_count = 0
    secret_count = 0
    endpoint_count = 0

    def _redact_vault_span(match: "re.Match[str]") -> str:
        nonlocal vault_count
        span = match.group(0)
        if _NOTES_VAULT_RE.search(span):
            vault_count += 1
            return "<private-notes-vault>"
        return span

    working = _VAULT_SPAN_RE.sub(_redact_vault_span, original)

    def _redact_token(match: "re.Match[str]") -> str:
        nonlocal secret_count, endpoint_count
        token = match.group(0)
        if "<" in token and ">" in token:
            return token  # already a placeholder from the vault-span pass
        lowered = token.lower()
        if _looks_path_like(token):
            basename = token.rsplit("/", 1)[-1].lower()
            if any(marker in basename for marker in _SECRET_BODY_MARKERS):
                secret_count += 1
                return "<secret-like-path>"
        if _LOCAL_ENDPOINT_RE.search(lowered):
            endpoint_count += 1
            return "<local-endpoint>"
        return token

    working = re.sub(r"\S+", _redact_token, working)

    repo_root_text = str(repo_root)
    repo_root_count = working.count(repo_root_text)
    if repo_root_count:
        working = working.replace(repo_root_text, "<repo-root>")

    private_paths_count = 0
    for allowed in allowed_paths:
        allowed_text = str(allowed)
        if allowed_text == repo_root_text:
            continue
        hits = working.count(allowed_text)
        if hits:
            private_paths_count += hits
            working = working.replace(allowed_text, "<private-path>")

    home_text = str(Path.home())
    home_count = working.count(home_text)
    if home_count:
        working = working.replace(home_text, "<home>")

    summary = {
        "enabled": True,
        "body_changed": working != original,
        "repo_root_redacted": repo_root_count,
        "home_redacted": home_count,
        "private_paths_redacted": private_paths_count,
        "notes_vault_redacted": vault_count,
        "secret_like_terms_redacted": secret_count,
        "local_endpoints_redacted": endpoint_count,
    }
    return working, summary


def hermes_owner_repo_issue_create(
    workdir: str,
    title: str,
    body: str,
    labels: Optional[list[str]] = None,
    dry_run: bool = True,
    runner=None,
) -> str:
    try:
        policy = op.OperatorPolicy()
        policy.require_owner(dry_run)
        if not title or not title.strip():
            raise ValueError("title is required.")
        if body is None:
            raise ValueError("body is required.")
        label_list = list(labels) if labels else []

        if not policy.effective_dry_run(dry_run):
            # Phase 4A ships preflight + sanitized dry-run plans only. A
            # direct request only reaches here when apply_mode=direct AND
            # dry_run=false (effective_dry_run already downgrades every
            # other combination to a plan, matching the other owner tools).
            raise PermissionError(
                "Direct issue creation is not implemented in Phase 4A. "
                "Only dry-run plans are supported for hermes_owner_repo_issue_create; "
                "call with dry_run=true (or leave apply_mode=dry_run) to preview."
            )

        # Governed recipes are stricter than raw owner primitives: workdir
        # is required and must be a real, allowed, non-secret git repo.
        if not workdir:
            raise ValueError("workdir is required.")
        policy.require_workspace_read_path(workdir)
        normalized_workdir = op._normalize_path(workdir)
        if not normalized_workdir.exists() or not normalized_workdir.is_dir():
            raise FileNotFoundError(f"workdir not found or not a directory: {workdir}")
        workdir_text = str(normalized_workdir)

        run_fn = runner or op.run_argv

        rc, out, err = run_fn(["git", "rev-parse", "--show-toplevel"], timeout=30, workdir=workdir_text)
        if rc != 0:
            raise PermissionError(
                f"workdir is not a git repository or worktree: {op.redact_output(err) or op.redact_output(out)}"
            )
        repo_root = op._normalize_path(out.strip())

        rc, out, err = run_fn(["gh", "auth", "status"], timeout=30, workdir=workdir_text)
        if rc != 0:
            raise PermissionError("gh auth status failed; cannot preflight issue creation.")

        rc, out, err = run_fn(
            ["gh", "repo", "view", "--json", "nameWithOwner,url,visibility"],
            timeout=30, workdir=workdir_text,
        )
        if rc != 0:
            raise PermissionError(f"gh repo view failed: {op.redact_output(err) or op.redact_output(out)}")
        try:
            repo_info = json.loads(out)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"gh repo view returned invalid JSON: {exc}") from exc

        repo_name = repo_info.get("nameWithOwner") or "unknown/unknown"
        repo_url = repo_info.get("url") or ""
        # Missing/unknown visibility defaults to the safer, more-sanitized
        # public-style assumption rather than trusting an absent field.
        visibility = str(repo_info.get("visibility") or "UNKNOWN").upper()

        sanitized_body, sanitization = _sanitize_public_issue_body(
            body, repo_root=repo_root, allowed_paths=policy.allowed_paths,
        )
        body_len, body_sha256 = op._hash_secret_text(sanitized_body)

        would_run = [
            "gh", "issue", "create",
            "--repo", repo_name,
            "--title", title,
            "--body-file", "<tempfile>",
        ]
        if label_list:
            would_run += ["--label", ",".join(label_list)]

        plan = {
            "success": True,
            "dry_run": True,
            "recipe": _ISSUE_CREATE_RECIPE,
            "repo": repo_name,
            "repo_url": repo_url,
            "visibility": visibility,
            "workdir": workdir_text,
            "title": title,
            "labels": label_list,
            "sanitization": sanitization,
            "body_len": body_len,
            "body_sha256": body_sha256,
            "body_preview": sanitized_body[:280],
            "would_run": would_run,
            "requires_user_review": True,
            "direct_supported": False,
        }

        op.audit_record(
            tool="hermes_owner_repo_issue_create",
            level=policy.level,
            apply_mode=policy.apply_mode,
            dry_run=True,
            success=True,
            changed=False,
            summary=f"dry-run plan repo={repo_name} visibility={visibility}",
            content=sanitized_body,
            extra={
                "recipe": _ISSUE_CREATE_RECIPE,
                "repo": repo_name,
                "visibility": visibility,
                "title": title,
                "labels": label_list,
                "sanitization": sanitization,
            },
        )
        return json.dumps(plan, indent=2)
    except Exception as exc:
        op.audit_record(
            tool="hermes_owner_repo_issue_create",
            level="owner",
            apply_mode="unknown",
            dry_run=dry_run,
            success=False,
            error=str(exc),
        )
        return json.dumps({"success": False, "error": str(exc)}, indent=2)
