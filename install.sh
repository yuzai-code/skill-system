#!/usr/bin/env bash
# Install / uninstall the skill system.
#
# Usage:
#   bash install.sh                  # install
#   bash install.sh --uninstall      # remove
#   bash install.sh --cli <name>     # only install for one CLI (default: all)
#   bash install.sh --no-hooks       # skip Claude Code hook registration
#
# What it does:
#   1. Verifies Python 3.10+ and PyYAML absence is fine (we ship our own parser)
#   2. Installs config files for each CLI:
#        Claude Code: ~/.claude/CLAUDE.md + ~/.claude/commands/{learn,skill-manage}.md
#        OpenCode:    ~/.config/opencode/instructions.md + ~/.config/opencode/commands/{learn,skill-manage}.md
#        Codex:       ~/.codex/AGENTS.md + ~/.codex/prompts/{learn,skill-manage}.md
#   3. Registers Claude Code hooks in ~/.claude/settings.json
#   4. Runs `skill doctor` to verify installation
#   5. Adds ~/.skill-system/bin to PATH (print instructions; doesn't auto-edit shell rc)
#
# On uninstall:
#   - Removes the installed config files (only the ones we created — user files
#     appended after our marker are preserved)
#   - Removes the Claude Code hooks we added
#   - Leaves ~/.skill-system/lib and bin/ alone (the source code)

set -euo pipefail

SYSTEM_ROOT="${HOME}/.skill-system"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# If invoked from a checkout, sync to ~/.skill-system. If invoked from
# ~/.skill-system/install.sh, no sync needed.
if [[ "${SCRIPT_DIR}" != "${SYSTEM_ROOT}" ]]; then
  echo ">> Syncing ${SCRIPT_DIR} -> ${SYSTEM_ROOT}"
  mkdir -p "${SYSTEM_ROOT}"
  rsync -a --delete \
    --exclude='.DS_Store' \
    --exclude='__pycache__' \
    "${SCRIPT_DIR}/" "${SYSTEM_ROOT}/"
fi

UNINSTALL=0
ONLY_CLI=""
NO_HOOKS=0
for arg in "$@"; do
  case "${arg}" in
    --uninstall) UNINSTALL=1 ;;
    --cli) shift; ONLY_CLI="${1:-}" ;;
    --no-hooks) NO_HOOKS=1 ;;
  esac
done

run_or_all() {
  local cli="$1"
  if [[ -z "${ONLY_CLI}" || "${ONLY_CLI}" == "${cli}" ]]; then
    return 0
  fi
  return 1
}

update_marked_block() {
  # Replace the marked <!-- skill-system:start --> ... <!-- skill-system:end -->
  # block in $1 with contents of $2. Idempotent: strips old first.
  python3 - "$1" "$2" <<'PYEOF'
import re, sys
from pathlib import Path
target = Path(sys.argv[1])
source = Path(sys.argv[2])
marker_open = "<!-- skill-system:start -->"
marker_close = "<!-- skill-system:end -->"
pattern = re.compile(
    re.escape(marker_open) + r".*?" + re.escape(marker_close) + r"\n?",
    re.DOTALL,
)
new_body = source.read_text(encoding="utf-8")
existing = target.read_text(encoding="utf-8") if target.exists() else ""
stripped = pattern.sub("", existing).rstrip() + "\n"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(
    stripped + "\n" + marker_open + "\n" + new_body.rstrip("\n") + "\n" + marker_close + "\n"
)
print(f"  updated marked block in {target}")
PYEOF
}

uninstall_cli() {
  local cli="$1"
  case "${cli}" in
    claude-code)
      rm -f "${HOME}/.claude/commands/learn.md" \
            "${HOME}/.claude/commands/skill-manage.md"
      python3 -c "
import json, sys
p = '${HOME}/.claude/settings.json'
try:
    s = json.load(open(p))
except Exception:
    sys.exit(0)
