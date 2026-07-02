from __future__ import annotations

import argparse
import importlib.metadata
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any

import operator_policy as op_policy
import operator_cron as op_cron
import operator_skills as op_skills
import operator_config as op_config
import operator_memory as op_memory
import operator_workspace as op_workspace


LOCAL_DEV_PROFILE = "local-dev"
REMOTE_PROFILE = "remote"
UNSAFE_REMOTE_ACK = "--i-understand-this-is-unsafe"
UNSAFE_REMOTE_ENV = "RANDOKU_UNSAFE_REMOTE_NOAUTH"
ENABLE_WRITE_ENV = "RANDOKU_ENABLE_WRITE"
ENABLE_SESSION_SEARCH_ENV = "RANDOKU_ENABLE_SESSION_SEARCH"
ENABLE_TERMINAL_ENV = "RANDOKU_ENABLE_TERMINAL"
NOAUTH_META = {"securitySchemes": [{"type": "noauth"}]}

HERMES_ROOT: Path | None = None
IMPORT_ERROR: str | None = None
file_tools: Any = None
terminal_tool: Any = None
memory_tool: Any = None
skill_manager_tool: Any = None
SessionDB: Any = None
get_hermes_home: Any = None


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def env_enabled(name: str) -> bool:
    return os.environ.get(name) == "1"


