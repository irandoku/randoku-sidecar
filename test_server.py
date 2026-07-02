import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

import server


GATE_ENVS = [
    server.ENABLE_WRITE_ENV,
    server.ENABLE_SESSION_SEARCH_ENV,
    server.ENABLE_TERMINAL_ENV,
    server.UNSAFE_REMOTE_ENV,
]

OPERATOR_ENVS = [
    server.op_policy.OPERATOR_ENABLED_ENV,
    server.op_policy.OPERATOR_LEVEL_ENV,
    server.op_policy.OPERATOR_APPLY_MODE_ENV,
]


def clear_gate_envs(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in GATE_ENVS + OPERATOR_ENVS:
        monkeypatch.delenv(name, raising=False)


def enable_memory_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable governed memory writes: operator on, skills_config level, direct."""
    monkeypatch.setenv(server.op_policy.OPERATOR_ENABLED_ENV, "1")
    monkeypatch.setenv(server.op_policy.OPERATOR_LEVEL_ENV, "skills_config")
    monkeypatch.setenv(server.op_policy.OPERATOR_APPLY_MODE_ENV, "direct")


def tool_names(mcp_server) -> list[str]:
    tools = asyncio.run(mcp_server.list_tools())
    return sorted(tool.name for tool in tools)


def tools_by_name(mcp_server):
    tools = asyncio.run(mcp_server.list_tools())
    return {tool.name: tool for tool in tools}


def test_default_tool_surface_is_read_or_local_metadata_only(monkeypatch):
    clear_gate_envs(monkeypatch)

    built = server.build_server()
    names = tool_names(built)

    # Original read-only / local-metadata tools must still be present.
    for required in [
        "hermes_memory",
        "hermes_read_file",
        "hermes_search_files",
        "hermes_skill_list",
        "hermes_skill_view",
    ]:
        assert required in names

    # Broad mutating tools must NOT be exposed without their env flags.
    for forbidden in [
        "hermes_write_file",
        "hermes_patch",
        "hermes_run_command",
        "hermes_session_search",
    ]:
        assert forbidden not in names

    # Operator / Owner Mode tools are always registered (with refusal when
    # the policy is disabled). Verify the core read-only + representative
    # mutating tools are present.
    for operator_tool in [
        "hermes_operator_policy",
        "hermes_operator_status",
        "hermes_operator_audit_tail",
        "hermes_cron_list",
        "hermes_cron_status",
        "hermes_skill_diff",
        "hermes_config_get",
        "hermes_env_status",
        "hermes_gateway_status",
        "hermes_git_status",
        "hermes_git_diff",
        "hermes_cron_run",
        "hermes_skill_create",
        "hermes_owner_run_command",
    ]:
        assert operator_tool in names

    for tool in tools_by_name(built).values():
        assert tool.meta == {"securitySchemes": [{"type": "noauth"}]}


def test_operator_status_tool_list_matches_real_registry(monkeypatch):
    """registered_operator_tools must be derived from the actual registration,
    not a hand-maintained list that can drift (issue #6)."""
    clear_gate_envs(monkeypatch)

    built = server.build_server()
    status = json.loads(server.hermes_operator_status())

    assert status["registered_operator_tools"] == tool_names(built)
    # Tools the old hardcoded list had drifted away from.
    for present in [
        "hermes_workspace_apply_diff",
        "hermes_memory_provider_writeback",
        "hermes_memory_provider_read",
        "hermes_external_context_recall",
        "hermes_codegraph_status",
        "hermes_codegraph_search",
    ]:
        assert present in status["registered_operator_tools"]


def test_operator_status_tool_list_tracks_env_gates(monkeypatch):
    clear_gate_envs(monkeypatch)
    monkeypatch.setenv(server.ENABLE_WRITE_ENV, "1")

    built = server.build_server()
    status = json.loads(server.hermes_operator_status())

    assert status["registered_operator_tools"] == tool_names(built)
    assert "hermes_write_file" in status["registered_operator_tools"]


def test_env_gates_expose_high_risk_tools(monkeypatch):
    clear_gate_envs(monkeypatch)
    monkeypatch.setenv(server.ENABLE_WRITE_ENV, "1")
    monkeypatch.setenv(server.ENABLE_TERMINAL_ENV, "1")
    monkeypatch.setenv(server.ENABLE_SESSION_SEARCH_ENV, "1")

    names = tool_names(server.build_server())

    assert "hermes_write_file" in names
    assert "hermes_patch" in names
    assert "hermes_run_command" in names
    assert "hermes_session_search" in names


def test_memory_write_refuses_without_operator_mode(monkeypatch):
    clear_gate_envs(monkeypatch)
    monkeypatch.setattr(server, "require_imports", lambda: None)
    monkeypatch.setattr(
        server,
        "memory_tool",
        SimpleNamespace(memory_tool=lambda **kwargs: "should not be called"),
    )

    # Operator mode off → require_level refuses before any store load or write.
    with pytest.raises(RuntimeError, match="Operator mode is disabled"):
        server.hermes_memory(action="add", target="memory", content="x", dry_run=False)


def test_memory_write_dry_run_returns_plan_without_writing(monkeypatch):
    clear_gate_envs(monkeypatch)
    enable_memory_writes(monkeypatch)
    monkeypatch.setattr(server, "require_imports", lambda: None)
    monkeypatch.setattr(
        server,
        "memory_tool",
        SimpleNamespace(memory_tool=lambda **kwargs: pytest.fail("must not write on dry-run")),
    )

    out = server.hermes_memory(action="add", target="user", content="uncle prefers terse logs", dry_run=True)
    parsed = json.loads(out)
    assert parsed["success"] is True
    assert parsed["dry_run"] is True
    assert parsed["plan"]["action"] == "add"
    assert parsed["plan"]["target"] == "user"
    assert parsed["plan"]["file"] == "USER.md"
    assert parsed["plan"]["content_len"] == len("uncle prefers terse logs")
    assert len(parsed["plan"]["content_sha256"]) == 64


def test_memory_write_refuses_direct_without_apply_mode(monkeypatch):
    clear_gate_envs(monkeypatch)
    monkeypatch.setenv(server.op_policy.OPERATOR_ENABLED_ENV, "1")
    monkeypatch.setenv(server.op_policy.OPERATOR_LEVEL_ENV, "skills_config")
    # apply_mode left at default (dry_run): a dry_run=False call must refuse.
    monkeypatch.setattr(server, "require_imports", lambda: None)
    monkeypatch.setattr(
        server,
        "memory_tool",
        SimpleNamespace(memory_tool=lambda **kwargs: pytest.fail("must not write")),
    )

    # effective_dry_run forces a plan when apply_mode != direct, so no write
    # happens and require_mutation is never reached; the result is a safe plan.
    out = server.hermes_memory(action="add", target="memory", content="x", dry_run=False)
    parsed = json.loads(out)
    assert parsed["success"] is True
    assert parsed["dry_run"] is True


def test_memory_search_remains_available(monkeypatch):
    clear_gate_envs(monkeypatch)

    class FakeMemoryStore:
        def __init__(self):
            self.memory_entries = []
            self.user_entries = []

        def load_from_disk(self):
            self.memory_entries = ["alpha bug", "beta note", "ALPHA uppercase"]
            self.user_entries = ["user prefers quiet logs"]

    monkeypatch.setattr(server, "require_imports", lambda: None)
    monkeypatch.setattr(server, "memory_tool", SimpleNamespace(MemoryStore=FakeMemoryStore))

    result = json.loads(server.hermes_memory(action="search", target="memory", content="alpha"))
    assert result == {
        "success": True,
        "target": "memory",
        "query": "alpha",
        "count": 2,
        "matches": ["alpha bug", "ALPHA uppercase"],
    }


def test_memory_write_passes_loaded_store_when_enabled(monkeypatch):
    clear_gate_envs(monkeypatch)
    enable_memory_writes(monkeypatch)
    captured = {}

    class FakeMemoryStore:
        def __init__(self):
            self.loaded = False
            self.memory_entries = []
            self.user_entries = []

        def load_from_disk(self):
            self.loaded = True

    def fake_memory_tool(**kwargs):
        captured.update(kwargs)
        return "memory write ok"

    monkeypatch.setattr(server, "require_imports", lambda: None)
    monkeypatch.setattr(
        server,
        "memory_tool",
        SimpleNamespace(MemoryStore=FakeMemoryStore, memory_tool=fake_memory_tool),
    )

    assert server.hermes_memory(action="add", target="memory", content="x", dry_run=False) == "memory write ok"
    assert captured["action"] == "add"
    assert captured["target"] == "memory"
    assert captured["content"] == "x"
    assert captured["store"].loaded is True


def test_terminal_direct_call_is_disabled_by_default(monkeypatch):
    clear_gate_envs(monkeypatch)
    monkeypatch.setattr(server, "require_imports", lambda: None)
    monkeypatch.setattr(
        server,
        "terminal_tool",
        SimpleNamespace(terminal_tool=lambda **kwargs: "should not be called"),
    )

    with pytest.raises(RuntimeError, match=server.ENABLE_TERMINAL_ENV):
        server.hermes_run_command("echo nope")


def test_terminal_timeout_is_capped_when_enabled(monkeypatch):
    clear_gate_envs(monkeypatch)
    monkeypatch.setenv(server.ENABLE_TERMINAL_ENV, "1")
    captured = {}

    def fake_terminal_tool(command, timeout=None, workdir=None):
        captured.update({"command": command, "timeout": timeout, "workdir": workdir})
        return "ok"

    monkeypatch.setattr(server, "require_imports", lambda: None)
    monkeypatch.setattr(server, "terminal_tool", SimpleNamespace(terminal_tool=fake_terminal_tool))

    assert server.hermes_run_command("echo ok", timeout=999) == "ok"
    assert captured["timeout"] == 120


def test_remote_profile_requires_explicit_unsafe_ack(monkeypatch):
    clear_gate_envs(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["server.py", "--http", "--profile", "remote"])

    with pytest.raises(SystemExit, match="Remote profile requires real authentication"):
        server.main()


def test_default_hermes_root_normalizes_profile_scoped_env(monkeypatch):
    monkeypatch.setenv(
        "HERMES_HOME", r"C:\Users\example\AppData\Local\hermes\profiles\example-profile"
    )
    assert server._default_hermes_root() == Path(r"C:\Users\example\AppData\Local\hermes")
    assert server._hermes_root_for_operator() == Path(r"C:\Users\example\AppData\Local\hermes")


def test_external_context_recall_wrapper_defaults_to_cli(monkeypatch):
    captured = {}

    def fake_recall(**kwargs):
        captured.update(kwargs)
        return "{}"

    monkeypatch.setattr(server.op_memory, "hermes_external_context_recall", fake_recall)

    assert server.hermes_external_context_recall("memory bug") == "{}"
    assert captured == {
        "query": "memory bug",
        "session_id": "",
        "profile": "default",
        "platform": "cli",
    }


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_http_initialize_smoke(monkeypatch):
    port = free_port()
    env = os.environ.copy()
    for name in GATE_ENVS:
        env.pop(name, None)

    proc = subprocess.Popen(
        [sys.executable, "server.py", "--http", "--host", "127.0.0.1", "--port", str(port)],
        cwd=os.path.dirname(__file__),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        deadline = time.time() + 10
        last_error = None
        response_text = None
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1"},
            },
        }
        data = json.dumps(payload).encode("utf-8")
        while time.time() < deadline:
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/mcp",
                    data=data,
                    method="POST",
                    headers={
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(request, timeout=2) as response:
                    response_text = response.read().decode("utf-8")
                    break
            except Exception as exc:
                last_error = exc
                time.sleep(0.25)
        if response_text is None:
            raise AssertionError(f"HTTP MCP server did not respond: {last_error}")

        parsed = json.loads(response_text)
        assert parsed["result"]["serverInfo"]["name"] == "randoku-sidecar"
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


# ---------------------------------------------------------------------------
# hermes_operator_doctor (issue #8)
# ---------------------------------------------------------------------------

DOCTOR_CHECKS = [
    "runtime_imports",
    "operator_policy",
    "registered_tools",
    "memory_provider",
    "session_search",
    "codegraph",
    "env_parity",
]


def test_operator_doctor_report_shape(monkeypatch):
    clear_gate_envs(monkeypatch)
    server.build_server()

    report = json.loads(server.hermes_operator_doctor())

    assert report["success"] is True
    assert report["overall_status"] in ("PASS", "WARN", "FAIL")
    assert report["transport"] == "unknown"  # not served via main() in tests
    assert len(report["trace_id"]) == 16
    assert sorted(report["checks"]) == sorted(DOCTOR_CHECKS)
    for check in report["checks"].values():
        assert check["status"] in ("PASS", "WARN", "FAIL", "UNSUPPORTED")
        for field in ("layer", "code", "message", "suggested_action"):
            assert check[field]


def test_operator_doctor_never_leaks_home_path(monkeypatch):
    clear_gate_envs(monkeypatch)
    server.build_server()

    raw = server.hermes_operator_doctor()
    assert str(Path.home()) not in raw


def test_operator_doctor_warns_on_direct_apply_mode(monkeypatch):
    clear_gate_envs(monkeypatch)
    monkeypatch.setenv(server.op_policy.OPERATOR_ENABLED_ENV, "1")
    monkeypatch.setenv(server.op_policy.OPERATOR_LEVEL_ENV, "workspace")
    monkeypatch.setenv(server.op_policy.OPERATOR_APPLY_MODE_ENV, "direct")
    server.build_server()

    report = json.loads(server.hermes_operator_doctor())

    assert report["checks"]["operator_policy"]["status"] == "WARN"
    assert report["checks"]["operator_policy"]["code"] == "DIRECT_APPLY_MODE"
    assert "operator_policy" in report["warnings"]
    assert report["overall_status"] in ("WARN", "FAIL")


def test_operator_doctor_warns_on_high_risk_tools(monkeypatch):
    clear_gate_envs(monkeypatch)
    monkeypatch.setenv(server.ENABLE_WRITE_ENV, "1")
    server.build_server()

    check = json.loads(server.hermes_operator_doctor())["checks"]["registered_tools"]
    assert check["status"] == "WARN"
    assert check["code"] == "HIGH_RISK_TOOLS_EXPOSED"
    assert "hermes_write_file" in check["details"]["high_risk"]


def test_operator_doctor_fails_when_no_tools_registered(monkeypatch):
    clear_gate_envs(monkeypatch)
    monkeypatch.setattr(server, "REGISTERED_TOOL_NAMES", [])

    report = json.loads(server.hermes_operator_doctor())

    assert report["checks"]["registered_tools"]["status"] == "FAIL"
    assert report["overall_status"] == "FAIL"
    assert report["ok"] is False


def test_operator_doctor_env_parity_unsupported_without_start_sh(monkeypatch, tmp_path):
    clear_gate_envs(monkeypatch)
    server.build_server()
    # Point the module's __file__ at a directory with no start.sh.
    monkeypatch.setattr(server, "__file__", str(tmp_path / "server.py"))

    report = json.loads(server.hermes_operator_doctor())

    check = report["checks"]["env_parity"]
    assert check["status"] == "UNSUPPORTED"
    assert check["details"]["action"] == "manual"
    assert "env_parity" in report["unsupported"]


def test_operator_doctor_env_parity_reports_names_only(monkeypatch):
    clear_gate_envs(monkeypatch)  # operator envs unset -> divergence vs start.sh
    server.build_server()

    check = json.loads(server.hermes_operator_doctor())["checks"]["env_parity"]
    assert check["status"] in ("PASS", "WARN")
    if check["status"] == "WARN":
        assert check["code"] == "ENV_PARITY_DIVERGENCE"
        # Names only, never values.
        for name in check["details"]["missing_in_this_process"]:
            assert "=" not in name


def test_operator_doctor_is_registered(monkeypatch):
    clear_gate_envs(monkeypatch)
    assert "hermes_operator_doctor" in tool_names(server.build_server())


# ---------------------------------------------------------------------------
# hermes_release_doctor (issue #9)
# ---------------------------------------------------------------------------

RELEASE_CHECKS = [
    "compile",
    "secret_files",
    "working_tree",
    "version_changelog",
    "docs_tool_count",
    "release_posture",
]


def _fake_git_runner(*, dirty=False, ls_files="server.py\n"):
    def runner(argv, timeout=30, workdir=None):
        if argv[:2] == ["git", "ls-files"]:
            return (0, ls_files, "")
        if argv[:2] == ["git", "status"]:
            return (0, "M server.py\n" if dirty else "", "")
        if argv[-2:] == ["pytest", "-q"] or "pytest" in argv:
            return (1, "", "1 failed")
        return (0, "", "")

    return runner


def test_release_doctor_report_shape(monkeypatch):
    clear_gate_envs(monkeypatch)
    server.build_server()

    report = json.loads(server.hermes_release_doctor())

    assert report["success"] is True
    assert report["overall_status"] in ("PASS", "WARN", "FAIL")
    assert report["full_tests"] is False
    assert sorted(report["checks"]) == sorted(RELEASE_CHECKS)  # no tests check unless opted in
    assert len(report["trace_id"]) == 16
    for check in report["checks"].values():
        assert check["status"] in ("PASS", "WARN", "FAIL", "UNSUPPORTED")
        assert check["layer"] == "release"


def test_release_doctor_warns_on_dirty_tree(monkeypatch):
    clear_gate_envs(monkeypatch)
    server.build_server()

    checks = server._release_doctor_impl(False, 60, runner=_fake_git_runner(dirty=True))

    assert checks["working_tree"]["status"] == "WARN"
    assert checks["working_tree"]["code"] == "DIRTY_TREE"


def test_release_doctor_blocks_on_secret_files(monkeypatch):
    clear_gate_envs(monkeypatch)
    server.build_server()

    checks = server._release_doctor_impl(
        False, 60, runner=_fake_git_runner(ls_files="server.py\nconfig/.env\n"),
    )

    assert checks["secret_files"]["status"] == "FAIL"
    assert checks["secret_files"]["blocking"] is True
    assert checks["secret_files"]["code"] == "SECRET_FILE_DETECTED"


def test_release_doctor_blocks_on_failing_tests(monkeypatch):
    clear_gate_envs(monkeypatch)
    server.build_server()

    checks = server._release_doctor_impl(True, 60, runner=_fake_git_runner())

    assert checks["tests"]["status"] == "FAIL"
    assert checks["tests"]["blocking"] is True


def test_release_doctor_no_false_drift_with_env_gates(monkeypatch):
    """Regression (issue #9 smoke test): a gated process (e.g. the tunnel with
    session search on) must not report doc drift — the documented count is the
    no-toggles baseline."""
    clear_gate_envs(monkeypatch)
    monkeypatch.setenv(server.ENABLE_SESSION_SEARCH_ENV, "1")
    monkeypatch.setenv(server.ENABLE_WRITE_ENV, "1")
    server.build_server()

    checks = server._release_doctor_impl(False, 60, runner=_fake_git_runner())

    assert checks["docs_tool_count"]["status"] == "PASS"
    assert set(checks["docs_tool_count"]["details"]["env_gated_registered"]) == {
        "hermes_session_search",
        "hermes_session_read",
        "hermes_session_recall",
        "hermes_write_file",
        "hermes_patch",
    }


def test_release_doctor_detects_doc_count_drift(monkeypatch):
    clear_gate_envs(monkeypatch)
    server.build_server()
    monkeypatch.setattr(server, "REGISTERED_TOOL_NAMES", ["only_one_tool"])

    checks = server._release_doctor_impl(False, 60, runner=_fake_git_runner())

    assert checks["docs_tool_count"]["status"] == "WARN"
    assert checks["docs_tool_count"]["code"] == "DOC_COUNT_DRIFT"


def test_release_doctor_warns_on_elevated_posture(monkeypatch):
    clear_gate_envs(monkeypatch)
    monkeypatch.setenv(server.op_policy.OPERATOR_ENABLED_ENV, "1")
    monkeypatch.setenv(server.op_policy.OPERATOR_APPLY_MODE_ENV, "direct")
    server.build_server()

    checks = server._release_doctor_impl(False, 60, runner=_fake_git_runner())

    assert checks["release_posture"]["status"] == "WARN"
    assert checks["release_posture"]["code"] == "ELEVATED_POSTURE"


def test_release_doctor_is_registered(monkeypatch):
    clear_gate_envs(monkeypatch)
    assert "hermes_release_doctor" in tool_names(server.build_server())
