from __future__ import annotations

import json
from typing import Any


def _json_error(message: str, **extra: Any) -> str:
    payload = {"success": False, "error": message}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, indent=2)


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
            return json.dumps(
                {
                    "success": True,
                    "provider": provider_name,
                    "provider_loaded": False,
                    "provider_available": False,
                    "session_id": session_id,
                    "platform": platform,
                    "effective_platform": effective_platform,
                    "query": clean_query,
                    "content": "",
                    "empty": True,
                    "note": note,
                },
                ensure_ascii=False,
                indent=2,
            )

        content = manager.prefetch_all(clean_query, session_id=session_id)
        return json.dumps(
            {
                "success": True,
                "provider": provider_name,
                "provider_loaded": True,
                "provider_available": True,
                "session_id": session_id,
                "platform": platform,
                "effective_platform": effective_platform,
                "query": clean_query,
                "content": content,
                "empty": not bool(content and content.strip()),
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:
        return _json_error(
            str(exc),
            session_id=session_id,
            platform=platform,
            effective_platform=effective_platform,
            query=clean_query,
        )
