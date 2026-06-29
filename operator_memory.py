from __future__ import annotations

import json
import os
import time
from typing import Any

import operator_policy as op_policy

# Phase-2 provider write-back (audit §4/§10).
#
# A memory provider exposes its own native tools (read AND write) through the
# neutral ``MemoryManager.get_all_tool_schemas()`` / ``handle_tool_call()``
# interface. The sidecar proxies an *allowlisted* subset of those tools so a
# caller can persist an already-distilled conclusion into the configured
# semantic layer — without the sidecar ever naming a provider (rule #1) or
# deciding what is worth remembering (audit §9).
#
# The allowlist is configuration, not code: which provider-native tool names
# may be proxied is read from this env var (comma-separated). Empty by default
# => disabled (audit §10 "disabled by default"). Naming a provider's tool here
# keeps the *code* provider-neutral; swapping providers is a config change.
WRITEBACK_ALLOWLIST_ENV = "RANDOKU_MEMORY_WRITEBACK_TOOLS"

# A freshly built per-call manager may still be initializing its provider
# session in a background thread (audit §6), so the first write can come back
# as a transient readiness error. These are GENERIC readiness substrings, not
# provider identifiers — matching them keeps the proxy provider-neutral while
# letting us retry the transient instead of reporting a false failure.
_TRANSIENT_HINTS = ("initializing", "try again", "temporarily", "not ready", "not yet")
_WRITE_MAX_ATTEMPTS = 4
_WRITE_RETRY_SLEEP_S = 0.75

# A freshly built per-call manager's prefetch is ASYNCHRONOUS (audit §6): the
# first ``prefetch_all`` fires the provider's background fetch and returns the
# currently-cached (empty) result; the landed content only surfaces on a later
# call once that background work completes. Because this tool builds a fresh
# manager, prefetches once, and tears it down immediately, a single call would
# always report empty even when the provider demonstrably has the data. So we
# poll the SAME warm manager with a small bounded backoff until content lands
# or the budget is exhausted. This is generic async-prefetch draining, not
# provider-specific code (rule #1).
_RECALL_MAX_ATTEMPTS = 4
_RECALL_RETRY_SLEEP_S = 1.5


def _json_error(message: str, **extra: Any) -> str:
    payload = {"success": False, "error": message}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _json_ok(**fields: Any) -> str:
    return json.dumps({"success": True, **fields}, ensure_ascii=False, indent=2)


def _load_config() -> dict[str, Any]:
    from hermes_cli.config import load_config

    cfg = load_config()
    return cfg if isinstance(cfg, dict) else {}


def _active_profile_name(default: str = "default") -> str:
    try:
        from hermes_cli.profiles import get_active_profile_name

        name = get_active_profile_name()
        if isinstance(name, str) and name.strip():
            return name.strip()
    except Exception:
        pass
    return default


def _hermes_home() -> str:
    from hermes_constants import get_hermes_home

    return str(get_hermes_home())


def _build_external_memory_manager(
    *,
    session_id: str = "",
    platform: str = "cli",
    profile: str = "default",
    warning_callback=None,
    status_callback=None,
):
    cfg = _load_config()
    mem_config = cfg.get("memory", {}) if isinstance(cfg.get("memory", {}), dict) else {}
    provider_name = str(mem_config.get("provider", "") or "").strip()
    if not provider_name:
        return None, provider_name, "No memory.provider configured."

    from agent.memory_manager import MemoryManager
    from plugins.memory import load_memory_provider

    provider = load_memory_provider(provider_name)
    if provider is None:
        raise RuntimeError(f"Configured memory provider {provider_name!r} could not be loaded.")
    if not provider.is_available():
        raise RuntimeError(f"Configured memory provider {provider_name!r} is not available.")

    manager = MemoryManager()
    manager.add_provider(provider)
    if not manager.providers:
        raise RuntimeError(f"Configured memory provider {provider_name!r} was not registered.")

    active_profile = profile or _active_profile_name()
    init_kwargs: dict[str, Any] = {
        "session_id": session_id,
        "platform": platform or "cli",
        "hermes_home": _hermes_home(),
        "agent_context": "sidecar",
        "agent_identity": active_profile,
        "agent_workspace": "hermes",
    }
    if warning_callback is not None:
        init_kwargs["warning_callback"] = warning_callback
    if status_callback is not None:
        init_kwargs["status_callback"] = status_callback

    manager.initialize_all(**init_kwargs)
    return manager, provider_name, ""


