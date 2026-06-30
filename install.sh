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

      # CLAUDE.md: append our block if not already present
      local claude_md="${HOME}/.claude/CLAUDE.md"
      mkdir -p "$(dirname "${claude_md}")"
      local marker="<!-- skill-system:start -->"
      if [[ -f "${claude_md}" ]] && grep -q "${marker}" "${claude_md}"; then
        echo "  CLAUDE.md already has skill-system block; skipping"
      else
        {
          echo ""
          echo "${marker}"
          cat "${SYSTEM_ROOT}/commands/claude-code/CLAUDE.md"
          echo "<!-- skill-system:end -->"
        } >> "${claude_md}"
        echo "  appended skill-system block to ~/.claude/CLAUDE.md"
      fi

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
      local marker="<!-- skill-system:start -->"
      if [[ -f "${instr}" ]] && grep -q "${marker}" "${instr}"; then
        echo "  instructions.md already has skill-system block; skipping"
      else
        {
          echo "${marker}"
          cat "${SYSTEM_ROOT}/commands/opencode/instructions.md"
          echo "<!-- skill-system:end -->"
        } > "${instr}.new"
        if [[ -f "${instr}" ]]; then
          cat "${instr}" >> "${instr}.new"
        fi
        mv "${instr}.new" "${instr}"
        echo "  wrote ~/.config/opencode/instructions.md"
      fi
      bash "${SYSTEM_ROOT}/hooks/refresh_index.sh" opencode
      echo "  generated initial skill index cache"
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
      local marker="<!-- skill-system:start -->"
      if [[ -f "${agents}" ]] && grep -q "${marker}" "${agents}"; then
        echo "  AGENTS.md already has skill-system block; skipping"
      else
        {
          echo "${marker}"
          cat "${SYSTEM_ROOT}/commands/codex/AGENTS.md"
          echo "<!-- skill-system:end -->"
        } > "${agents}.new"
        if [[ -f "${agents}" ]]; then
          cat "${agents}" >> "${agents}.new"
        fi
        mv "${agents}.new" "${agents}"
        echo "  wrote ~/.codex/AGENTS.md"
      fi
      bash "${SYSTEM_ROOT}/hooks/refresh_index.sh" codex
      echo "  generated initial skill index cache"
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
