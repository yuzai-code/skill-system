---
description: Direct interface to the skill_manage tool (6 actions). Most users should use /learn instead.
argument-hint: [action] [name] [--flags]
---

# /skill-manage

Low-level interface to the 6 skill_manage actions. Prefer `/learn` for
authoring new skills — it walks the same standards. Use `/skill-manage`
when you need to patch an existing skill, write a supporting file, or
archive one.

## Actions

```
skill_manage(action="create", name="<name>", content="<full SKILL.md>")
skill_manage(action="edit",   name="<name>", content="<full SKILL.md>")
skill_manage(action="patch",  name="<name>", old_string="...", new_string="...",
             [file_path="references/x.md"] [replace_all=false])
skill_manage(action="delete", name="<name>", [absorbed_into="<umbrella>"])
skill_manage(action="write_file",  name="<name>", file_path="references/x.md",
             file_content="...")
skill_manage(action="remove_file", name="<name>", file_path="references/x.md")
```

## Hard rules (HARDLINE)

- `description` ≤ 60 chars in any new frontmatter
- `author` = `hermes-skill-system` (literal, never environment)
- 8-section body when creating
- `delete` archives, never hard-deletes
- `file_path` must start with `references/`, `templates/`, `scripts/`,
  or `assets/` — never `..`, never bare directory
- `absorbed_into="<umbrella>"` for consolidations, `absorbed_into=""`
  for explicit prune (curator path)

For new skills, use `/learn` instead — it walks the same standards and
reminds you of the 60-char description limit.
