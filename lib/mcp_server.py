"""MCP server — exposes skill_manage as a native tool via Model Context Protocol.

JSON-RPC 2.0 over stdio (newline-delimited JSON). No external deps — implements
the MCP wire protocol directly so the package has zero PyPI requirements.

Wire format (NDJSON):
  request:  {"jsonrpc": "2.0", "id": N, "method": "tools/call", "params": {...}}\\n
  response: {"jsonrpc": "2.0", "id": N, "result": {...}}\\n

Supported methods:
  initialize     — handshake, returns server info + capabilities
  ping           — health check
  tools/list     — returns the skill_manage tool definition
  tools/call     — invokes skill_manage with given arguments
  notifications/initialized — no-op acknowledgement
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Import lazily so --help / startup is fast
def _import_skill_manage():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from lib import skill_manage
    return skill_manage


SERVER_INFO = {
    "name": "skill-system",
    "version": "1.0.0",
}

SERVER_CAPABILITIES = {
    "tools": {},
}

TOOL_DEFINITION: Dict[str, Any] = {
    "name": "skill_manage",
    "description": (
        "Manage skills (the agent's procedural memory — reusable approaches for "
        "recurring task types).\n\n"
        "Actions:\n"
        "  create        — Create new skill with SKILL.md\n"
        "  edit          — Full SKILL.md rewrite (major overhauls only)\n"
        "  patch         — Fuzzy find-and-replace (preferred for targeted fixes)\n"
        "  delete        — Archive to .archive/ (recoverable; absorbed_into declares intent)\n"
        "  write_file    — Add supporting file (references/, templates/, scripts/, assets/)\n"
        "  remove_file   — Remove supporting file\n\n"
        "HARD constraints when creating or editing SKILL.md:\n"
        "  - description MUST be ≤60 characters. The system-prompt skill index "
        "truncates at 60 and loads every session — anything past char 60 is "
        "silently cut and never routes. COUNT chars before saving.\n"
        "  - author MUST equal literal 'skill-system'. Never use OS "
        "username or git config — skills get shared/published; environment "
        "identity is a privacy leak.\n"
        "  - body MUST have all 8 sections: When to Use / Prerequisites / "
        "How to Run / Quick Reference / Procedure / Pitfalls / Verification\n"
        "  - Frame commands as tool names: read_file (not cat), search_files "
        "(not grep), patch (not sed), web_extract (not curl), write_file "
        "(not echo>), terminal (not bash).\n\n"
        "After complex tasks (≥5 tool calls, user corrections, or non-obvious "
        "workflows discovered), OFFER to save as a skill. The user runs /learn "
        "or confirms, then you call skill_manage(action='create') with the "
        "full SKILL.md content you generate from the conversation.\n\n"
        "Delete ARCHIVES to .archive/ (recoverable), never hard-deletes. "
        "Pass absorbed_into='<umbrella>' for consolidations or "
        "absorbed_into='' for explicit prune."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "edit", "patch", "delete", "write_file", "remove_file"],
                "description": "Which skill_manage operation to perform.",
            },
            "name": {
                "type": "string",
                "description": "kebab-case skill name. ^[a-z0-9][a-z0-9._-]*$, ≤64 chars.",
                "pattern": "^[a-z0-9][a-z0-9._-]*$",
                "maxLength": 64,
            },
            "content": {
                "type": "string",
                "description": (
                    "Full SKILL.md content for create/edit. MUST include YAML "
                    "frontmatter (--- name / description / version / author ---) "
                    "and an 8-section body. Description ≤60 chars; "
                    "author = 'skill-system'."
                ),
            },
            "category": {
                "type": "string",
                "description": "Optional category directory (single segment).",
            },
            "file_path": {
                "type": "string",
                "description": (
                    "Path under the skill directory for write_file/remove_file/patch. "
                    "Must start with one of: references/, templates/, scripts/, assets/. "
                    "No '..' traversal."
                ),
            },
            "file_content": {
                "type": "string",
                "description": "Content for write_file.",
            },
            "old_string": {
                "type": "string",
                "description": "Text to find for patch. Required when action='patch'.",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement text for patch. Empty string deletes matched text.",
            },
            "replace_all": {
                "type": "boolean",
                "default": False,
                "description": "Patch all occurrences instead of just the first.",
            },
            "absorbed_into": {
                "type": "string",
                "description": (
                    "For action='delete': '<umbrella-name>' if this skill's content "
                    "was merged into another, '' for explicit prune. Required when "
                    "deleting during the background curator pass."
                ),
            },
        },
        "required": ["action", "name"],
    },
}


def _ok(id_: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _err(id_: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id_, "error": err}


def handle_request(req: Dict[str, Any], skill_manage_module: Any) -> Optional[Dict[str, Any]]:
    """Route one JSON-RPC request. Returns None for notifications (no reply)."""
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        return _ok(req_id, {
            "protocolVersion": "2024-11-05",
            "serverInfo": SERVER_INFO,
            "capabilities": SERVER_CAPABILITIES,
        })

    if method == "ping":
        return _ok(req_id, {})

    if method == "tools/list":
        return _ok(req_id, {"tools": [TOOL_DEFINITION]})

    if method == "tools/call":
        return _handle_tool_call(req_id, params, skill_manage_module)

    if method == "notifications/initialized":
        return None

    if method.startswith("notifications/"):
        return None

    return _err(req_id, -32601, f"Method not found: {method}")


def _handle_tool_call(
    req_id: Any, params: Dict[str, Any], skill_manage_module: Any
) -> Dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if name != "skill_manage":
        return _err(req_id, -32602, f"Unknown tool: {name}")

    if not isinstance(arguments, dict):
        return _err(req_id, -32602, "arguments must be a JSON object")

    action = arguments.get("action")
    if not action:
        return _err(req_id, -32602, "Missing required argument: action")
    skill_name = arguments.get("name")
    if not skill_name:
        return _err(req_id, -32602, "Missing required argument: name")

    try:
        if action == "restore":
            result = skill_manage_module.action_restore(skill_name)
        else:
            result = skill_manage_module.skill_manage(
                action=action,
                name=skill_name,
                content=arguments.get("content"),
                category=arguments.get("category"),
                file_path=arguments.get("file_path"),
                file_content=arguments.get("file_content"),
                old_string=arguments.get("old_string"),
                new_string=arguments.get("new_string"),
                replace_all=bool(arguments.get("replace_all", False)),
                absorbed_into=arguments.get("absorbed_into"),
            )
    except Exception as e:
        logging.exception("skill_manage call failed")
        return _ok(req_id, {
            "content": [{"type": "text", "text": f"Internal error: {e}"}],
            "isError": True,
        })

    is_error = not result.get("success", False)
    # On a successful create, advance the offer-gate cooldown (WAITING->COOLDOWN)
    # so we don't re-offer in the same session after the user already saved.
    if not is_error and action == "create":
        try:
            from lib import offer_gate, paths
            cli = paths.detect_active_cli()
            offer_gate.record_create(cli)
        except Exception:  # best-effort; never break the tool response
            pass
    text = json.dumps(result, ensure_ascii=False, indent=2)
    return _ok(req_id, {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    })


def serve_stdio(skills_dir: Optional[Path] = None) -> int:
    """Run the MCP server over stdin/stdout."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [mcp] %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    skill_manage_module = _import_skill_manage()
    if skills_dir is not None:
        skill_manage_module.configure(skills_dir)

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            err_resp = _err(None, -32700, f"Parse error: {e}")
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()
            continue
        try:
            resp = handle_request(req, skill_manage_module)
        except Exception as e:
            logging.exception("request handler crashed")
            resp = _err(req.get("id"), -32603, f"Internal error: {e}")
        if resp is None:
            continue
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()
    return 0


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="MCP server for skill_manage")
    parser.add_argument(
        "--skills-dir",
        help="Override skills directory (default: per-CLI auto-detect)",
    )
    args = parser.parse_args()
    skills_dir = Path(args.skills_dir) if args.skills_dir else None
    return serve_stdio(skills_dir)


if __name__ == "__main__":
    sys.exit(main())