#!/usr/bin/env python3
"""MCP server end-to-end tests.

Spawns the actual MCP server as a subprocess and exchanges JSON-RPC over
stdin/stdout, verifying:
  - initialize handshake
  - tools/list returns the skill_manage tool
  - tools/call(action='create') writes a real SKILL.md
  - tools/call(action='list') is not in the schema → invalid action rejected
  - HARD constraints are enforced via the tool

Run: python3 ~/.skill-system/tests/test_mcp.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

SYSTEM_ROOT = Path(__file__).resolve().parent.parent
MCP_BIN = SYSTEM_ROOT / "bin" / "skill-manage-mcp"

GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")
    sys.exit(1)


class MCPClient:
    """Minimal JSON-RPC over stdio client."""

    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.proc: Optional[subprocess.Popen] = None
        self._id = 0

    def start(self) -> None:
        env = {**os.environ, "SKILLS_DIR": str(self.skills_dir)}
        self.proc = subprocess.Popen(
            [str(MCP_BIN)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            bufsize=1,
        )

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        return json.loads(line)

    def stop(self) -> None:
        if self.proc:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def test_initialize(client: MCPClient) -> None:
    r = client.call("initialize", {
        "protocolVersion": "2024-11-05",
        "clientInfo": {"name": "test", "version": "0"},
        "capabilities": {},
    })
    assert r.get("jsonrpc") == "2.0", r
    assert r.get("result", {}).get("serverInfo", {}).get("name") == "hermes-skill-system"
    assert "tools" in r.get("result", {}).get("capabilities", {})
    ok("initialize handshake (serverInfo + capabilities)")


def test_ping(client: MCPClient) -> None:
    r = client.call("ping")
    assert "result" in r
    ok("ping")


def test_tools_list(client: MCPClient) -> None:
    r = client.call("tools/list")
    tools = r["result"]["tools"]
    assert len(tools) == 1, r
    t = tools[0]
    assert t["name"] == "skill_manage"
    desc = t["description"]
    assert "60 characters" in desc, "description must mention 60-char limit"
    assert "hermes-skill-system" in desc, "description must mention required author"
    assert "When to Use" in desc, "description must mention 8 sections"
    schema = t["inputSchema"]
    assert "action" in schema["properties"]
    assert "name" in schema["properties"]
    assert set(schema["properties"]["action"]["enum"]) == {
        "create", "edit", "patch", "delete", "write_file", "remove_file"
    }
    assert schema["required"] == ["action", "name"]
    ok("tools/list returns skill_manage with proper schema + HARD constraint docs")


def test_create_skill(client: MCPClient, tmp: Path) -> None:
    content = (
        "---\n"
        "name: mcp-test\n"
        "description: MCP end-to-end test skill.\n"
        "version: 0.1.0\n"
        "author: hermes-skill-system\n"
        "---\n\n# MCP Test\n\n## When to Use\n- mcp\n- test\n"
    )
    r = client.call("tools/call", {
        "name": "skill_manage",
        "arguments": {"action": "create", "name": "mcp-test", "content": content},
    })
    assert "result" in r, r
    is_error = r["result"].get("isError", False)
    text = r["result"]["content"][0]["text"]
    parsed = json.loads(text)
    assert parsed.get("success"), text
    assert not is_error
    assert (tmp / "mcp-test" / "SKILL.md").exists()
    ok("tools/call action='create' writes SKILL.md + returns success JSON")


def test_create_rejects_61_char_desc(client: MCPClient) -> None:
    long_desc = "a" * 61
    assert len(long_desc) == 61, f"test fixture wrong: {len(long_desc)} chars"
    bad = (
        "---\n"
        f"name: bad-desc-mcp\n"
        f"description: {long_desc}\n"
        "version: 0.1.0\n"
        "author: hermes-skill-system\n"
        "---\n\n# Bad\n\n## When to Use\n- x\n"
    )
    r = client.call("tools/call", {
        "name": "skill_manage",
        "arguments": {"action": "create", "name": "bad-desc-mcp", "content": bad},
    })
    assert r["result"]["isError"] is True
    text = json.loads(r["result"]["content"][0]["text"])
    assert "60" in text["error"]
    ok("tools/call rejects 61-char description with isError=true")


def test_create_rejects_bad_author(client: MCPClient) -> None:
    bad = (
        "---\n"
        "name: bad-author-mcp\n"
        "description: Short.\n"
        "version: 0.1.0\n"
        "author: yuzai\n"
        "---\n\n# Bad\n\n## When to Use\n- x\n"
    )
    r = client.call("tools/call", {
        "name": "skill_manage",
        "arguments": {"action": "create", "name": "bad-author-mcp", "content": bad},
    })
    assert r["result"]["isError"] is True
    text = json.loads(r["result"]["content"][0]["text"])
    assert "hermes-skill-system" in text["error"]
    ok("tools/call rejects non-canonical author with isError=true")


def test_patch_skill(client: MCPClient) -> None:
    r = client.call("tools/call", {
        "name": "skill_manage",
        "arguments": {
            "action": "patch",
            "name": "mcp-test",
            "old_string": "# MCP Test",
            "new_string": "# MCP Test (patched via MCP)",
        },
    })
    parsed = json.loads(r["result"]["content"][0]["text"])
    assert parsed["success"], parsed
    skill_md = client.skills_dir / "mcp-test" / "SKILL.md"
    assert "patched via MCP" in skill_md.read_text()
    ok("tools/call action='patch' modifies SKILL.md")


def test_delete_archives(client: MCPClient) -> None:
    r = client.call("tools/call", {
        "name": "skill_manage",
        "arguments": {
            "action": "delete",
            "name": "mcp-test",
            "absorbed_into": "umbrella",
        },
    })
    parsed = json.loads(r["result"]["content"][0]["text"])
    assert parsed["success"]
    assert parsed.get("archived")
    assert not (client.skills_dir / "mcp-test").exists()
    archive = client.skills_dir / ".archive" / "mcp-test"
    assert archive.exists()
    ok("tools/call action='delete' archives (no hard delete)")


def test_unknown_method(client: MCPClient) -> None:
    r = client.call("tools/foobar")
    assert "error" in r
    assert r["error"]["code"] == -32601
    ok("unknown method returns JSON-RPC error -32601")


def test_missing_required_arg(client: MCPClient) -> None:
    r = client.call("tools/call", {
        "name": "skill_manage",
        "arguments": {"action": "create"},
    })
    assert "error" in r
    assert r["error"]["code"] == -32602
    ok("missing required arg returns JSON-RPC error -32602")


def main() -> int:
    print(f"MCP server binary: {MCP_BIN}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "skills"
        tmp.mkdir()
        client = MCPClient(tmp)
        client.start()
        try:
            test_initialize(client)
            test_ping(client)
            test_tools_list(client)
            test_create_skill(client, tmp)
            test_create_rejects_61_char_desc(client)
            test_create_rejects_bad_author(client)
            test_patch_skill(client)
            test_delete_archives(client)
            test_unknown_method(client)
            test_missing_required_arg(client)
        finally:
            client.stop()
    print(f"\n{GREEN}=== ALL MCP TESTS PASSED ==={RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())