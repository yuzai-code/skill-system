# skill-system

Procedural memory for **Claude Code / OpenCode / Codex / CodeFuse**.

Captures successful workflows as reusable skills, with a curator that
archives unused ones and consolidates overlapping clusters. Per-CLI skill
storage, shared system code.

## What you get

- **`/learn <description>`** in any CLI — author a new skill
- **`skill_manage` as a native MCP tool** — registered in each CLI's `.mcp.json`,
  not a shell-script wrapper. Agent calls it with structured arguments:
  `skill_manage(action="create", name="arxiv-search", content="---...---")`
- **6 actions** with HARD-constraint validation: create / edit / patch / delete
  / write_file / remove_file
- **Curator** that runs inactivity-triggered, archives skills unused > 90d
- **Skill index** auto-injected into every prompt (truncated at 60-char description)
- **Archive-not-delete** — every `delete` is reversible from `~/.{cli}/skills/.archive/`

## Architecture

```
~/.skill-system/                          # shared system code (one source of truth)
├── lib/                                  # Python modules
│   ├── paths.py          # per-CLI path resolution
│   ├── yaml_mini.py      # frontmatter parser (no PyYAML needed)
│   ├── schema.py         # SKILL.md validation (HARD 60-char description)
│   ├── atomic_io.py      # crash-safe writes
│   ├── fuzzy_match.py    # whitespace-tolerant find-and-replace
│   ├── skill_preprocess.py  # ${SKILL_DIR} + !`cmd` (opt-in)
│   ├── skill_usage.py    # .usage.json sidecar with fcntl lock
│   ├── skill_manage.py   # 6 actions, 6-layer validation
│   ├── skill_index.py    # prompt-injectable index
│   ├── skill_curator.py  # inactivity-triggered state machine
│   └── mcp_server.py     # MCP server (skill_manage as native tool)
├── bin/                                  # CLI entry points
│   ├── skill              # main CLI (list/view/init/sync/doctor/index)
│   ├── skill-manage       # skill_manage shell backend (for direct CLI use)
│   ├── skill-curator      # curator runner
│   └── skill-manage-mcp   # MCP server entry (used by Claude Code etc.)
├── commands/                              # per-CLI config templates
│   ├── claude-code/CLAUDE.md + commands/{learn,skill-manage}.md
│   ├── opencode/instructions.md + commands/{learn,skill-manage}.md
│   ├── codex/AGENTS.md + prompts/{learn,skill-manage}.md
│   └── codefuse/CODEFUSE.md + commands/{learn,skill-manage}.md
├── hooks/                                # hook scripts
├── tests/
│   ├── test_smoke.py     # 31 assertions, core library end-to-end
│   └── test_mcp.py       # 10 assertions, MCP server end-to-end (subprocess)
└── install.sh                            # installer + MCP registration

~/.claude/skills/                         # per-CLI storage (each keeps its own)
~/.config/opencode/skills/                # symlink-friendly
~/.codex/skills/                          # ...
~/.codefuse/fuse/skills/                  # ...

~/.claude/.mcp.json                       # registers skill-system MCP server
~/.config/opencode/.mcp.json
~/.codex/.mcp.json
~/.codefuse/fuse/codefuse.json            # CodeFuse: hooks + mcpServers in one file
```

## Install

```bash
bash ~/.skill-system/install.sh
```

Uninstall: `bash ~/.skill-system/install.sh --uninstall`

Per-CLI: `bash ~/.skill-system/install.sh --cli opencode`

Skip hook registration: `bash ~/.skill-system/install.sh --no-hooks`

After install, add `~/.skill-system/bin` to your PATH (instructions printed
by installer).

## SKILL.md format (HARD constraints)

```yaml
---
name: arxiv-search              # kebab-case, ≤64 chars
description: Search arXiv by keyword, author, or ID.    # HARD: ≤60 chars
version: 0.1.0
author: skill-system     # literal, NEVER environment identity
platforms: [macos, linux]       # optional, OS-bound only
metadata:
    tags: [Research, Academic]
---

# <Human Title>

2-3 sentence intro: what it does, what it doesn't, key dep.

## When to Use              ← trigger phrases
## Prerequisites            ← env vars, install, credentials
## How to Run               ← canonical invocation (tool-framed)
## Quick Reference          ← flat command list
## Procedure                ← numbered steps with exact commands
## Pitfalls                 ← known limits, "looks broken but isn't"
## Verification             ← single command that proves it worked
```

