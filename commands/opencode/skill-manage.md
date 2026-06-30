---
name: skill-manage
description: Direct interface to skill_manage (6 actions). Prefer /learn.
---

# /skill-manage

Low-level interface to 6 skill_manage actions. Use `/learn` for new skills.

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

HARD: description ≤ 60, author = `hermes-skill-system`, 8-section body,
file_path under references/templates/scripts/assets, no `..`.