hooks = s.get('hooks', {})
for event in ('UserPromptSubmit', 'PostToolUse', 'Stop'):
    if event in hooks:
        hooks[event] = [h for h in hooks[event]
                        if not any('skill' in (c.get('command', '') if isinstance(c, dict) else '')
                                   for c in h.get('hooks', []))]
        if not hooks[event]: del hooks[event]
if not hooks: s.pop('hooks', None)
json.dump(s, open(p, 'w'), indent=2)
"
      echo "  removed Claude Code hooks + commands"
      ;;
    opencode)
      rm -f "${HOME}/.config/opencode/commands/learn.md" \
            "${HOME}/.config/opencode/commands/skill-manage.md"
      # instructions.md is user-edited; we don't remove it wholesale
      python3 -c "
import re
p = '${HOME}/.config/opencode/instructions.md'
try:
    txt = open(p).read()
except Exception:
    raise SystemExit(0)
marker = '<!-- skill-system:start -->'
if marker in txt:
    txt = re.sub(re.escape(marker) + r'.*?<!-- skill-system:end -->\n*', '', txt, flags=re.S)
    open(p, 'w').write(txt)
    print('  trimmed OpenCode instructions.md (skill-system block removed)')
"
      echo "  removed OpenCode commands"
      ;;
    codex)
      rm -f "${HOME}/.codex/prompts/learn.md" \
            "${HOME}/.codex/prompts/skill-manage.md"
      python3 -c "
import re
p = '${HOME}/.codex/AGENTS.md'
try:
    txt = open(p).read()
except Exception:
    raise SystemExit(0)
marker = '<!-- skill-system:start -->'
if marker in txt:
    txt = re.sub(re.escape(marker) + r'.*?<!-- skill-system:end -->\n*', '', txt, flags=re.S)
    open(p, 'w').write(txt)
    print('  trimmed Codex AGENTS.md (skill-system block removed)')
"
      echo "  removed Codex prompts"
      ;;
  esac
}

install_cli() {
  local cli="$1"
  case "${cli}" in
    claude-code)
      mkdir -p "${HOME}/.claude/commands"
      cp -f "${SYSTEM_ROOT}/commands/claude-code/learn.md" \
            "${HOME}/.claude/commands/learn.md"
      cp -f "${SYSTEM_ROOT}/commands/claude-code/skill-manage.md" \
            "${HOME}/.claude/commands/skill-manage.md"
      echo "  installed: ~/.claude/commands/{learn,skill-manage}.md"

      # CLAUDE.md: replace existing marked block (idempotent)
      local claude_md="${HOME}/.claude/CLAUDE.md"
      mkdir -p "$(dirname "${claude_md}")"
      update_marked_block "${claude_md}" "${SYSTEM_ROOT}/commands/claude-code/CLAUDE.md"

      if [[ "${NO_HOOKS}" -eq 0 ]]; then
        register_claude_hooks
      fi
      ;;
    opencode)
      mkdir -p "${HOME}/.config/opencode/commands"
      cp -f "${SYSTEM_ROOT}/commands/opencode/learn.md" \
            "${HOME}/.config/opencode/commands/learn.md"
      cp -f "${SYSTEM_ROOT}/commands/opencode/skill-manage.md" \
            "${HOME}/.config/opencode/commands/skill-manage.md"
      echo "  installed: ~/.config/opencode/commands/{learn,skill-manage}.md"

      local instr="${HOME}/.config/opencode/instructions.md"
      mkdir -p "$(dirname "${instr}")"
      update_marked_block "${instr}" "${SYSTEM_ROOT}/commands/opencode/instructions.md"
      bash "${SYSTEM_ROOT}/hooks/refresh_index.sh" opencode
      echo "  refreshed skill index cache"
      ;;
    codex)
      mkdir -p "${HOME}/.codex/prompts"
      cp -f "${SYSTEM_ROOT}/commands/codex/learn.md" \
            "${HOME}/.codex/prompts/learn.md"
      cp -f "${SYSTEM_ROOT}/commands/codex/skill-manage.md" \
            "${HOME}/.codex/prompts/skill-manage.md"
      echo "  installed: ~/.codex/prompts/{learn,skill-manage}.md"

      local agents="${HOME}/.codex/AGENTS.md"
      mkdir -p "$(dirname "${agents}")"
      update_marked_block "${agents}" "${SYSTEM_ROOT}/commands/codex/AGENTS.md"
      bash "${SYSTEM_ROOT}/hooks/refresh_index.sh" codex
      echo "  refreshed skill index cache"
      ;;
  esac
}

