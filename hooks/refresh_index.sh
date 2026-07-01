#!/usr/bin/env bash
# Refresh skill index for any CLI.
# Usage: refresh_index.sh <cli>
set -euo pipefail

CLI="${1:-opencode}"
SYSTEM_ROOT="${HOME}/.skill-system"
HOME_DIR="${HOME}"

case "${CLI}" in
  claude-code) SKILLS_DIR="${HOME_DIR}/.claude/skills" ;;
  opencode)    SKILLS_DIR="${HOME_DIR}/.config/opencode/skills" ;;
  codex)       SKILLS_DIR="${HOME_DIR}/.codex/skills" ;;
  codefuse)    SKILLS_DIR="${HOME_DIR}/.codefuse/fuse/skills" ;;
  *) echo "unknown CLI: ${CLI}" >&2; exit 1 ;;
esac

mkdir -p "${SKILLS_DIR}"
python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, '${SYSTEM_ROOT}')
from lib import skill_index
print(skill_index.render_for_prompt(Path('${SKILLS_DIR}')), end='')
" > "${SKILLS_DIR}/.index.cache" 2>/dev/null || true

exit 0
