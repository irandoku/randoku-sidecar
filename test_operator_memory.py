import json
from types import SimpleNamespace

import operator_memory as om


class FakeManager:
    def __init__(self):
        self.added = []
        self.initialized = None
        self.prefetched = None

    @property
    def providers(self):
        return list(self.added)

    def add_provider(self, provider):
        self.added.append(provider)

    def initialize_all(self, **kwargs):
        self.initialized = kwargs

    def prefetch_all(self, query, *, session_id=""):
        self.prefetched = {"query": query, "session_id": session_id}
        return "external context"


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
