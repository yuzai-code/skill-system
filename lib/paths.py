"""Path resolution per CLI. Detects active CLI from env or explicit arg."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

CLI = Literal["claude-code", "opencode", "codex"]


def _home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def skills_dir_for(cli: CLI) -> Path:
    """Per-CLI skill storage. Each CLI keeps its own skills/.

    SKILLS_DIR env var overrides per-CLI default (for testing + monorepo use).
    """
    override = os.environ.get("SKILLS_DIR")
    if override:
        return Path(override)
    if cli == "claude-code":
        return _home() / ".claude" / "skills"
    if cli == "opencode":
        return _home() / ".config" / "opencode" / "skills"
    if cli == "codex":
        return _home() / ".codex" / "skills"
    raise ValueError(f"Unknown CLI: {cli}")


def config_dir_for(cli: CLI) -> Path:
    """Where CLI-specific config files (CLAUDE.md / instructions.md / AGENTS.md) live."""
    if cli == "claude-code":
        return _home() / ".claude"
    if cli == "opencode":
        return _home() / ".config" / "opencode"
    if cli == "codex":
        return _home() / ".codex"
    raise ValueError(f"Unknown CLI: {cli}")


def detect_active_cli() -> CLI:
    """Detect the currently running CLI from env vars or context.

    Priority:
      1. SKILL_SYSTEM_CLI env var (explicit override)
      2. CLAUDE_CODE / CODEX env hints
      3. Default to 'opencode' (works in any context)
    """
    explicit = os.environ.get("SKILL_SYSTEM_CLI")
    if explicit in ("claude-code", "opencode", "codex"):
        return explicit  # type: ignore[return-value]
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT") or os.environ.get("CLAUDE_PROJECT_DIR"):
        return "claude-code"
    if os.environ.get("CODEX_HOME") or os.environ.get("CODEX_RUNTIME"):
        return "codex"
    return "opencode"


def system_root() -> Path:
    """Shared system root: ~/.skill-system/"""
    return _home() / ".skill-system"
