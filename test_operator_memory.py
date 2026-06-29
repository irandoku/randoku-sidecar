import json
from types import SimpleNamespace

import pytest

import operator_memory as om
import operator_policy as op


class FakeManager:
    def __init__(self, results=None):
        self.added = []
        self.initialized = None
        self.prefetched = None
        # ``results`` lets a test simulate the provider's ASYNCHRONOUS prefetch:
        # a sequence of per-call return values (e.g. empty until the background
        # fetch lands). Default: content on the first call.
        self._results = list(results) if results is not None else ["external context"]
        self.prefetch_calls = 0
        self.shutdown_calls = 0

    @property
    def providers(self):
        return list(self.added)

    def add_provider(self, provider):
        self.added.append(provider)

    def initialize_all(self, **kwargs):
        self.initialized = kwargs

    def prefetch_all(self, query, *, session_id=""):
        self.prefetched = {"query": query, "session_id": session_id}
        idx = min(self.prefetch_calls, len(self._results) - 1)
        self.prefetch_calls += 1
        return self._results[idx]

    def shutdown_all(self):
        self.shutdown_calls += 1


def test_external_context_recall_requires_query():
    parsed = json.loads(om.hermes_external_context_recall(""))
    assert parsed["success"] is False
    assert "query" in parsed["error"]


def test_external_context_recall_no_provider_configured(monkeypatch):
    monkeypatch.setattr(om, "_load_config", lambda: {"memory": {}})

    parsed = json.loads(om.hermes_external_context_recall("alpha"))

    assert parsed["success"] is True
    assert parsed["provider_loaded"] is False
    assert parsed["empty"] is True
    assert parsed["content"] == ""


def test_external_context_recall_provider_unavailable(monkeypatch):
    provider = SimpleNamespace(is_available=lambda: False)
    monkeypatch.setattr(om, "_load_config", lambda: {"memory": {"provider": "honcho"}})

    def fake_build(*, session_id="", platform="cli", profile="default", **kwargs):
        raise RuntimeError("Configured memory provider 'honcho' is not available.")

    monkeypatch.setattr(om, "_build_external_memory_manager", fake_build)
    parsed = json.loads(om.hermes_external_context_recall("alpha"))

    assert parsed["success"] is False
    assert "not available" in parsed["error"]


def test_external_context_recall_prefetches_with_initialized_manager(monkeypatch):
    manager = FakeManager()

    def fake_build(*, session_id="", platform="cli", profile="default", **kwargs):
        manager.initialize_all(
            session_id=session_id,
            platform=platform,
            agent_context="sidecar",
            agent_identity=profile,
            agent_workspace="hermes",
        )
        return manager, "honcho", ""

    monkeypatch.setattr(om, "_build_external_memory_manager", fake_build)

    parsed = json.loads(
        om.hermes_external_context_recall(
            "memory bug",
            session_id="s1",
            profile="default",
            platform="chatgpt",
        )
    )

    assert parsed["success"] is True
    assert parsed["provider"] == "honcho"
    assert parsed["provider_loaded"] is True
    assert parsed["provider_available"] is True
    assert parsed["content"] == "external context"
    assert parsed["empty"] is False
    assert parsed["platform"] == "chatgpt"
    assert parsed["effective_platform"] == "chatgpt"
    assert manager.initialized["session_id"] == "s1"
    assert manager.initialized["platform"] == "chatgpt"
    assert manager.initialized["agent_context"] == "sidecar"
    assert manager.prefetched == {"query": "memory bug", "session_id": "s1"}
    assert manager.shutdown_calls == 1


def test_external_context_recall_waits_for_async_prefetch(monkeypatch):
    # The provider's prefetch is asynchronous: the first call returns empty
    # while the background fetch is in flight; later calls return the landed
    # content. The tool must poll the warm manager until content appears.
    manager = FakeManager(results=["", "", "landed context"])

    def fake_build(*, session_id="", platform="cli", profile="default", **kwargs):
        manager.initialize_all(
            session_id=session_id,
            platform=platform,
            agent_context="sidecar",
            agent_identity=profile,
            agent_workspace="hermes",
        )
        return manager, "honcho", ""

    monkeypatch.setattr(om, "_build_external_memory_manager", fake_build)
    monkeypatch.setattr(om.time, "sleep", lambda _s: None)

    parsed = json.loads(om.hermes_external_context_recall("memory bug"))

    assert parsed["success"] is True
    assert parsed["content"] == "landed context"
    assert parsed["empty"] is False
    assert manager.prefetch_calls == 3  # polled until non-empty
    assert manager.shutdown_calls == 1