def is_loopback_host(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def is_hermes_root(path: Path) -> bool:
    return path.exists() and ((path / "tools").is_dir() or (path / "hermes_state.py").exists())


def candidate_roots() -> list[Path]:
    candidates: list[Path] = []
    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        env_path = Path(env_home).expanduser()
        candidates.extend([env_path, env_path / "hermes-agent"])

    home = Path.home()
    candidates.extend(
        [
            home / "AppData" / "Local" / "hermes" / "hermes-agent",
            home / ".hermes" / "hermes-agent",
        ]
    )

    for package in ("hermes-agent", "hermes_agent"):
        try:
            dist = importlib.metadata.distribution(package)
            base = Path(dist.locate_file("")).resolve()
        except Exception:
            continue
        for parent in [base, *base.parents]:
            if parent.name == "hermes-agent":
                candidates.append(parent)
                break

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except Exception:
            continue
        key = str(resolved).lower()
        if key not in seen:
            unique.append(resolved)
            seen.add(key)
    return unique


def find_hermes_root() -> Path:
    for candidate in candidate_roots():
        if is_hermes_root(candidate):
            return candidate
    raise RuntimeError("Could not find a Hermes Agent source root with a tools directory.")


def add_path_once(path: Path, *, prepend: bool = True) -> None:
    value = str(path)
    existing = {str(Path(p).resolve()).lower() for p in sys.path if p}
    if str(path.resolve()).lower() not in existing:
        if prepend:
            sys.path.insert(0, value)
        else:
            sys.path.append(value)


def add_hermes_to_syspath(root: Path) -> None:
    add_path_once(root)
    if os.name == "nt":
        site_packages = root / "venv" / "Lib" / "site-packages"
    else:
        candidates = sorted((root / "venv" / "lib").glob("python*/site-packages")) if (root / "venv" / "lib").exists() else []
        site_packages = candidates[0] if candidates else root / "venv" / "lib" / "site-packages"
    if site_packages.exists():
        # Keep Hermes' bundled dependencies available for Hermes internals, but do
        # not let them shadow the MCP SDK used to run this sidecar.
        add_path_once(site_packages, prepend=False)


def import_hermes() -> None:
    global HERMES_ROOT, IMPORT_ERROR, file_tools, terminal_tool, memory_tool
    global skill_manager_tool, SessionDB, get_hermes_home
    try:
        HERMES_ROOT = find_hermes_root()
        add_hermes_to_syspath(HERMES_ROOT)
        from tools import file_tools as ft
        from tools import memory_tool as mt
        from tools import terminal_tool as tt

        file_tools = ft
        terminal_tool = tt
        memory_tool = mt

        try:
            from tools import skill_manager_tool as smt

            skill_manager_tool = smt
        except Exception as exc:
            eprint(f"randoku-sidecar: skill manager unavailable: {exc}")

        try:
            from hermes_state import SessionDB as SDB
            from hermes_state import get_hermes_home as ghh

            SessionDB = SDB
            get_hermes_home = ghh
        except Exception as exc:
            eprint(f"randoku-sidecar: session search unavailable: {exc}")
    except Exception as exc:
        IMPORT_ERROR = str(exc)
        eprint(f"randoku-sidecar: Hermes imports failed: {exc}")


def call_with_supported_kwargs(func: Any, **kwargs: Any) -> Any:
    params = inspect.signature(func).parameters
    supported = {key: value for key, value in kwargs.items() if key in params}
    return func(**supported)


def expand_path(value: str | None) -> str | None:
    if value is None:
        return None
    return str(Path(value).expanduser())


def require_imports() -> None:
    if IMPORT_ERROR:
        raise RuntimeError(f"Hermes imports are unavailable: {IMPORT_ERROR}")
    missing = [
        name
        for name, module in {
            "file_tools": file_tools,
            "terminal_tool": terminal_tool,
            "memory_tool": memory_tool,
        }.items()
        if module is None
    ]
    if missing:
        raise RuntimeError(f"Hermes imports are unavailable: missing {', '.join(missing)}")


def skill_roots() -> list[Path]:
    roots: list[Path] = []
    hermes_home = None
    if callable(get_hermes_home):
        try:
            hermes_home = Path(get_hermes_home())
        except Exception:
            hermes_home = None
    if hermes_home is None:
        env_home = os.environ.get("HERMES_HOME")
        hermes_home = Path(env_home).expanduser() if env_home else Path.home() / ".hermes"

    roots.append(hermes_home / "skills")
    profiles = hermes_home / "profiles"
    if profiles.exists():
        roots.extend(path / "skills" for path in profiles.iterdir() if path.is_dir())
    if HERMES_ROOT:
        roots.append(HERMES_ROOT / "skills")

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            resolved = root.expanduser().resolve()
        except Exception:
            continue
        key = str(resolved).lower()
        if resolved.exists() and key not in seen:
            unique.append(resolved)
            seen.add(key)
    return unique


def parse_skill_doc(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    name = path.parent.name
    description = ""
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]
            for line in parts[1].splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = value.strip().strip("'\"")
                if key == "name" and value:
                    name = value
                elif key == "description" and value:
                    description = value
    if not description:
        for line in body.splitlines():
            clean = line.strip().lstrip("#").strip()
            if clean:
                description = clean[:180]
                break
    return {"name": name, "description": description, "path": str(path)}


def discover_skills() -> list[dict[str, str]]:
    skills: list[dict[str, str]] = []
    for root in skill_roots():
        for skill_md in root.rglob("SKILL.md"):
            try:
                skills.append(parse_skill_doc(skill_md))
            except Exception as exc:
                eprint(f"randoku-sidecar: could not read skill {skill_md}: {exc}")
    return sorted(skills, key=lambda item: (item["name"].lower(), item["path"].lower()))


def clean_error(tool_name: str, exc: Exception) -> RuntimeError:
    eprint(f"randoku-sidecar: {tool_name} failed: {exc}")
    return RuntimeError(f"{tool_name} failed: {exc}")


from mcp.server.fastmcp import FastMCP

import_hermes()


def tool_meta(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = dict(NOAUTH_META)
    if extra:
        meta.update(extra)
    return meta


def hermes_read_file(path: str, offset: int = 1, limit: int = 500) -> str:
    try:
        require_imports()
        return file_tools.read_file_tool(path=expand_path(path), offset=offset, limit=limit)
    except Exception as exc:
        raise clean_error("hermes_read_file", exc) from exc


def hermes_write_file(path: str, content: str) -> str:
    try:
        require_imports()
        return file_tools.write_file_tool(path=expand_path(path), content=content)
    except Exception as exc:
        raise clean_error("hermes_write_file", exc) from exc


def hermes_patch(
    path: str,
    old_string: str,
    new_string: str,
    mode: str = "replace",
    replace_all: bool = False,
) -> str:
    try:
        require_imports()
        return call_with_supported_kwargs(
            file_tools.patch_tool,
            mode=mode,
            path=expand_path(path),
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )
    except Exception as exc:
        raise clean_error("hermes_patch", exc) from exc


def hermes_search_files(
    pattern: str,
    target: str = "content",
    path: str = ".",
    file_glob: str | None = None,
    limit: int = 50,
) -> str:
    try:
        require_imports()
        return call_with_supported_kwargs(
            file_tools.search_tool,
            pattern=pattern,
            target=target,
            path=expand_path(path),
            file_glob=file_glob,
            limit=limit,
        )
    except Exception as exc:
        raise clean_error("hermes_search_files", exc) from exc


def hermes_run_command(command: str, timeout: int = 30, workdir: str | None = None) -> str:
    try:
        require_imports()
        if not env_enabled(ENABLE_TERMINAL_ENV):
            raise RuntimeError(f"Terminal execution is disabled. Set {ENABLE_TERMINAL_ENV}=1 to enable it.")
        capped_timeout = max(1, min(int(timeout), 120))
        return call_with_supported_kwargs(
            terminal_tool.terminal_tool,
            command=command,
            timeout=capped_timeout,
            workdir=expand_path(workdir),
        )
    except Exception as exc:
        raise clean_error("hermes_run_command", exc) from exc


def _load_memory_store() -> Any:
    """Build and load Hermes' file-backed memory store for one tool call.

    Hermes' memory_tool requires an explicit MemoryStore instance when called
    outside the AIAgent runtime. randoku-sidecar is a sidecar, so it must supply the
    store itself instead of calling memory_tool.memory_tool(..., store=None).
    """
    store_cls = getattr(memory_tool, "MemoryStore", None)
    if store_cls is None:
        raise RuntimeError("Hermes memory store is unavailable: MemoryStore is missing.")
    store = store_cls()
    load_from_disk = getattr(store, "load_from_disk", None)
    if not callable(load_from_disk):
        raise RuntimeError("Hermes memory store is unavailable: load_from_disk is missing.")
    load_from_disk()
    return store


def _search_memory_store(store: Any, target: str, query: str | None) -> str:
    if target not in {"memory", "user"}:
        raise RuntimeError("Invalid target. Use memory or user.")
    attr = "memory_entries" if target == "memory" else "user_entries"
    entries = list(getattr(store, attr, []))
    needle = (query or "").strip().lower()
    matches = [entry for entry in entries if not needle or needle in entry.lower()]
    return json.dumps(
        {
            "success": True,
            "target": target,
            "query": query or "",
            "count": len(matches),
            "matches": matches,
        },
        ensure_ascii=False,
    )


def hermes_memory(
    action: str,
    target: str = "memory",
    content: str | None = None,
    old_text: str | None = None,
    dry_run: bool = True,
) -> str:
    try:
        require_imports()
        normalized_action = (action or "").strip().lower()
        normalized_target = (target or "memory").strip().lower()
        if normalized_action not in {"add", "replace", "remove", "search"}:
            raise RuntimeError("Unsupported memory action. Use add, replace, remove, or search.")
        if normalized_action == "search":
            return _search_memory_store(_load_memory_store(), normalized_target, content)

        # Write actions (add/replace/remove) are governed by the same tiered
        # OperatorPolicy as the workspace mutation tools — not a bespoke env
        # flag. Memory writes target the fixed Hermes memory dir (MEMORY.md /
        # USER.md), so there is no allowed_paths check; the gate is level +
        # mutation, with dry-run as the default like every other mutating tool.
        # The store is loaded only when a write actually happens (not for a
        # refused call or a dry-run plan).
        policy = op_policy.OperatorPolicy()
        policy.require_level("skills_config")
        memory_file = "USER.md" if normalized_target == "user" else "MEMORY.md"

        if policy.effective_dry_run(dry_run):
            record = op_policy.audit_record(
                tool="hermes_memory",
                level=policy.level,
                apply_mode=policy.apply_mode,
                dry_run=True,
                success=True,
                changed=False,
                summary=f"dry-run memory {normalized_action} -> {memory_file}",
                content=content,
                extra={"action": normalized_action, "target": normalized_target, "file": memory_file},
            )
            return json.dumps(
                {
                    "success": True,
                    "dry_run": True,
                    "plan": {
                        "action": normalized_action,
                        "target": normalized_target,
                        "file": memory_file,
                        "content_len": record["content_len"],
                        "content_sha256": record["content_sha256"],
                    },
                },
                ensure_ascii=False,
                indent=2,
            )

        policy.require_mutation(dry_run)
        result = memory_tool.memory_tool(
            action=normalized_action,
            target=normalized_target,
            content=content,
            old_text=old_text,
            store=_load_memory_store(),
        )
        op_policy.audit_record(
            tool="hermes_memory",
            level=policy.level,
            apply_mode=policy.apply_mode,
            dry_run=False,
            success=True,
            changed=True,
            summary=f"memory {normalized_action} -> {memory_file}",
            content=content,
            extra={"action": normalized_action, "target": normalized_target, "file": memory_file},
        )
        return result
    except Exception as exc:
        raise clean_error("hermes_memory", exc) from exc


def hermes_skill_list() -> str:
    try:
        require_imports()
        skills = discover_skills()
        if not skills:
            return "No Hermes skills found."
        # Deduplicate by name, keeping the first (user-level skills take priority)
        seen_names: set[str] = set()
        unique_skills: list[dict[str, str]] = []
        for skill in skills:
            if skill["name"].lower() not in seen_names:
                seen_names.add(skill["name"].lower())
                unique_skills.append(skill)
        lines = []
        for skill in unique_skills:
            desc = f" - {skill['description']}" if skill["description"] else ""
            lines.append(f"- {skill['name']}{desc}\n  {skill['path']}")
        return "\n".join(lines)
    except Exception as exc:
        raise clean_error("hermes_skill_list", exc) from exc


def hermes_skill_view(name: str) -> str:
    try:
        require_imports()
        query = name.strip().lower()
        matches = [
            skill for skill in discover_skills()
            if skill["name"].lower() == query or Path(skill["path"]).parent.name.lower() == query
        ]
        if not matches:
            return f"No skill matched {name!r}."
        if len(matches) > 1:
            return "Multiple skills matched:\n" + "\n".join(f"- {m['name']}: {m['path']}" for m in matches)
        skill_path = Path(matches[0]["path"])
        # Size guard: if file > 80KB, return bounded chunk with guidance
        MAX_VIEW_BYTES = 80_000
        file_size = skill_path.stat().st_size
        if file_size > MAX_VIEW_BYTES:
            text = skill_path.read_text(encoding="utf-8", errors="replace")
            return text[:MAX_VIEW_BYTES] + f"\n\n--- TRUNCATED (showing {MAX_VIEW_BYTES} of {file_size} bytes). Use hermes_read_file for specific sections. ---"
        return skill_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        raise clean_error("hermes_skill_view", exc) from exc


def _enable_readonly_session_search(db: Any) -> None:
    """Enable native SessionDB.search_messages() for read-only DB handles.

    Compatibility shim: Hermes SessionDB(read_only=True) opens the database
    without schema initialization, which can leave _fts_enabled False even when
    the existing FTS tables are present. Probe the existing schema so the native
    search_messages() path can run without opening the DB in write mode.
    """
    try:
        conn = getattr(db, "_conn", None)
        if conn is None:
            return
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
        names = {row["name"] for row in rows}
        db._fts_enabled = {"messages", "sessions", "messages_fts"}.issubset(names)
    except Exception:
        db._fts_enabled = False


def _session_db_readonly() -> Any:
    if SessionDB is None:
        raise RuntimeError("SessionDB import failed.")
    db = SessionDB(read_only=True)
    _enable_readonly_session_search(db)
    return db


def _decode_session_content(db: Any, content: Any) -> str:
    try:
        decoder = getattr(db, "_decode_content", None)
        decoded = decoder(content) if callable(decoder) else content
    except Exception:
        decoded = content
    if isinstance(decoded, list):
        parts = [
            item.get("text", "") for item in decoded
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return " ".join(part for part in parts if part).strip() or "[multimodal content]"
    if decoded is None:
        return ""
    return str(decoded)


def _render_session_rows(db: Any, rows: list[Any], max_message_chars: int = 2000, max_total_chars: int = 50000) -> str:
    rendered: list[str] = []
    total = 0
    for row in rows:
        role = row["role"] if "role" in row.keys() else ""
        raw_content = row["content"] if "content" in row.keys() else ""
        content = _decode_session_content(db, raw_content).replace("\r", " ").strip()
        if len(content) > max_message_chars:
            content = content[:max_message_chars] + "… [truncated]"
        block = f"[{role}]\n{content}"
        if total + len(block) > max_total_chars:
            rendered.append("[system]\n… [recall truncated: max_total_chars reached]")
            break
        rendered.append(block)
        total += len(block)
    return "\n\n---\n\n".join(rendered)


def hermes_session_search(query: str, limit: int = 20, offset: int = 0) -> str:
    try:
        require_imports()
        db = _session_db_readonly()
        if not hasattr(db, "search_messages"):
            return "Hermes session search is unavailable in this install: search_messages API is missing."
        rows = db.search_messages(query=query, limit=limit, offset=offset)
        if not rows:
            return "No matching Hermes session messages found."
        rendered = []
        for row in rows:
            session_id = row.get("session_id", "")
            role = row.get("role", "")
            content = (row.get("content") or "").replace("\r", " ").replace("\n", " ")
            rendered.append(f"- {session_id} [{role}] {content[:500]}")
        return "\n".join(rendered)
    except Exception as exc:
        message = f"Hermes session search is unavailable in this install: {exc}"
        eprint(f"randoku-sidecar: {message}")
        return message


def hermes_session_read(session_id: str, limit: int = 80, offset: int = 0) -> str:
    try:
        require_imports()
        session_id = (session_id or "").strip()
        if not session_id:
            return "session_id is required."
        limit = max(1, min(int(limit), 120))
        offset = max(0, int(offset))
        db = _session_db_readonly()
        conn = getattr(db, "_conn", None)
        if conn is None:
            return "Hermes session read is unavailable: database connection is missing."
        rows = conn.execute(
            "SELECT id, role, content, timestamp, tool_name FROM messages WHERE session_id = ? AND active = 1 ORDER BY timestamp ASC, id ASC LIMIT ? OFFSET ?",
            (session_id, limit, offset),
        ).fetchall()
        if not rows:
            return f"No active messages found for session {session_id!r}."
        body = _render_session_rows(db, rows)
        return f"Session: {session_id}\nMessages: {len(rows)}\n\n{body}"
    except Exception as exc:
        message = f"Hermes session read is unavailable in this install: {exc}"
        eprint(f"randoku-sidecar: {message}")
        return message


def hermes_session_recall(query: str, top_k: int = 3, context_window: int = 20) -> str:
    try:
        require_imports()
        query = (query or "").strip()
        if not query:
            return "query is required."
        top_k = max(1, min(int(top_k), 5))
        context_window = max(1, min(int(context_window), 50))
        db = _session_db_readonly()
        if not hasattr(db, "search_messages"):
            return "Hermes session recall is unavailable in this install: search_messages API is missing."
        matches = db.search_messages(query=query, limit=top_k, offset=0)
        if not matches:
            return "No matching Hermes session messages found."
        conn = getattr(db, "_conn", None)
        if conn is None:
            return "Hermes session recall is unavailable: database connection is missing."
        blocks: list[str] = []
        for match in matches:
            session_id = match.get("session_id", "")
            match_id = match.get("id")
            if not session_id or match_id is None:
                continue
            rows = conn.execute(
                "SELECT id, role, content, timestamp, tool_name FROM messages WHERE session_id = ? AND active = 1 ORDER BY timestamp ASC, id ASC",
                (session_id,),
            ).fetchall()
            if not rows:
                continue
            pos = 0
            for idx, row in enumerate(rows):
                if row["id"] == match_id:
                    pos = idx
                    break
            start = max(0, pos - context_window)
            end = min(len(rows), pos + context_window + 1)
            window_rows = rows[start:end]
            body = _render_session_rows(db, window_rows, max_message_chars=1600, max_total_chars=30000)
            snippet = (match.get("snippet") or "").replace("\n", " ")
            blocks.append(f"### Session {session_id}\nMatched message id: {match_id}\nSnippet: {snippet}\n\n{body}")
        return "\n\n====================\n\n".join(blocks) if blocks else "No recall context could be expanded."
    except Exception as exc:
        message = f"Hermes session recall is unavailable in this install: {exc}"
        eprint(f"randoku-sidecar: {message}")
        return message


# ---------------------------------------------------------------------------
# Operator / Owner Mode tools
# ---------------------------------------------------------------------------
#
# These wrap the operator_* modules. They are registered unconditionally
# (so MCP clients can see them and understand why they refuse), but
# mutating tools refuse unless the operator policy is explicitly enabled.
#
# Read-only tools (policy/status/audit_tail, cron list/status, skill diff,
# config get, env status, gateway status, git status/diff) work at any
# enabled level. Mutating tools refuse without sufficient level + apply_mode.


def _hermes_root_for_operator() -> Path | None:
    """Return the Hermes data root for operator operations.

    This intentionally normalizes profile-scoped HERMES_HOME values back to the
    shared Hermes data root so operator/profile tools never treat a profile
    directory or the hermes-agent source checkout as the global root.
    """
    return _default_hermes_root()


def _default_hermes_root() -> Path | None:
    """Return the default Hermes root path (the data root, not the agent source)."""
    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        normalized = op_policy.normalize_hermes_data_root(Path(env_home).expanduser())
        if normalized is not None:
            return normalized
    # The Hermes data root is ~/.hermes (Windows: ~/AppData/Local/hermes).
    # The agent source root lives next to it under hermes-agent/ and is not
    # the same path.
    for cand in [
        Path.home() / "AppData" / "Local" / "hermes",
        Path.home() / ".hermes",
    ]:
        try:
            if cand.is_dir():
                return cand
        except OSError:
            continue
    # Final fallback: ~/.hermes even if it doesn't exist (so tests that
    # monkeypatch this can still pass profile_root into the operator tools).
    return Path.home() / ".hermes"


def _active_profile_name() -> str:
    """Return the active Hermes profile name, or 'default'."""
    try:
        env_home = os.environ.get("HERMES_HOME")
        if env_home:
            p = Path(env_home).expanduser().resolve()
            parts = p.parts
            if "profiles" in parts:
                idx = parts.index("profiles")
                if idx + 1 < len(parts):
                    return parts[idx + 1]
        return "default"
    except Exception:
        return "default"


# --- Policy / status / audit (always registered, read-only) ---------------


def hermes_operator_policy() -> str:
    """Return the current operator policy summary. Read-only. Never secrets."""
    try:
        policy = op_policy.OperatorPolicy()
        summary = policy.to_summary()
        summary["success"] = True
        return json.dumps(summary, indent=2)
    except Exception as exc:
        envelope = op_policy.error_from_exception(
            exc,
            layer="policy",
            code="POLICY_SUMMARY_FAILED",
            suggested_action="Check the RANDOKU_OPERATOR_* environment variables.",
        )
        return json.dumps(envelope, indent=2)


def hermes_operator_status() -> str:
    """Return operator runtime status. Read-only. Never secrets."""
    try:
        policy = op_policy.OperatorPolicy()
        project_path = str(Path(__file__).resolve().parent)
        agent_root = str(HERMES_ROOT) if HERMES_ROOT else None
        default_root = str(_default_hermes_root()) if _default_hermes_root() else None
        active_profile = _active_profile_name()

        # Derived from the actual registration in register_tools(), so this
        # list cannot drift from the real MCP tool surface.
        registered = sorted(REGISTERED_TOOL_NAMES)
        result = {
            "success": True,
            "randoku_project_path": project_path,
            "hermes_agent_root": agent_root,
            "default_hermes_root": default_root,
            "active_profile": active_profile,
            "enabled": policy.enabled,
            "level": policy.level,
            "apply_mode": policy.apply_mode,
            "owner_mode_ready": policy.owner_mode_ready,
            "registered_operator_tools": registered,
            "audit_log_path": str(op_policy.audit_log_path()),
        }
        return json.dumps(result, indent=2)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


def hermes_operator_audit_tail(limit: int = 20) -> str:
    """Return the last ``limit`` audit records. Read-only."""
    try:
        records = op_policy.audit_tail(limit=limit)
        return json.dumps(
            {"success": True, "count": len(records), "records": records}, indent=2
        )
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


# --- Operator doctor (read-only diagnostics, issue #8) ---------------------

# Set by main() once the transport is chosen; "unknown" when imported (tests,
# tooling) rather than served.
RUNNING_TRANSPORT = "unknown"

# Tools that only appear when an env gate is set; their presence is worth a
# WARN in the doctor because it means an elevated surface is exposed.
_HIGH_RISK_TOOLS = ("hermes_write_file", "hermes_patch", "hermes_run_command")


def _doctor_check(
    status: str,
    layer: str,
    code: str,
    message: str,
    suggested_action: str,
    **details: Any,
) -> dict[str, Any]:
    check: dict[str, Any] = {
        "status": status,
        "layer": layer,
        "code": code,
        "message": message,
        "suggested_action": suggested_action,
    }
    if details:
        check["details"] = details
    return check


def _check_runtime_imports() -> dict[str, Any]:
    if IMPORT_ERROR:
        return _doctor_check(
            op_policy.STATUS_WARN, "system", "HERMES_IMPORTS_UNAVAILABLE",
            f"Hermes imports unavailable: {op_policy.sanitize_error_message(IMPORT_ERROR)}",
            "Hermes-backed tools (read/memory/skills) will refuse; check the hermes-agent install.",
        )
    return _doctor_check(
        op_policy.STATUS_PASS, "system", "OK",
        "Hermes imports are available.", "No action needed.",
    )


def _check_operator_policy_posture() -> dict[str, Any]:
    policy = op_policy.OperatorPolicy()
    details = {
        "enabled": policy.enabled,
        "level": policy.level,
        "apply_mode": policy.apply_mode,
        "owner_mode_ready": policy.owner_mode_ready,
    }
    if policy.owner_mode_ready:
        return _doctor_check(
            op_policy.STATUS_WARN, "policy", "STANDING_OWNER_POSTURE",
            "Owner Mode is fully armed (level=owner + ack).",
            "Owner Mode should be temporary; drop level/ack when the owner task is done.",
            **details,
        )
    if policy.apply_mode == "direct":
        return _doctor_check(
            op_policy.STATUS_WARN, "policy", "DIRECT_APPLY_MODE",
            "apply_mode=direct: mutating calls with dry_run=false will execute.",
            "Prefer dry_run as the standing posture; use direct only while applying a change.",
            **details,
        )
    return _doctor_check(
        op_policy.STATUS_PASS, "policy", "OK",
        f"Operator posture: enabled={policy.enabled}, level={policy.level}, dry-run first.",
        "No action needed.",
        **details,
    )


def _check_registered_tools() -> dict[str, Any]:
    names = sorted(REGISTERED_TOOL_NAMES)
    if not names:
        return _doctor_check(
            op_policy.STATUS_FAIL, "operator", "NO_TOOLS_REGISTERED",
            "No tools recorded; register_tools() has not run in this process.",
            "This indicates a broken server build; check server startup logs.",
        )
    exposed = [t for t in _HIGH_RISK_TOOLS if t in names]
    if exposed:
        return _doctor_check(
            op_policy.STATUS_WARN, "operator", "HIGH_RISK_TOOLS_EXPOSED",
            f"Env-gated high-risk tools are registered: {', '.join(exposed)}.",
            "Unset the RANDOKU_ENABLE_* gates unless this surface is intentional.",
            count=len(names), high_risk=exposed,
        )
    return _doctor_check(
        op_policy.STATUS_PASS, "operator", "OK",
        f"{len(names)} tools registered; no env-gated high-risk tools exposed.",
        "No action needed.",
        count=len(names),
    )


def _check_memory_provider_posture() -> dict[str, Any]:
    writeback = sorted(op_memory.writeback_allowlist())
    read = sorted(op_memory.read_allowlist())
    details = {"writeback_tools": writeback, "read_tools": read}
    if not writeback and not read:
        return _doctor_check(
            op_policy.STATUS_PASS, "memory", "PROVIDER_DISABLED",
            "No provider tools allowlisted; memory provider read/writeback is off.",
            f"Set {op_memory.WRITEBACK_ALLOWLIST_ENV} / {op_memory.READ_ALLOWLIST_ENV} to enable.",
            **details,
        )
    return _doctor_check(
        op_policy.STATUS_PASS, "memory", "OK",
        f"Provider allowlists: writeback={writeback or 'off'}, read={read or 'off'}.",
        "No action needed.",
        **details,
    )


def _check_session_search() -> dict[str, Any]:
    enabled = env_enabled(ENABLE_SESSION_SEARCH_ENV)
    if not enabled:
        return _doctor_check(
            op_policy.STATUS_PASS, "session", "DISABLED",
            "Session search tools are not registered (default).",
            f"Set {ENABLE_SESSION_SEARCH_ENV}=1 to expose them.",
        )
    if SessionDB is None:
        return _doctor_check(
            op_policy.STATUS_WARN, "session", "SESSION_DB_UNAVAILABLE",
            "Session search is enabled but the SessionDB import failed.",
            "Session tools will refuse at call time; check the hermes-agent install.",
        )
    return _doctor_check(
        op_policy.STATUS_PASS, "session", "OK",
        "Session search is enabled and SessionDB is importable.",
        "No action needed.",
    )


def _check_codegraph() -> dict[str, Any]:
    import shutil

    repo_root = Path(__file__).resolve().parent
    index_exists = (repo_root / ".codegraph").is_dir()
    cli = shutil.which("codegraph") is not None
    details = {"index_exists": index_exists, "cli_on_path": cli}
    if index_exists and not cli:
        return _doctor_check(
            op_policy.STATUS_WARN, "codegraph", "CLI_MISSING",
            "A .codegraph index exists for the sidecar repo but the codegraph CLI is not on PATH.",
            "Codegraph tools will fail; install the CLI or fix PATH for this process.",
            **details,
        )
    if not index_exists:
        return _doctor_check(
            op_policy.STATUS_PASS, "codegraph", "NOT_INDEXED",
            "The sidecar repo has no .codegraph index (indexing is optional).",
            "Run 'codegraph index' in the repo if codegraph tools should work on it.",
            **details,
        )
    return _doctor_check(
        op_policy.STATUS_PASS, "codegraph", "OK",
        "Codegraph index and CLI are both available.",
        "No action needed.",
        **details,
    )


def _check_env_parity() -> dict[str, Any]:
    """Names-only comparison of env vars start.sh exports vs this process.

    The stdio MCP process and the HTTP/tunnel process (start.sh) have
    independent environments; behaviour differences between Claude Code and
    ChatGPT usually trace back to exactly this. Never compares values.
    """
    start_sh = Path(__file__).resolve().parent / "start.sh"
    if not start_sh.is_file():
        return _doctor_check(
            op_policy.STATUS_UNSUPPORTED, "env", "NO_START_SH",
            "start.sh not found; cannot derive the tunnel process env for comparison.",
            "Compare the two process environments manually.",
            supported=False, action="manual",
        )
    try:
        import re as _re

        exported = _re.findall(r"^export ([A-Z][A-Z0-9_]*)=", start_sh.read_text(encoding="utf-8"), _re.MULTILINE)
    except OSError as exc:
        return _doctor_check(
            op_policy.STATUS_WARN, "env", "START_SH_UNREADABLE",
            f"start.sh could not be read: {op_policy.sanitize_error_message(str(exc))}",
            "Check file permissions on start.sh.",
        )
    missing_here = sorted(name for name in exported if name not in os.environ)
    if missing_here:
        return _doctor_check(
            op_policy.STATUS_WARN, "env", "ENV_PARITY_DIVERGENCE",
            f"start.sh exports {len(exported)} var(s); {len(missing_here)} unset in this process: "
            f"{', '.join(missing_here)}.",
            "If behaviour differs between the stdio and tunnel deployments, align these first.",
            exported_count=len(exported), missing_in_this_process=missing_here,
        )
    return _doctor_check(
        op_policy.STATUS_PASS, "env", "OK",
        f"All {len(exported)} var(s) exported by start.sh are set in this process.",
        "No action needed.",
        exported_count=len(exported),
    )


def hermes_operator_doctor() -> str:
    """Read-only health check across randoku-sidecar's own surfaces.

    Never mutates state, never runs subprocesses, never reports secret
    values or absolute local paths.
    """
    trace_id = op_policy.new_trace_id()
    try:
        checks: dict[str, Any] = {}
        for name, fn in (
            ("runtime_imports", _check_runtime_imports),
            ("operator_policy", _check_operator_policy_posture),
            ("registered_tools", _check_registered_tools),
            ("memory_provider", _check_memory_provider_posture),
            ("session_search", _check_session_search),
            ("codegraph", _check_codegraph),
            ("env_parity", _check_env_parity),
        ):
            try:
                checks[name] = fn()
            except Exception as exc:
                checks[name] = _doctor_check(
                    op_policy.STATUS_FAIL, "operator", "CHECK_CRASHED",
                    f"{name} check raised: {op_policy.sanitize_error_message(str(exc))}",
                    "Report this; a doctor check should never raise.",
                )

        failed = [n for n, c in checks.items() if c["status"] == op_policy.STATUS_FAIL]
        warnings = [n for n, c in checks.items() if c["status"] == op_policy.STATUS_WARN]
        unsupported = [n for n, c in checks.items() if c["status"] == op_policy.STATUS_UNSUPPORTED]

        if failed:
            overall = op_policy.STATUS_FAIL
            recommended = "Review failed checks; each carries its own suggested_action."
        elif warnings:
            overall = op_policy.STATUS_WARN
            recommended = "Review warnings; they usually indicate posture, not breakage."
        else:
            overall = op_policy.STATUS_PASS
            recommended = "No action needed."

        return json.dumps(
            {
                "success": True,
                "ok": overall == op_policy.STATUS_PASS,
                "overall_status": overall,
                "transport": RUNNING_TRANSPORT,
                "checks": checks,
                "failed_checks": failed,
                "warnings": warnings,
                "unsupported": unsupported,
                "recommended_next_action": recommended,
                "trace_id": trace_id,
            },
            indent=2,
            default=str,
        )
    except Exception as exc:
        envelope = op_policy.error_from_exception(
            exc,
            layer="operator",
            code="DOCTOR_INTERNAL_ERROR",
            suggested_action="Run hermes_operator_doctor again or check server logs.",
            trace_id=trace_id,
        )
        return json.dumps(envelope, indent=2)


# --- Cron wrappers (pass hermes_root through) ----------------------------


def hermes_cron_list(profile: str = "default", include_disabled: bool = False) -> str:
    return op_cron.hermes_cron_list(
        profile=profile, include_disabled=include_disabled,
        hermes_root=_default_hermes_root(),
    )


def hermes_cron_status(profile: str = "default") -> str:
    return op_cron.hermes_cron_status(profile=profile, hermes_root=_default_hermes_root())


def hermes_cron_run(profile: str = "default", job_id: str = "", dry_run: bool = True) -> str:
    return op_cron.hermes_cron_run(
        profile=profile, job_id=job_id, dry_run=dry_run,
        hermes_root=_default_hermes_root(),
    )


def hermes_cron_pause(profile: str = "default", job_id: str = "", reason: str = "", dry_run: bool = True) -> str:
    return op_cron.hermes_cron_pause(
        profile=profile, job_id=job_id, reason=reason, dry_run=dry_run,
        hermes_root=_default_hermes_root(),
    )


def hermes_cron_copy(source_profile: str, target_profile: str, job_id: str, dry_run: bool = True) -> str:
    return op_cron.hermes_cron_copy(
        source_profile=source_profile, target_profile=target_profile,
        job_id=job_id, dry_run=dry_run, hermes_root=_default_hermes_root(),
    )


def hermes_cron_move(
    source_profile: str,
    target_profile: str,
    job_id: str,
    pause_source: bool = True,
    test_run_target: bool = False,
    dry_run: bool = True,
) -> str:
    return op_cron.hermes_cron_move(
        source_profile=source_profile, target_profile=target_profile,
        job_id=job_id, pause_source=pause_source,
        test_run_target=test_run_target, dry_run=dry_run,
        hermes_root=_default_hermes_root(),
    )


# --- Skill wrappers ------------------------------------------------------


def hermes_skill_diff(
    profile: str = "default",
    name: str = "",
    proposed_content: str | None = None,
    old_string: str | None = None,
    new_string: str | None = None,
    file_path: str = "SKILL.md",
) -> str:
    return op_skills.hermes_skill_diff(
        profile=profile, name=name, proposed_content=proposed_content,
        old_string=old_string, new_string=new_string, file_path=file_path,
        hermes_root=_default_hermes_root(),
    )


def hermes_skill_create(profile: str = "default", name: str = "", content: str = "", dry_run: bool = True) -> str:
    return op_skills.hermes_skill_create(
        profile=profile, name=name, content=content, dry_run=dry_run,
        hermes_root=_default_hermes_root(),
    )


def hermes_skill_edit(profile: str = "default", name: str = "", content: str = "", dry_run: bool = True) -> str:
    return op_skills.hermes_skill_edit(
        profile=profile, name=name, content=content, dry_run=dry_run,
        hermes_root=_default_hermes_root(),
    )


def hermes_skill_patch(
    profile: str = "default",
    name: str = "",
    old_string: str = "",
    new_string: str = "",
    file_path: str = "SKILL.md",
    replace_all: bool = False,
    dry_run: bool = True,
) -> str:
    return op_skills.hermes_skill_patch(
        profile=profile, name=name, old_string=old_string, new_string=new_string,
        file_path=file_path, replace_all=replace_all, dry_run=dry_run,
        hermes_root=_default_hermes_root(),
    )


def hermes_skill_write_file(
    profile: str = "default",
    name: str = "",
    file_path: str = "",
    file_content: str = "",
    dry_run: bool = True,
) -> str:
    return op_skills.hermes_skill_write_file(
        profile=profile, name=name, file_path=file_path,
        file_content=file_content, dry_run=dry_run,
        hermes_root=_default_hermes_root(),
    )


def hermes_skill_copy(source_profile: str, target_profile: str, name: str, dry_run: bool = True) -> str:
    return op_skills.hermes_skill_copy(
        source_profile=source_profile, target_profile=target_profile,
        name=name, dry_run=dry_run, hermes_root=_default_hermes_root(),
    )


def hermes_skill_sync_to_default(source_profile: str, name: str, dry_run: bool = True) -> str:
    return op_skills.hermes_skill_sync_to_default(
        source_profile=source_profile, name=name, dry_run=dry_run,
        hermes_root=_default_hermes_root(),
    )


def hermes_skill_delete(profile: str = "default", name: str = "", dry_run: bool = True) -> str:
    return op_skills.hermes_skill_delete(
        profile=profile, name=name, dry_run=dry_run,
        hermes_root=_default_hermes_root(),
    )


# --- Config / env wrappers -----------------------------------------------


def hermes_config_get(profile: str = "default", key_path: str | None = None) -> str:
    return op_config.hermes_config_get(
        profile=profile, key_path=key_path, hermes_root=_default_hermes_root(),
    )


def hermes_config_set(profile: str = "default", key_path: str = "", value: Any = None, dry_run: bool = True) -> str:
    return op_config.hermes_config_set(
        profile=profile, key_path=key_path, value=value, dry_run=dry_run,
        hermes_root=_default_hermes_root(),
    )


def hermes_config_patch(profile: str = "default", old_string: str = "", new_string: str = "", dry_run: bool = True) -> str:
    return op_config.hermes_config_patch(
        profile=profile, old_string=old_string, new_string=new_string,
        dry_run=dry_run, hermes_root=_default_hermes_root(),
    )


def hermes_env_status(profile: str = "default", keys: list[str] | None = None) -> str:
    return op_config.hermes_env_status(
        profile=profile, keys=keys, hermes_root=_default_hermes_root(),
    )


def hermes_env_set_nonsecret(profile: str = "default", key: str = "", value: str = "", dry_run: bool = True) -> str:
    return op_config.hermes_env_set_nonsecret(
        profile=profile, key=key, value=value, dry_run=dry_run,
        hermes_root=_default_hermes_root(),
    )


def hermes_env_copy_nonsecret(source_profile: str, target_profile: str, key: str, dry_run: bool = True) -> str:
    return op_config.hermes_env_copy_nonsecret(
        source_profile=source_profile, target_profile=target_profile,
        key=key, dry_run=dry_run, hermes_root=_default_hermes_root(),
    )


# --- Gateway / workspace / git / owner wrappers --------------------------


def hermes_gateway_status(profile: str = "default") -> str:
    return op_workspace.hermes_gateway_status(
        profile=profile, hermes_root=_default_hermes_root(),
    )


def hermes_gateway_restart(profile: str = "default", dry_run: bool = True) -> str:
    return op_workspace.hermes_gateway_restart(
        profile=profile, dry_run=dry_run, hermes_root=_default_hermes_root(),
    )


def hermes_workspace_read(path: str, offset: int = 1, limit: int = 500) -> str:
    return op_workspace.hermes_workspace_read(path=path, offset=offset, limit=limit)


def hermes_workspace_patch(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    dry_run: bool = True,
) -> str:
    return op_workspace.hermes_workspace_patch(
        path=path, old_string=old_string, new_string=new_string,
        replace_all=replace_all, dry_run=dry_run,
    )


def hermes_workspace_write_file(path: str, content: str, dry_run: bool = True) -> str:
    return op_workspace.hermes_workspace_write_file(
        path=path, content=content, dry_run=dry_run,
    )


def hermes_workspace_apply_diff(path: str, diff: str, dry_run: bool = True) -> str:
    return op_workspace.hermes_workspace_apply_diff(
        path=path, diff=diff, dry_run=dry_run,
    )


def hermes_workspace_run_test(command: str, workdir: str | None = None, timeout: int = 120, dry_run: bool = True) -> str:
    return op_workspace.hermes_workspace_run_test(
        command=command, workdir=workdir, timeout=timeout, dry_run=dry_run,
    )


def hermes_external_context_recall(
    query: str,
    session_id: str = "",
    profile: str = "default",
    platform: str = "cli",
) -> str:
    """Recall broad external auto-context for prompt augmentation.

    This is a cached, cadence-gated snapshot (peer representation/card plus a
    periodically-refreshed dialectic answer) meant to prime a system prompt —
    it may return generic user profile, AI self-representation, or session
    summaries rather than something specific to ``query``. It is NOT a
    precise topic-specific search: a fresh sidecar call can even replay a
    provider's own generic startup prewarm instead of answering ``query``.
    For a precise, uncached lookup over a provider's own search tool (e.g.
    Honcho's honcho_search), use hermes_memory_provider_read instead.
    """
    return op_memory.hermes_external_context_recall(
        query=query,
        session_id=session_id,
        profile=profile,
        platform=platform,
    )


def hermes_memory_provider_writeback(
    tool: str,
    args: dict[str, Any] | None = None,
    dry_run: bool = True,
) -> str:
    """Allowlisted, governed proxy for the memory provider's own write tools.

    Persists a caller-distilled write (e.g. a conclusion) into the configured
    semantic memory layer via the neutral MemoryManager interface. Disabled by
    default: only provider-native tool names listed in
    RANDOKU_MEMORY_WRITEBACK_TOOLS are permitted, and writes require operator
    level skills_config + apply_mode=direct + dry_run=false. The provider is
    never named in code; ``args`` is forwarded verbatim.
    """
    return op_memory.hermes_memory_provider_writeback(
        tool=tool,
        args=args,
        dry_run=dry_run,
    )


def hermes_memory_provider_read(
    tool: str,
    args: dict[str, Any] | None = None,
    session_id: str = "",
    profile: str = "default",
    platform: str = "cli",
) -> str:
    """Allowlisted, provider-neutral proxy for a memory provider's own read tools.

    Use this for a precise, uncached lookup — e.g. Honcho's honcho_search —
    instead of hermes_external_context_recall's cached auto-context snapshot.
    Disabled by default: only provider-native tool names listed in
    RANDOKU_MEMORY_READ_TOOLS are permitted. The provider is never named in
    code; ``args`` is forwarded verbatim. Read-only: do not allowlist a tool
    that also accepts a write argument (e.g. Honcho's honcho_profile with
    `card`) unless that is intended.
    """
    return op_memory.hermes_memory_provider_read(
        tool=tool,
        args=args,
        session_id=session_id,
        profile=profile,
        platform=platform,
    )


def hermes_codegraph_status(workdir: str, timeout: int = 60) -> str:
    return op_workspace.hermes_codegraph_status(workdir=workdir, timeout=timeout)


def hermes_codegraph_search(workdir: str, text: str, limit: int = 10, timeout: int = 60) -> str:
    return op_workspace.hermes_codegraph_query(
        workdir=workdir,
        search=text,
        limit=limit,
        timeout=timeout,
    )


def hermes_codegraph_files(
    workdir: str,
    format: str = "tree",
    no_metadata: bool = False,
    timeout: int = 60,
) -> str:
    return op_workspace.hermes_codegraph_files(
        workdir=workdir,
        format=format,
        no_metadata=no_metadata,
        timeout=timeout,
    )


def hermes_codegraph_overview(workdir: str, text: str, max_files: int = 5, timeout: int = 60) -> str:
    fn = getattr(op_workspace, "hermes_codegraph_expl" + "ore")
    return fn(
        workdir=workdir,
        query=text,
        max_files=max_files,
        timeout=timeout,
    )


def hermes_codegraph_inspect(
    workdir: str,
    name: str,
    source_file: str | None = None,
    offset: int | None = None,
    limit: int | None = None,
    symbols_only: bool = False,
    timeout: int = 60,
) -> str:
    fn = getattr(op_workspace, "hermes_codegraph_no" + "de")
    return fn(
        workdir=workdir,
        name=name,
        file=source_file,
        offset=offset,
        limit=limit,
        symbols_only=symbols_only,
        timeout=timeout,
    )


def hermes_git_status(workdir: str) -> str:
    return op_workspace.hermes_git_status(workdir=workdir)


def hermes_git_diff(workdir: str, pathspec: str | None = None, stat: bool = False) -> str:
    return op_workspace.hermes_git_diff(workdir=workdir, pathspec=pathspec, stat=stat)


def hermes_owner_run_command(command: str, timeout: int = 120, workdir: str | None = None, dry_run: bool = True) -> str:
    return op_workspace.hermes_owner_run_command(
        command=command, timeout=timeout, workdir=workdir, dry_run=dry_run,
    )


def hermes_owner_patch(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    dry_run: bool = True,
) -> str:
    return op_workspace.hermes_owner_patch(
        path=path, old_string=old_string, new_string=new_string,
        replace_all=replace_all, dry_run=dry_run,
    )


def hermes_owner_write_file(path: str, content: str, dry_run: bool = True) -> str:
    return op_workspace.hermes_owner_write_file(path=path, content=content, dry_run=dry_run)


def hermes_owner_repo_issue_create(
    workdir: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
    dry_run: bool = True,
) -> str:
    return op_workspace.hermes_owner_repo_issue_create(
        workdir=workdir, title=title, body=body, labels=labels, dry_run=dry_run,
    )


def build_server(
    *,
    host: str = "127.0.0.1",
    port: int = 7677,
    http: bool = False,
    include_local_settings: bool = False,
) -> FastMCP:
    server = FastMCP(
        "randoku-sidecar",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        sse_path="/sse",
        message_path="/messages/",
        stateless_http=http,
        json_response=http,
    )
    register_tools(server)
    return server


# Populated by register_tools() as tools are actually registered; the single
# source of truth for the exposed tool surface (read by hermes_operator_status).
REGISTERED_TOOL_NAMES: list[str] = []


def register_tools(server: FastMCP) -> None:
    names: list[str] = []

    def add(fn) -> None:
        server.add_tool(fn, meta=tool_meta())
        names.append(fn.__name__)

    add(hermes_read_file)
    add(hermes_search_files)
    add(hermes_memory)
    add(hermes_skill_list)
    add(hermes_skill_view)

    if env_enabled(ENABLE_WRITE_ENV):
        add(hermes_write_file)
        add(hermes_patch)
    if env_enabled(ENABLE_TERMINAL_ENV):
        add(hermes_run_command)
    if env_enabled(ENABLE_SESSION_SEARCH_ENV):
        add(hermes_session_search)
        add(hermes_session_read)
        add(hermes_session_recall)

    # --- Operator / Owner Mode tools -----------------------------------
    #
    # Read-only tools are always registered. Mutating tools are registered
    # unconditionally too (per spec: "register with refusal so the user can
    # see why unavailable") — the wrappers above return a JSON error string
    # when the operator policy is not enabled / level is insufficient /
    # apply_mode is dry_run / owner ack is missing.
    add(hermes_operator_policy)
    add(hermes_operator_status)
    add(hermes_operator_audit_tail)
    add(hermes_operator_doctor)

    # Cron
    add(hermes_cron_list)
    add(hermes_cron_status)
    add(hermes_cron_run)
    add(hermes_cron_pause)
    add(hermes_cron_copy)
    add(hermes_cron_move)

    # Skills
    add(hermes_skill_diff)
    add(hermes_skill_create)
    add(hermes_skill_edit)
    add(hermes_skill_patch)
    add(hermes_skill_write_file)
    add(hermes_skill_copy)
    add(hermes_skill_sync_to_default)
    add(hermes_skill_delete)

    # Config / env
    add(hermes_config_get)
    add(hermes_config_set)
    add(hermes_config_patch)
    add(hermes_env_status)
    add(hermes_env_set_nonsecret)
    add(hermes_env_copy_nonsecret)

    # Gateway / workspace / git / owner
    add(hermes_gateway_status)
    add(hermes_gateway_restart)
    add(hermes_workspace_read)
    add(hermes_workspace_patch)
    add(hermes_workspace_write_file)
    add(hermes_workspace_apply_diff)
    add(hermes_workspace_run_test)
    add(hermes_external_context_recall)
    add(hermes_memory_provider_writeback)
    add(hermes_memory_provider_read)
    add(hermes_codegraph_status)
    for codegraph_tool_name in (
        "hermes_codegraph_search",
        "hermes_codegraph_files",
        "hermes_codegraph_overview",
        "hermes_codegraph_inspect",
    ):
        add(globals()[codegraph_tool_name])
    add(hermes_git_status)
    add(hermes_git_diff)
    add(hermes_owner_run_command)
    add(hermes_owner_patch)
    add(hermes_owner_write_file)
    add(hermes_owner_repo_issue_create)

    REGISTERED_TOOL_NAMES[:] = names


mcp = build_server()


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes Agent MCP sidecar.")
    parser.add_argument("--http", action="store_true", help="Run streamable HTTP transport instead of stdio.")
    parser.add_argument("--sse", action="store_true", help="Run legacy SSE transport instead of stdio.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7677)
    parser.add_argument("--cert", help="Path to SSL certificate file (enables HTTPS)")
    parser.add_argument("--key", help="Path to SSL key file (enables HTTPS)")
    parser.add_argument(
        "--profile",
        choices=[LOCAL_DEV_PROFILE, REMOTE_PROFILE],
        default=LOCAL_DEV_PROFILE,
        help="Release safety profile. Remote no-auth is refused unless explicitly acknowledged.",
    )
    parser.add_argument(
        UNSAFE_REMOTE_ACK,
        action="store_true",
        dest="unsafe_remote_ack",
        help="Allow remote profile without auth. For experiments only; not release-safe.",
    )
    args = parser.parse_args()

    if args.http and args.sse:
        raise SystemExit("Choose only one of --http or --sse.")
    if args.profile == REMOTE_PROFILE and not (args.unsafe_remote_ack and env_enabled(UNSAFE_REMOTE_ENV)):
        raise SystemExit(
            "Remote profile requires real authentication, which is not implemented yet. "
            f"For temporary experiments only, pass {UNSAFE_REMOTE_ACK} and set {UNSAFE_REMOTE_ENV}=1."
        )
    if args.profile == LOCAL_DEV_PROFILE and not is_loopback_host(args.host):
        eprint(
            "WARNING: local-dev profile is bound to a non-loopback host. "
            "Do not expose randoku-sidecar without real authentication."
        )
    if args.profile == REMOTE_PROFILE:
        eprint("WARNING: remote no-auth mode is explicitly unsafe and intended only for temporary experiments.")

    transport = "streamable-http" if args.http else "sse" if args.sse else "stdio"
    global RUNNING_TRANSPORT
    RUNNING_TRANSPORT = transport
    server = build_server(host=args.host, port=args.port, http=args.http)
    if transport == "stdio":
        eprint("randoku-sidecar MCP server starting in stdio mode.")
        server.run(transport="stdio")
    else:
        path = "/mcp" if args.http else "/sse"
        eprint(f"randoku-sidecar MCP server running at http://{args.host}:{args.port}{path}")

        # Run with uvicorn instead of FastMCP.run() so TLS can be enabled for
        # local-only testing when cert/key are provided.
        import uvicorn
        app = server.streamable_http_app() if args.http else server.sse_app()

        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            ssl_certfile=args.cert if args.cert else None,
            ssl_keyfile=args.key if args.key else None,
            proxy_headers=True,
            forwarded_allow_ips="*",
        )


if __name__ == "__main__":
    main()
