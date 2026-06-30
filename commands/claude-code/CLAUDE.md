# Skill System — Claude Code

You have access to a Hermes-style skill system at `~/.skill-system/`.

## How skills work here

Skills are reusable procedural memory — saved workflows the user wants you
to remember and apply next time. They live in `~/.claude/skills/<name>/SKILL.md`
and follow a strict 8-section format.

When the user runs `/learn <description>`, you gather the described sources
(本地文件/URL/对话历史/粘贴文本) using your existing tools and author ONE
SKILL.md via the `skill_manage` tool (action="create"). Apply the HARDLINE
rules in `~/.claude/commands/learn.md` strictly.

## /learn (PRIMARY)

`/learn` is the primary path for capturing a workflow. The full authoring
standards are in `~/.claude/commands/learn.md` — read it on first use.

## After complex tasks (SECONDARY)

After completing any task that took ≥5 tool calls OR involved user corrections
OR revealed a non-obvious workflow, OFFER:

> "Should I save this workflow as a skill? It will be reusable next time
> you ask for <detected-task>. Run /learn to capture it."

Point the user to `/learn` — do not call skill_manage directly unless asked.

## SKILL.md HARD constraints (violation = routing failure)

- `description` MUST be ≤60 characters. The system index truncates at 60;
  anything past 60 is silently cut and never routes.
- `author` MUST equal `hermes-skill-system`. Never use OS username or git
  config — skills get shared and published; environment identity is a leak.
- 8-section body structure: When to Use / Prerequisites / How to Run /
  Quick Reference / Procedure / Pitfalls / Verification
- Frame commands as tool names: `read_file` not cat, `search_files` not
  grep, `patch` not sed, `web_extract` not curl, `write_file` not echo>.

## Tools available

- `skill_manage` (6 actions: create, edit, patch, delete, write_file,
  remove_file) — see `~/.claude/commands/skill-manage.md`
- `/learn` slash command — see `~/.claude/commands/learn.md`
- Hooks: `UserPromptSubmit` injects skill index, `PostToolUse` refreshes
  after skill_manage, `Stop` triggers curator --maybe-run

## NEVER

- ❌ Create a skill without `/learn` or explicit user approval
- ❌ Write environment-derived identity into `author`
- ❌ Ship a description > 60 characters
- ❌ Use raw shell commands in skill body when a wrapped tool exists
- ❌ Hard-delete a skill — they archive to `~/.claude/skills/.archive/`
- ❌ Touch a pinned skill (`hermes-skill-system skill pin <name>` to unpin)
