from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_MCP_URL = "http://127.0.0.1:4750/mcp"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _extract_json_response(raw: str) -> dict[str, Any]:
    """Parse plain JSON or simple text/event-stream JSON payloads."""
    raw = raw.strip()
    if not raw:
        raise ValueError("empty response body")
    if raw.startswith("data:"):
        data_lines = []
        for line in raw.splitlines():
            if line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        raw = "\n".join(data_lines).strip()
    return json.loads(raw)


def mcp_call(mcp_url: str, name: str, args: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": args or {}},
    }
    request = urllib.request.Request(
        mcp_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - local/manual verifier
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"MCP request failed for {name}: {exc}") from exc
    try:
        return _extract_json_response(raw)
    except Exception as exc:
        preview = raw[:500].replace("\n", "\\n")
        raise RuntimeError(f"Could not parse MCP response for {name}: {exc}; body={preview!r}") from exc


def result_text(resp: dict[str, Any]) -> str:
    content = resp.get("result", {}).get("content", [])
    if not content:
        return ""
    first = content[0]
    return first.get("text", "") if isinstance(first, dict) else ""


def result_is_error(resp: dict[str, Any]) -> bool:
    result = resp.get("result", {})
    if result.get("isError") is True:
        return True
    text = result_text(resp).lower()
    return "error" in text or "not found" in text or "no such file" in text


def record(results: list[tuple[str, bool, str]], name: str, passed: bool, detail: str) -> None:
    results.append((name, passed, detail))


def main() -> int:
    mcp_url = _env("MCP_URL", DEFAULT_MCP_URL)
    project_path = Path(_env("VERIFY_PROJECT_PATH", str(_repo_root())) or str(_repo_root())).expanduser().resolve()
    skill_path = _env("VERIFY_SKILL_PATH")
    skill_name = _env("VERIFY_SKILL_NAME")
    timeout = int(_env("VERIFY_TIMEOUT", "30") or "30")

    results: list[tuple[str, bool, str]] = []

    print(f"MCP_URL={mcp_url}")
    print(f"VERIFY_PROJECT_PATH={project_path}")
    print(f"VERIFY_SKILL_PATH={skill_path or '<unset: skipped>'}")
    print(f"VERIFY_SKILL_NAME={skill_name or '<unset: skipped>'}")
    print()

    try:
        t0 = time.time()
        resp = mcp_call(mcp_url, "hermes_skill_list", timeout=timeout)
        elapsed = time.time() - t0
        text = result_text(resp)
        lines = text.splitlines()
        names = {line.split(" - ")[0][2:].strip().lower() for line in lines if line.startswith("- ")}
        record(results, "hermes_skill_list", bool(text) and len(names) < 500, f"{len(lines)} lines, {len(names)} unique, {elapsed:.1f}s")

        pyproject = project_path / "pyproject.toml"
        t0 = time.time()
        resp = mcp_call(mcp_url, "hermes_read_file", {"path": str(pyproject), "offset": 1, "limit": 40}, timeout=timeout)
        elapsed = time.time() - t0
        text = result_text(resp)
        record(results, "read project pyproject", "build-system" in text, f"{len(text)} chars, {elapsed:.1f}s")

        t0 = time.time()
        resp = mcp_call(
            mcp_url,
            "hermes_search_files",
            {"pattern": "version", "target": "content", "path": str(project_path), "file_glob": "pyproject.toml", "limit": 5},
            timeout=timeout,
        )
        elapsed = time.time() - t0
        text = result_text(resp)
        record(results, "search project pyproject", "version" in text.lower(), f"{len(text)} chars, {elapsed:.1f}s")

        missing = project_path / "__randoku_sidecar_missing_verify_file__.txt"
        resp = mcp_call(mcp_url, "hermes_read_file", {"path": str(missing)}, timeout=timeout)
        record(results, "error handling", result_is_error(resp), result_text(resp)[:120].replace("\n", " "))

        if skill_path:
            t0 = time.time()
            resp = mcp_call(mcp_url, "hermes_read_file", {"path": skill_path, "offset": 1, "limit": 40}, timeout=timeout)
            elapsed = time.time() - t0
            text = result_text(resp)
            record(results, "optional skill path read", bool(text), f"{len(text)} chars, {elapsed:.1f}s")
        else:
            record(results, "optional skill path read", True, "skipped")

        if skill_name:
            t0 = time.time()
            resp = mcp_call(mcp_url, "hermes_skill_view", {"name": skill_name}, timeout=timeout)
            elapsed = time.time() - t0
            text = result_text(resp)
            record(results, "optional skill view", bool(text), f"{len(text)} chars, {elapsed:.1f}s")
        else:
            record(results, "optional skill view", True, "skipped")
    except Exception as exc:
        print(f"[FAIL] verifier setup/runtime: {exc}")
        return 1

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")

    all_pass = all(passed for _, passed, _ in results)
    print(f"\nOverall: {'ALL PASS' if all_pass else 'SOME FAILURES'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