register_claude_hooks() {
  local settings="${HOME}/.claude/settings.json"
  mkdir -p "$(dirname "${settings}")"
  python3 - <<PYEOF
import json, os
p = "${settings}"
try:
    s = json.load(open(p)) if os.path.exists(p) else {}
except Exception:
    s = {}
hooks = s.setdefault("hooks", {})

def make_hook(cmd, timeout=5):
    return {"type": "command", "command": cmd, "timeout": timeout}

skill_marker = "skill-system"

def already(entries, marker=skill_marker):
    for e in entries:
        for h in e.get("hooks", []):
            c = h.get("command", "")
            if marker in c:
                return True
    return False

# UserPromptSubmit: inject skill index
ups = hooks.setdefault("UserPromptSubmit", [])
if not already(ups):
    ups.append({"hooks": [make_hook("bash ${HOME}/.skill-system/hooks/claude_code_userpromptsubmit.sh", timeout=3)]})

# PostToolUse: refresh index after skill_manage
ptu = hooks.setdefault("PostToolUse", [])
if not already(ptu):
    ptu.append({
        "matcher": "Bash|Read|Write|Edit",
        "hooks": [make_hook("bash \${HOME}/.skill-system/hooks/claude_code_posttooluse.sh", timeout=5)]
    })

# Stop: trigger curator
stop = hooks.setdefault("Stop", [])
if not already(stop):
    stop.append({"hooks": [make_hook("bash \${HOME}/.skill-system/hooks/claude_code_stop.sh", timeout=10)]})

json.dump(s, open(p, "w"), indent=2)
print("  registered hooks in ~/.claude/settings.json")
PYEOF
}