**The 60-char description is a hard system constraint** — the skill index
truncates at 60, anything past is silently cut and never routes. After
writing, `len(description) <= 60` is enforced at create time.

**author = `skill-system` literally** — never OS username or git
config. Skills get shared; environment identity is a privacy leak.

**Tool framing** — say `read_file` not cat, `search_files` not grep,
`patch` not sed, `web_extract` not curl. This is what makes a skill
work across local / Docker / SSH backends.

## CLI usage

```bash
# List skills
skill list                    # active CLI
skill list --cli claude-code  # explicit CLI

# View a skill
skill view arxiv-search

# Direct skill_manage access (prefer /learn for new skills)
echo '{"action":"create","name":"x","content":"..."}' | \
  skill-manage --cli opencode

# Curator
skill-curator --status        # show state
skill-curator --run-once      # bypass gate, run transitions
skill-curator --maybe-run     # gate check, only runs if interval elapsed
skill-curator --pause         # disable auto maintenance
skill-curator --resume
```

## How it works in each CLI

| CLI | Skill storage | System prompt | Slash command | Hooks |
|---|---|---|---|---|
| Claude Code | `~/.claude/skills/` | `~/.claude/CLAUDE.md` | `~/.claude/commands/{learn,skill-manage}.md` | `~/.claude/settings.json` (UserPromptSubmit / PostToolUse / Stop) |
| OpenCode | `~/.config/opencode/skills/` | `~/.config/opencode/instructions.md` | `~/.config/opencode/commands/{learn,skill-manage}.md` | Manual refresh via `skill index`; launchd plist optional |
| Codex | `~/.codex/skills/` | `~/.codex/AGENTS.md` | `~/.codex/prompts/{learn,skill-manage}.md` | Manual refresh via `skill index` |
| CodeFuse | `~/.codefuse/fuse/skills/` | `~/.codefuse/fuse/CODEFUSE.md` | `~/.codefuse/fuse/commands/{learn,skill-manage}.md` | `~/.codefuse/fuse/codefuse.json` (UserPromptSubmit / PostToolUse / Stop) |

Each CLI's existing user content in those files is preserved — the
installer appends a marked block (`<!-- skill-system:start --> ... <!-- skill-system:end -->`)
or only overwrites its own files.

## Dynamic syntax (opt-in)

In any SKILL.md body:

```markdown
Run: ${SKILL_DIR}/scripts/setup.sh
Today's date: !`date +%Y-%m-%d`
```

- `${SKILL_DIR}` / `${SKILL_SESSION_ID}`: replaced at load
  (`SKILL_TEMPLATE_VARS=1`, default ON)
- `!`cmd``: runs at load, replaces stdout
  (`SKILL_INLINE_SHELL=1`, **default OFF for security**)

Inline shell is bounded: 10s timeout, 4000-char output cap, stdin=DEVNULL,
cwd=skill dir, fail-marker (not exception) on error.

## Curator

Inactivity-triggered, not cron:

- Default interval: 7 days (`maybe_run_curator` checks `last_run_at`)
- First run after install: deferred (seed state, no immediate pass)
- 30d inactive → `stale`
- 90d inactive → `archived` (moved to `.archive/`, recoverable)
- `use_count == 0` skills get a grace floor (don't archive < 30d)
- Pinned skills: never auto-transition

LLM consolidation pass is **off by default** (cost). Enable with
`SKILL_CONSOLIDATE=1` or run `skill-curator --consolidate` (the umbrella
prompt is implemented as a stub; would invoke the main LLM with the
umbrella-building spec).

## Tests

```bash
python3 ~/.skill-system/tests/test_smoke.py
```

Covers: yaml_mini parsing, schema validation (incl. 60-char + author
HARD constraints), fuzzy_match (3 strategies), skill_manage (all 6
actions + 6-layer validation), curator (state machine, gates, grace
floor), index (trigger extraction), preprocess (template vars + inline
shell), CLI end-to-end (subprocess).

## Privacy

Skills get shared and published. Two HARD rules prevent leaks:

1. `author` must equal `skill-system` literally. We refuse to
   accept `os.getlogin()`, `git config user.name`, or any environment-
   derived identity at create/edit time.
2. Description is truncated to 60 chars in the prompt index, so a
   malicious skill can't exfiltrate via a long description.

Per-skill `.usage.json` is local to the user's machine — never
transmitted.
