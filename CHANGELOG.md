# Changelog

All notable changes to hermes-skill-system.

## [1.2.0] - 2026-06-30

### Changed

- **System prompt精简 (180 → 52 lines, -71%)**:
  - Based on Hermes Agent source (`agent/system_prompt.py` SKILLS_GUIDANCE):
    only 4 lines of skill guidance in the baseline prompt
  - HARD constraints moved out of system prompt (already in tool description)
  - 8-section body requirements moved to `/learn` (loaded on demand)
  - Tool framing table moved to `/learn`
  - Each CLI's marked block now ~16-18 lines instead of 47-59
- **`update_marked_block()` helper** in `install.sh`:
  - Replaces existing marked blocks instead of skipping on rerun
  - Idempotent: strips old block, appends new block, preserves user content
  - Fixed bug where reinstall did not update previously-installed blocks
- **Fixed duplicate `install_cli` function**:
  - Broken leftover from prior inserts had `install_cli` containing uninstall
    logic
  - Renamed to `uninstall_cli` to match its actual behavior

### Why this matters

Before: every Claude Code / OpenCode / Codex session paid ~180 lines of
permanent context overhead describing the skill system in detail.

After: each session pays ~52 lines total. HARD constraints live in the
tool description (which the agent sees only when calling the tool),
detailed authoring standards live in `/learn` (loaded only when the user
invokes /learn), and the skill index is dynamically generated per-session.

The agent still gets clear guidance ("save after complex tasks, patch
when stale") in 4-5 lines, not 50.

## [1.1.0] - 2026-06-30

### Added

- **MCP server** (`lib/mcp_server.py` + `bin/skill-manage-mcp`):
  - Implements Model Context Protocol over stdio (JSON-RPC 2.0, NDJSON)
  - Exposes `skill_manage` as a **native tool** (not a shell script wrapper)
  - Handles `initialize`, `ping`, `tools/list`, `tools/call`, `notifications/initialized`
  - Tool schema documents HARD constraints in description (60-char limit,
    author requirement, 8-section body)
  - Zero external deps (no `mcp` PyPI package — implements wire protocol directly)
- **MCP server tests** (`tests/test_mcp.py`): 10 assertions covering initialize
  handshake, tools/list schema, tools/call for all 6 actions, HARD-constraint
  rejection, JSON-RPC error codes (-32601, -32602)
- **MCP auto-registration** (`install.sh`):
  - `register_mcp_servers()` writes to `~/.claude/.mcp.json`,
    `~/.config/opencode/.mcp.json`, `~/.codex/.mcp.json`
  - Idempotent: re-running is safe; preserves user-added MCP servers
  - `unregister_mcp_servers()` runs on `--uninstall`
- **System prompt updates** (`commands/{claude-code,opencode,codex}/*`):
  - Agent now told `skill_manage` is a native MCP tool — call it directly,
    don't invoke via Bash

### Changed

- `commands/claude-code/CLAUDE.md`, `commands/opencode/instructions.md`,
  `commands/codex/AGENTS.md`: now describe `skill_manage` as a native MCP tool
  with explicit "do NOT invoke via Bash" rule
- `install.sh` quick-start mentions restarting CLIs to activate the MCP tool
- `.github/workflows/test.yml`: now runs both `test_smoke.py` AND `test_mcp.py`

### Why this matters

Before v1.1.0, `skill_manage` was a shell script the agent had to invoke via the Bash
tool — fragile (string args, no type safety, error handling was string
parsing). After v1.1.0, it's a proper tool: structured JSON Schema,
typed arguments, `isError` flag in the response, agent sees it in the
native tool list.

## [1.0.0] - 2026-06-30

### Added

- Core library (`lib/`):
  - `paths.py` — per-CLI path resolution + active CLI detection
  - `yaml_mini.py` — self-contained frontmatter parser (no PyYAML dep)
  - `schema.py` — SKILL.md validation with HARD 60-char description + author constraints
  - `atomic_io.py` — crash-safe writes (tempfile + fsync + os.replace)
  - `fuzzy_match.py` — 3-strategy whitespace-tolerant find-and-replace (exact / line-trim / whitespace-normalized)
  - `skill_preprocess.py` — `${HERMES_SKILL_DIR}` / `${HERMES_SESSION_ID}` / `!`shell-cmd`` (opt-in)
  - `skill_usage.py` — `.usage.json` sidecar with fcntl file lock
  - `skill_manage.py` — 6 actions (create/edit/patch/delete/write_file/remove_file) + 6-layer validation
  - `skill_index.py` — prompt-injectable skill index with 60-char description truncation
  - `skill_curator.py` — inactivity-triggered state machine (30d→stale, 90d→archived)
- CLI entry points (`bin/`):
  - `skill` — list/view/init/sync/doctor/index
  - `skill-manage` — JSON-RPC backend for the `skill_manage` tool
  - `skill-curator` — curator runner with `--maybe-run` / `--run-once` / `--consolidate` / `--status` / `--pause` / `--resume`
- Per-CLI integration (`commands/`):
  - Claude Code: `CLAUDE.md` block + `commands/{learn,skill-manage}.md`
  - OpenCode: `instructions.md` + `commands/{learn,skill-manage}.md`
  - Codex: `AGENTS.md` + `prompts/{learn,skill-manage}.md`
- Hook scripts (`hooks/`):
  - Claude Code `UserPromptSubmit` (inject index)
  - Claude Code `PostToolUse` (refresh index)
  - Claude Code `Stop` (curator --maybe-run)
  - `refresh_index.sh` for OpenCode/Codex manual refresh
- Installer (`install.sh`):
  - Per-CLI install (`--cli <name>`)
  - Uninstall (`--uninstall`)
  - Skip hooks (`--no-hooks`)
  - Idempotent (re-running is safe; preserves user content via marked blocks)
- Tests (`tests/test_smoke.py`): 31 assertions covering all modules + end-to-end CLI
- Documentation (`README.md`): architecture, install, usage, HARD constraints

### Design principles (from Hermes Agent analysis)

- `/learn` slash command is the primary path for skill creation
- Agent offer after complex tasks is the secondary path
- `description ≤ 60 chars` is a hard system constraint (longer never routes)
- `author = "hermes-skill-system"` literal (privacy: never environment identity)
- 8-section body structure enforced
- Archive-not-delete (every delete is reversible)
- Curator runs inactivity-triggered, not on cron
- LLM consolidation pass is off by default (cost)