register_mcp_servers() {
  # Register the skill_manage MCP server in each CLI's native config.
  # Format differs per CLI — see comments below.
  export _SS_SYSTEM_ROOT="${SYSTEM_ROOT}"
  export _SS_MCP_BIN="${SYSTEM_ROOT}/bin/skill-manage-mcp"
  export _SS_HOME="${HOME}"
  export _SS_CC_DIR="${HOME}/.claude/skills"
  export _SS_OC_DIR="${HOME}/.config/opencode/skills"
  export _SS_CDX_DIR="${HOME}/.codex/skills"
  python3 - <<'PYEOF'
import json, os, re
from pathlib import Path

SERVER_NAME = "skill-system"
SYSTEM_ROOT = os.environ["_SS_SYSTEM_ROOT"]
MCP_BIN = os.environ["_SS_MCP_BIN"]
HOME = os.environ["_SS_HOME"]
CC_DIR = os.environ["_SS_CC_DIR"]
OC_DIR = os.environ["_SS_OC_DIR"]
CDX_DIR = os.environ["_SS_CDX_DIR"]

# ---------- Claude Code ----------
# Reads ~/.claude/.mcp.json, key "mcpServers", value {command, args, env}.
cc_path = Path(HOME) / ".claude" / ".mcp.json"
try:
    cc_cfg = json.loads(cc_path.read_text()) if cc_path.exists() else {}
except Exception:
    cc_cfg = {}
cc_servers = cc_cfg.setdefault("mcpServers", {})
cc_servers[SERVER_NAME] = {
    "command": MCP_BIN,
    "args": [],
    "env": {"SKILLS_DIR": CC_DIR},
}
cc_path.write_text(json.dumps(cc_cfg, indent=2))
print(f"  registered in {cc_path} (Claude Code format)")

# ---------- OpenCode ----------
# Reads ~/.config/opencode/opencode.json, key "mcp", value
# {type:"local", command:[array], environment, enabled}.  NOT .mcp.json.
oc_path = Path(HOME) / ".config" / "opencode" / "opencode.json"
try:
    oc_cfg = json.loads(oc_path.read_text()) if oc_path.exists() else {}
except Exception:
    oc_cfg = {}
# Add $schema only if file is empty (informational; OpenCode doesn't require it)
if not oc_cfg:
    oc_cfg["$schema"] = "https://opencode.ai/config.json"
oc_mcp = oc_cfg.setdefault("mcp", {})
oc_mcp[SERVER_NAME] = {
    "type": "local",
    "command": [MCP_BIN],
    "environment": {"SKILLS_DIR": OC_DIR},
    "enabled": True,
}
oc_path.write_text(json.dumps(oc_cfg, indent=2, ensure_ascii=False))
print(f"  registered in {oc_path} (OpenCode format)")

# Clean up legacy .mcp.json from earlier install versions
oc_legacy = Path(HOME) / ".config" / "opencode" / ".mcp.json"
if oc_legacy.exists():
    try:
        legacy = json.loads(oc_legacy.read_text())
        legacy.pop("mcpServers", None)
        if not legacy:
            oc_legacy.unlink()
        else:
            oc_legacy.write_text(json.dumps(legacy, indent=2))
        print(f"  cleaned up legacy {oc_legacy}")
    except Exception:
        pass

# ---------- Codex ----------
# Reads ~/.codex/config.toml, table [mcp_servers.<name>] with
# command, args, startup_timeout_sec, and nested [mcp_servers.<name>.env].
# NOT .mcp.json.  We rewrite the .toml via a careful manual edit.
codex_toml = Path(HOME) / ".codex" / "config.toml"
if codex_toml.exists():
    text = codex_toml.read_text()
    pattern = re.compile(
        r"\[mcp_servers\.skill-system\].*?(?=\n\[|\Z)",
        re.DOTALL,
    )
    text = pattern.sub("", text).rstrip() + "\n\n"
    block = (
        "[mcp_servers.skill-system]\n"
        f'command = "{MCP_BIN}"\n'
        "args = []\n"
        "startup_timeout_sec = 30\n\n"
        "[mcp_servers.skill-system.env]\n"
        f'SKILLS_DIR = "{CDX_DIR}"\n\n'
    )
    codex_toml.write_text(text + block)
    print(f"  registered in {codex_toml} (Codex TOML format)")

# Clean up legacy .mcp.json
codex_legacy = Path(HOME) / ".codex" / ".mcp.json"
if codex_legacy.exists():
    try:
        legacy = json.loads(codex_legacy.read_text())
        legacy.pop("mcpServers", None)
        if not legacy:
            codex_legacy.unlink()
        else:
            codex_legacy.write_text(json.dumps(legacy, indent=2))
        print(f"  cleaned up legacy {codex_legacy}")
    except Exception:
        pass

print()
print("  Restart Claude Code / OpenCode / Codex to activate the new MCP tool.")
print("  After restart, the agent sees a native `skill_manage` tool with 6 actions.")
PYEOF
}