def hermes_external_context_recall(
    query: str,
    session_id: str = "",
    profile: str = "default",
    platform: str = "cli",
) -> str:
    """Read-only Hermes MemoryManager external auto-context prefetch."""
    clean_query = (query or "").strip()
    effective_platform = platform or "cli"
    if not clean_query:
        return _json_error(
            "query is required.",
            platform=platform,
            effective_platform=effective_platform,
        )

    try:
        manager, provider_name, note = _build_external_memory_manager(
            session_id=session_id,
            platform=effective_platform,
            profile=profile,
        )
        if manager is None:
            return _json_ok(
                provider=provider_name,
                provider_loaded=False,
                provider_available=False,
                session_id=session_id,
                platform=platform,
                effective_platform=effective_platform,
                query=clean_query,
                content="",
                empty=True,
                note=note,
            )

        try:
            content = manager.prefetch_all(clean_query, session_id=session_id)
            attempt = 1
            while (not (content and content.strip())) and attempt < _RECALL_MAX_ATTEMPTS:
                time.sleep(_RECALL_RETRY_SLEEP_S)
                content = manager.prefetch_all(clean_query, session_id=session_id)
                attempt += 1
        finally:
            try:
                manager.shutdown_all()
            except Exception:
                pass

        return _json_ok(
            provider=provider_name,
            provider_loaded=True,
            provider_available=True,
            session_id=session_id,
            platform=platform,
            effective_platform=effective_platform,
            query=clean_query,
            content=content,
            empty=not bool(content and content.strip()),
        )
    except Exception as exc:
        return _json_error(
            str(exc),
            session_id=session_id,
            platform=platform,
            effective_platform=effective_platform,
            query=clean_query,
        )


def writeback_allowlist() -> list[str]:
    """Provider-native tool names permitted for write-back, from config.

    Empty (the default) means provider write-back is disabled (audit §10).
    """
    raw = os.environ.get(WRITEBACK_ALLOWLIST_ENV, "") or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _looks_transient(message: str) -> bool:
    low = (message or "").lower()
    return any(hint in low for hint in _TRANSIENT_HINTS)


def _interpret_provider_result(raw: Any) -> tuple[bool, str, bool]:
    """Decide success/failure from a provider tool result.

    Provider tool handlers return a JSON string: success carries a
    ``result`` field, failure carries an ``error`` field (Hermes'
    ``tool_error`` shape). A provider write that silently fails (audit §5 —
    e.g. an uncached session) surfaces here as an ``error``; we MUST report
    that as failure and never fold it into a fake success (the orphan trap).

    Returns ``(ok, message, transient)``.
    """
    text = raw if isinstance(raw, str) else str(raw)
    try:
        parsed = json.loads(text)
    except Exception:
        # Unparseable response: be conservative and call it a failure rather
        # than inventing success from an opaque string.
        return False, text.strip() or "Empty provider response.", False
    if isinstance(parsed, dict):
        if "error" in parsed:
            message = str(parsed.get("error") or "Provider reported an error.")
            return False, message, _looks_transient(message)
        if "result" in parsed:
            return True, str(parsed.get("result", "")), False
        # Unknown shape — do not assume success.
        return False, text.strip(), False
    return False, text.strip(), False


