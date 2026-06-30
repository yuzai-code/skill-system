# Changelog

All notable changes to hermes-skill-system.

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