def test_external_context_recall_gives_up_after_budget(monkeypatch):
    # If the background fetch never lands within the budget, the tool reports
    # empty rather than hanging — bounded by _RECALL_MAX_ATTEMPTS.
    manager = FakeManager(results=[""])

    def fake_build(*, session_id="", platform="cli", profile="default", **kwargs):
        manager.initialize_all(
            session_id=session_id,
            platform=platform,
            agent_context="sidecar",
            agent_identity=profile,
            agent_workspace="hermes",
        )
        return manager, "honcho", ""

    monkeypatch.setattr(om, "_build_external_memory_manager", fake_build)
    monkeypatch.setattr(om.time, "sleep", lambda _s: None)

    parsed = json.loads(om.hermes_external_context_recall("memory bug"))

    assert parsed["success"] is True
    assert parsed["content"] == ""
    assert parsed["empty"] is True
    assert manager.prefetch_calls == om._RECALL_MAX_ATTEMPTS
    assert manager.shutdown_calls == 1


def test_external_context_recall_defaults_to_cli_platform(monkeypatch):
    manager = FakeManager()

    def fake_build(*, session_id="", platform="cli", profile="default", **kwargs):
        manager.initialize_all(
            session_id=session_id,
            platform=platform,
            agent_context="sidecar",
            agent_identity=profile,
            agent_workspace="hermes",
        )
        return manager, "honcho", ""

    monkeypatch.setattr(om, "_build_external_memory_manager", fake_build)

    parsed = json.loads(om.hermes_external_context_recall("memory bug"))

    assert parsed["success"] is True
    assert parsed["platform"] == "cli"
    assert parsed["effective_platform"] == "cli"
    assert manager.initialized["platform"] == "cli"


def test_external_context_recall_empty_platform_reports_cli_fallback(monkeypatch):
    manager = FakeManager()

    def fake_build(*, session_id="", platform="cli", profile="default", **kwargs):
        manager.initialize_all(
            session_id=session_id,
            platform=platform,
            agent_context="sidecar",
            agent_identity=profile,
            agent_workspace="hermes",
        )
        return manager, "honcho", ""

    monkeypatch.setattr(om, "_build_external_memory_manager", fake_build)

    parsed = json.loads(om.hermes_external_context_recall("memory bug", platform=""))

    assert parsed["success"] is True
    assert parsed["platform"] == ""
    assert parsed["effective_platform"] == "cli"
    assert manager.initialized["platform"] == "cli"


def test_build_external_memory_manager_empty_platform_falls_back_to_cli(monkeypatch):
    manager = FakeManager()
    provider = SimpleNamespace(is_available=lambda: True)

    monkeypatch.setattr(om, "_load_config", lambda: {"memory": {"provider": "honcho"}})
    monkeypatch.setattr(om, "_hermes_home", lambda: "/tmp/hermes")

    import sys
    import types

    agent_module = types.ModuleType("agent.memory_manager")
    agent_module.MemoryManager = lambda: manager
    plugins_module = types.ModuleType("plugins.memory")
    plugins_module.load_memory_provider = lambda name: provider
    monkeypatch.setitem(sys.modules, "agent.memory_manager", agent_module)
    monkeypatch.setitem(sys.modules, "plugins.memory", plugins_module)

    built, provider_name, note = om._build_external_memory_manager(platform="")

    assert built is manager
    assert provider_name == "honcho"
    assert note == ""
    assert manager.initialized["platform"] == "cli"


# --- provider write-back proxy (phase 2) -----------------------------------


