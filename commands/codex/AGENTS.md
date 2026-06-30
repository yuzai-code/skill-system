# Skill System — Codex

You have access to a Hermes-style skill system at `~/.skill-system/`.

## How skills work here

Skills are reusable procedural memory — saved workflows the user wants you
to remember and apply next time. They live in `~/.codex/skills/<name>/SKILL.md`
and follow a strict 8-section format.

When the user runs `/learn <description>`, you gather the described
sources using your existing tools and author ONE SKILL.md via the
`skill_manage` tool (action="create"). Apply the HARDLINE rules in
`~/.codex/prompts/learn.md` strictly.

## /learn (PRIMARY)

`/learn` is the primary path for capturing a workflow.

## After complex tasks (SECONDARY)

After completing any task that took ≥5 tool calls OR involved user
corrections OR revealed a non-obvious workflow, OFFER:

> "Should I save this workflow as a skill? It will be reusable next time
> you ask for <detected-task>. Run /learn to capture it."

## Tools available

- **`skill_manage` — registered as a native MCP tool** (NOT a shell script).
  When you see `skill_manage` in your available tools list, call it directly
  with structured arguments: `skill_manage(action="create", name="x", content="...")`.
  The MCP server at `~/.skill-system/bin/skill-manage-mcp` handles all 6 actions
  with HARD-constraint validation. Schema is in your tool list; descriptions
  document the 60-char limit and `hermes-skill-system` author requirement.
- ❌ Do NOT invoke `skill_manage` via Bash/terminal — it's a native MCP tool.

## SKILL.md HARD constraints

- `description` MUST be ≤60 characters.
- `author` MUST equal `hermes-skill-system`. Never environment identity.
- 8-section body structure.
- Frame commands as tool names.

## Skill index (auto-injected)

{{SKILL_INDEX}}

## NEVER

- ❌ Create a skill without `/learn` or explicit user approval
- ❌ Write environment-derived identity into `author`
- ❌ Ship a description > 60 characters
- ❌ Use raw shell commands when a wrapped tool exists
- ❌ Hard-delete a skill — they archive to `~/.codex/skills/.archive/`