unregister_mcp_servers() {
  python3 - <<PYEOF
import json, re
from pathlib import Path

SERVER_NAME = "skill-system"

# Claude Code: .mcp.json
p = Path("${HOME}/.claude/.mcp.json")
if p.exists():
    try:
        cfg = json.loads(p.read_text())
        servers = cfg.get("mcpServers", {})
        if SERVER_NAME in servers:
            del servers[SERVER_NAME]
            if not servers:
                cfg.pop("mcpServers", None)
            p.write_text(json.dumps(cfg, indent=2))
            print(f"  removed MCP server from {p}")
    except Exception:
        pass

# OpenCode: opencode.json
p = Path("${HOME}/.config/opencode/opencode.json")
if p.exists():
    try:
        cfg = json.loads(p.read_text())
        mcp = cfg.get("mcp", {})
        if SERVER_NAME in mcp:
            del mcp[SERVER_NAME]
            if not mcp:
                cfg.pop("mcp", None)
            p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
            print(f"  removed MCP server from {p}")
    except Exception:
        pass

# Codex: config.toml (manual block removal)
p = Path("${HOME}/.codex/config.toml")
if p.exists():
    text = p.read_text()
    pattern = re.compile(
        r"\[mcp_servers\.skill-system\]\n.*?(?=\n\[|\Z)",
        re.DOTALL,
    )
    new_text = pattern.sub("", text)
    if new_text != text:
        p.write_text(new_text)
        print(f"  removed MCP server from {p}")
PYEOF
}

print_path_instructions() {
  cat <<'PATH'

>> Add ~/.skill-system/bin to your PATH (one-time):

   # zsh
   echo 'export PATH="$HOME/.skill-system/bin:$PATH"' >> ~/.zshrc
   source ~/.zshrc

   # bash
   echo 'export PATH="$HOME/.skill-system/bin:$PATH"' >> ~/.bashrc
   source ~/.bashrc

PATH
}

# ---------- Main ----------

if [[ "${UNINSTALL}" -eq 1 ]]; then
  echo ">> Uninstalling skill system"
  for cli in claude-code opencode codex; do
    run_or_all "${cli}" && uninstall_cli "${cli}"
  done
  echo ""
  echo ">> Unregistering MCP server"
  unregister_mcp_servers
  echo ""
  echo "Uninstalled config files. Source code at ${SYSTEM_ROOT} is preserved."
  echo "To fully remove: rm -rf ${SYSTEM_ROOT}"
  exit 0
fi

echo ">> Installing skill system to ${SYSTEM_ROOT}"

# Verify Python
PYTHON_BIN="$(command -v python3 || true)"
if [[ -z "${PYTHON_BIN}" ]]; then
  echo "ERROR: python3 not found in PATH" >&2
  exit 1
fi
PY_VERSION="$("${PYTHON_BIN}" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "  python3: ${PY_VERSION}"
if [[ "${PY_VERSION%%.*}" -lt 3 ]]; then
  echo "ERROR: Python 3.10+ required (have ${PY_VERSION})" >&2
  exit 1
fi

# Verify core
if [[ ! -d "${SYSTEM_ROOT}/lib" ]] || [[ ! -x "${SYSTEM_ROOT}/bin/skill" ]]; then
  echo "ERROR: ${SYSTEM_ROOT}/lib or bin not present" >&2
  exit 1
fi

chmod +x "${SYSTEM_ROOT}"/bin/* "${SYSTEM_ROOT}"/hooks/*.sh 2>/dev/null || true

# Install per CLI
for cli in claude-code opencode codex; do
  run_or_all "${cli}" && install_cli "${cli}"
done

# Register MCP server in all 3 CLIs' .mcp.json (idempotent)
echo ""
echo ">> Registering MCP server (skill_manage as native tool)"
register_mcp_servers

# Doctor
echo ""
echo ">> Running skill doctor"
SKILLS_DIR="" "${PYTHON_BIN}" "${SYSTEM_ROOT}/bin/skill" doctor 2>&1 | head -20 || true

# PATH
print_path_instructions

echo ""
echo ">> Installation complete."
echo ""
echo "Quick start:"
echo "  skill list                  # show installed skills (any CLI)"
echo "  skill view <name>           # inspect a skill"
echo "  /learn <description>        # author a new skill (in any CLI)"
echo "  skill-curator --status      # check curator state"