class FakeWriteManager:
    """Stand-in for a per-call MemoryManager on the write path."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.shutdown_called = 0

    def handle_tool_call(self, tool, args):
        self.calls.append((tool, dict(args)))
        idx = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[idx]

    def shutdown_all(self):
        self.shutdown_called += 1


def _enable_operator(monkeypatch, *, apply_mode="dry_run", allow="honcho_conclude"):
    monkeypatch.setenv("RANDOKU_OPERATOR_ENABLED", "1")
    monkeypatch.setenv("RANDOKU_OPERATOR_LEVEL", "skills_config")
    monkeypatch.setenv("RANDOKU_OPERATOR_APPLY_MODE", apply_mode)
    if allow is None:
        monkeypatch.delenv("RANDOKU_MEMORY_WRITEBACK_TOOLS", raising=False)
    else:
        monkeypatch.setenv("RANDOKU_MEMORY_WRITEBACK_TOOLS", allow)


@pytest.fixture
def audit_tmp(tmp_path):
    op.set_audit_log_override(tmp_path / "audit.jsonl")
    try:
        yield tmp_path / "audit.jsonl"
    finally:
        op.set_audit_log_override(None)


def test_provider_writeback_requires_operator_level(monkeypatch, audit_tmp):
    monkeypatch.delenv("RANDOKU_OPERATOR_ENABLED", raising=False)
    parsed = json.loads(om.hermes_memory_provider_writeback("honcho_conclude", {"conclusion": "x"}))
    assert parsed["success"] is False
    assert "disabled" in parsed["error"].lower()


def test_provider_writeback_rejects_non_allowlisted(monkeypatch, audit_tmp):
    _enable_operator(monkeypatch, allow=None)

    def boom(**kwargs):  # must not build a manager for a refused call
        raise AssertionError("manager must not be built for a refused call")

    monkeypatch.setattr(om, "_build_external_memory_manager", boom)

    parsed = json.loads(om.hermes_memory_provider_writeback("honcho_conclude", {"conclusion": "x"}))
    assert parsed["success"] is False
    assert "not allowlisted" in parsed["error"]
    assert parsed["allowlist"] == []


def test_provider_writeback_dry_run_returns_plan(monkeypatch, audit_tmp):
    _enable_operator(monkeypatch, apply_mode="dry_run")
    monkeypatch.setattr(om, "_load_config", lambda: {"memory": {"provider": "honcho"}})

    def boom(**kwargs):  # dry-run must not build a manager
        raise AssertionError("dry-run must not build a manager")

    monkeypatch.setattr(om, "_build_external_memory_manager", boom)

    parsed = json.loads(
        om.hermes_memory_provider_writeback("honcho_conclude", {"conclusion": "uncle prefers tabs"})
    )
    assert parsed["success"] is True
    assert parsed["dry_run"] is True
    assert parsed["plan"]["provider"] == "honcho"
    assert parsed["plan"]["tool"] == "honcho_conclude"
    assert parsed["plan"]["arg_keys"] == ["conclusion"]
    assert parsed["plan"]["args_len"] > 0
    assert len(parsed["plan"]["args_sha256"]) == 64
    # The raw conclusion text never appears in the response.
    assert "uncle prefers tabs" not in json.dumps(parsed)


def test_provider_writeback_direct_success_and_teardown(monkeypatch, audit_tmp):
    _enable_operator(monkeypatch, apply_mode="direct")
    monkeypatch.setattr(om, "_load_config", lambda: {"memory": {"provider": "honcho"}})
    manager = FakeWriteManager([json.dumps({"result": "Conclusion saved for user: x"})])

    monkeypatch.setattr(
        om, "_build_external_memory_manager", lambda **kwargs: (manager, "honcho", "")
    )

    parsed = json.loads(
        om.hermes_memory_provider_writeback("honcho_conclude", {"conclusion": "x"}, dry_run=False)
    )
    assert parsed["success"] is True
    assert parsed["provider"] == "honcho"
    assert parsed["result"] == "Conclusion saved for user: x"
    # args forwarded verbatim; manager torn down exactly once
    assert manager.calls == [("honcho_conclude", {"conclusion": "x"})]
    assert manager.shutdown_called == 1


def test_provider_writeback_surfaces_provider_failure(monkeypatch, audit_tmp):
    _enable_operator(monkeypatch, apply_mode="direct")
    monkeypatch.setattr(om, "_load_config", lambda: {"memory": {"provider": "honcho"}})
    # The audit §5 silent-skip trap: provider returns an error; must never
    # be folded into a fake success.
    manager = FakeWriteManager([json.dumps({"error": "Failed to save conclusion."})])
    monkeypatch.setattr(
        om, "_build_external_memory_manager", lambda **kwargs: (manager, "honcho", "")
    )

    parsed = json.loads(
        om.hermes_memory_provider_writeback("honcho_conclude", {"conclusion": "x"}, dry_run=False)
    )
    assert parsed["success"] is False
    assert parsed["error"] == "Failed to save conclusion."
    assert parsed["retryable"] is False
    assert manager.shutdown_called == 1


def test_provider_writeback_retries_transient_then_succeeds(monkeypatch, audit_tmp):
    _enable_operator(monkeypatch, apply_mode="direct")
    monkeypatch.setattr(om, "_load_config", lambda: {"memory": {"provider": "honcho"}})
    monkeypatch.setattr(om.time, "sleep", lambda *_: None)
    manager = FakeWriteManager(
        [
            json.dumps({"error": "Honcho session is still initializing; try again shortly."}),
            json.dumps({"result": "Conclusion saved for user: x"}),
        ]
    )
    monkeypatch.setattr(
        om, "_build_external_memory_manager", lambda **kwargs: (manager, "honcho", "")
    )

    parsed = json.loads(
        om.hermes_memory_provider_writeback("honcho_conclude", {"conclusion": "x"}, dry_run=False)
    )
    assert parsed["success"] is True
    assert len(manager.calls) == 2  # retried the transient, then succeeded
    assert manager.shutdown_called == 1


def test_provider_writeback_transient_exhausted_reports_retryable(monkeypatch, audit_tmp):
    _enable_operator(monkeypatch, apply_mode="direct")
    monkeypatch.setattr(om, "_load_config", lambda: {"memory": {"provider": "honcho"}})
    monkeypatch.setattr(om.time, "sleep", lambda *_: None)
    manager = FakeWriteManager(
        [json.dumps({"error": "Honcho session is still initializing; try again shortly."})]
    )
    monkeypatch.setattr(
        om, "_build_external_memory_manager", lambda **kwargs: (manager, "honcho", "")
    )

    parsed = json.loads(
        om.hermes_memory_provider_writeback("honcho_conclude", {"conclusion": "x"}, dry_run=False)
    )
    assert parsed["success"] is False
    assert parsed["retryable"] is True
    assert len(manager.calls) == om._WRITE_MAX_ATTEMPTS
    assert manager.shutdown_called == 1
