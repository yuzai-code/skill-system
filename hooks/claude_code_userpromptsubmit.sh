#!/usr/bin/env bash
# Claude Code UserPromptSubmit hook: inject skill index into prompt context.
# Reads the cached index and prints it as additional context.
set -euo pipefail

SKILLS_DIR="${HOME}/.claude/skills"
CACHE="${SKILLS_DIR}/.index.cache"

if [[ ! -f "${CACHE}" ]]; then
  # First run: generate cache.
  python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / '.skill-system'))
from lib import skill_index
print(skill_index.render_for_prompt(Path('${SKILLS_DIR}')), end='')
" > "${CACHE}" 2>/dev/null || true
fi

if [[ -f "${CACHE}" && -s "${CACHE}" ]]; then
  echo "<skill-index>"
  cat "${CACHE}"
  echo ""
  echo "</skill-index>"
fi

exit 0