def hermes_memory_provider_writeback(
    tool: str,
    args: dict[str, Any] | None = None,
    dry_run: bool = True,
) -> str:
    """Governed, allowlisted proxy for the memory provider's own write tools.

    The caller supplies an already-distilled write as the provider-native
    ``tool`` name plus its native ``args`` (transparent proxy — the sidecar
    maps nothing and decides nothing; audit §9). The sidecar only enforces
    policy and executes:

    - provider-neutral: tools are dispatched through ``MemoryManager`` and
      the permitted set comes from the ``RANDOKU_MEMORY_WRITEBACK_TOOLS``
      config allowlist, so no provider is named in code (rule #1);
    - governed: operator level ``skills_config`` + mutation gate, dry-run by
      default, every plan/write audited as length+hash (rule #3);
    - honest: the provider's own success/failure is verified and surfaced,
      never swallowed (audit §5);
    - clean: the per-call manager is always torn down in a ``finally`` path
      (audit §6); a transient session-init error is retried, not faked.

    ``args`` is passed to the provider verbatim. Scope decisions (e.g.
    create-only, default write target) are enforced by what the allowlist
    permits and by operator review of the dry-run plan — not by inspecting
    provider-specific argument names here, which would re-couple the sidecar
    to a provider.
    """
    clean_tool = (tool or "").strip()
    call_args: dict[str, Any] = args if isinstance(args, dict) else {}
    allowlist = writeback_allowlist()

    try:
        policy = op_policy.OperatorPolicy()
        policy.require_level("skills_config")

        if not clean_tool:
            return _json_error(
                "A provider tool name is required.",
                allowlist=allowlist,
                hint=f"Allowlist provider write tools via {WRITEBACK_ALLOWLIST_ENV}.",
            )
        if clean_tool not in allowlist:
            return _json_error(
                f"Provider tool {clean_tool!r} is not allowlisted for write-back.",
                tool=clean_tool,
                allowlist=allowlist,
                hint=f"Add it to {WRITEBACK_ALLOWLIST_ENV} (comma-separated) to permit it.",
            )

        provider_name = str(
            (_load_config().get("memory", {}) or {}).get("provider", "") or ""
        ).strip()
        arg_keys = sorted(str(k) for k in call_args.keys())
        args_payload = json.dumps(call_args, ensure_ascii=False, sort_keys=True)

        if policy.effective_dry_run(dry_run):
            record = op_policy.audit_record(
                tool="hermes_memory_provider_writeback",
                level=policy.level,
                apply_mode=policy.apply_mode,
                dry_run=True,
                success=True,
                changed=False,
                summary=f"dry-run provider write {clean_tool} -> {provider_name}",
                content=args_payload,
                extra={
                    "provider": provider_name,
                    "provider_tool": clean_tool,
                    "arg_keys": ",".join(arg_keys),
                },
            )
            return _json_ok(
                dry_run=True,
                plan={
                    "provider": provider_name,
                    "tool": clean_tool,
                    "arg_keys": arg_keys,
                    "args_len": record["content_len"],
                    "args_sha256": record["content_sha256"],
                },
            )

        policy.require_mutation(dry_run)

        manager, provider_name, note = _build_external_memory_manager(platform="cli")
        if manager is None:
            return _json_error(
                note or "No memory provider configured.",
                provider=provider_name,
                tool=clean_tool,
            )

        ok = False
        message = ""
        transient = False
        try:
            for attempt in range(1, _WRITE_MAX_ATTEMPTS + 1):
                raw = manager.handle_tool_call(clean_tool, call_args)
                ok, message, transient = _interpret_provider_result(raw)
                if ok or not transient:
                    break
                if attempt < _WRITE_MAX_ATTEMPTS:
                    time.sleep(_WRITE_RETRY_SLEEP_S)
        finally:
            try:
                manager.shutdown_all()
            except Exception:
                pass

        op_policy.audit_record(
            tool="hermes_memory_provider_writeback",
            level=policy.level,
            apply_mode=policy.apply_mode,
            dry_run=False,
            success=ok,
            changed=ok,
            summary=f"provider write {clean_tool} -> {provider_name}",
            error="" if ok else message,
            content=args_payload,
            extra={
                "provider": provider_name,
                "provider_tool": clean_tool,
                "arg_keys": ",".join(arg_keys),
            },
        )

        if not ok:
            return _json_error(
                message,
                provider=provider_name,
                tool=clean_tool,
                retryable=transient,
            )
        return _json_ok(
            provider=provider_name,
            tool=clean_tool,
            result=message,
        )
    except Exception as exc:
        return _json_error(str(exc), tool=clean_tool)
