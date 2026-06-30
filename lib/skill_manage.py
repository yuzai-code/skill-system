"""skill_manage — 6 actions, 6-layer validation, archive-not-delete.

Actions:
  - create        : Create skill + SKILL.md
  - edit          : Full SKILL.md rewrite
  - patch         : Fuzzy find-and-replace (within SKILL.md or supporting file)
  - delete        : Archive to .archive/ (recoverable; absorbed_into declares intent)
  - write_file    : Add supporting file (references/, templates/, scripts/, assets/)
  - remove_file   : Remove supporting file

Hard validation:
  1. name regex + length
  2. frontmatter structure (name, description, version, author)
  3. description ≤ 60 chars (HARD: system truncates, longer = never routes)
  4. author = FORCE_AUTHOR (privacy: never environment identity)
  5. content size limits (100k chars SKILL.md, 1 MiB supporting file)
  6. path security (no traversal, symlink, escape)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import atomic_io, fuzzy_match, schema
from .skill_preprocess import preprocess
from .skill_usage import UsageStore

logger = logging.getLogger(__name__)

SKILLS_DIR: Optional[Path] = None
USAGE: Optional[UsageStore] = None


def configure(skills_dir: Path) -> None:
    """Bind to a specific skills directory. Idempotent."""
    global SKILLS_DIR, USAGE
    SKILLS_DIR = skills_dir
    USAGE = UsageStore(skills_dir)


def _require_config() -> tuple[Path, UsageStore]:
    if SKILLS_DIR is None or USAGE is None:
        raise RuntimeError("skill_manage not configured. Call configure(skills_dir) first.")
    return SKILLS_DIR, USAGE


# ---------- Discovery ----------

def find_skill(name: str) -> Optional[Path]:
    """Locate a skill by its frontmatter name field. Returns the skill directory."""
    skills_dir, _ = _require_config()
    if not skills_dir.exists():
        return None
    for skill_md in skills_dir.rglob("SKILL.md"):
        if any(part in (".archive", ".hub") for part in skill_md.parts):
            continue
        try:
            fm = schema.parse(skill_md.read_text(encoding="utf-8", errors="replace")[:4000])
            if fm.get("name") == name:
                return skill_md.parent
        except Exception:
            continue
    return None


def _skill_not_found_msg(name: str) -> str:
    return (
        f"Skill '{name}' not found. Use skill_list to see available skills, "
        f"or skill_view(name) to inspect one."
    )


# ---------- Path security ----------

def _is_path_redirect(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError:
        return False


def _validate_delete_target(skill_dir: Path) -> Optional[str]:
    skills_dir, _ = _require_config()
    if _is_path_redirect(skill_dir):
        return (
            f"Refusing to delete '{skill_dir}': the skill directory is a symlink. "
            f"Remove the link target manually if intended."
        )
    try:
        resolved = skill_dir.resolve()
        resolved.relative_to(skills_dir.resolve())
    except (ValueError, OSError) as e:
        return f"Refusing to delete '{skill_dir}': not inside skills root ({e})."
    return None


def _resolve_skill_target(skill_dir: Path, file_path: str) -> tuple[Optional[Path], Optional[str]]:
    target = skill_dir / file_path
    try:
        target.resolve().relative_to(skill_dir.resolve())
    except ValueError:
        return None, f"file_path escapes skill directory: {file_path}"
    return target, None


# ---------- Pin guard ----------

def _pinned_guard(name: str) -> Optional[str]:
    _, usage = _require_config()
    rec = usage.get(name)
    if rec.get("pinned"):
        return (
            f"Skill '{name}' is pinned. To delete, unpin first: "
            f"skill pin {name} --off (or remove 'pinned: true' from "
            f".usage.json). Patches and edits are still allowed on pinned skills."
        )
    return None


# ---------- Actions ----------

def action_create(name: str, content: str, category: Optional[str] = None) -> dict[str, Any]:
    skills_dir, _ = _require_config()
    if err := schema.validate_name(name):
        return {"success": False, "error": err}
    if err := schema.validate_content(content):
        return {"success": False, "error": err}
    try:
        fm, body = schema.validate_frontmatter(content)
    except schema.ValidationError as e:
        return {"success": False, "error": str(e)}
    if fm["name"] != name:
        return {
            "success": False,
            "error": (
                f"Frontmatter name '{fm['name']}' does not match skill name '{name}'. "
                f"They must match exactly."
            ),
        }
    if find_skill(name):
        return {
            "success": False,
            "error": f"Skill '{name}' already exists. Use action='edit' to update, or 'patch' for targeted fixes.",
        }
    skill_dir = skills_dir / (category or "") / name if category else skills_dir / name
    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return {"success": False, "error": f"Directory already exists: {skill_dir}"}
    except OSError as e:
        return {"success": False, "error": f"Failed to create directory: {e}"}
    try:
        atomic_io.atomic_write_text(skill_dir / "SKILL.md", content)
    except Exception as e:
        shutil.rmtree(skill_dir, ignore_errors=True)
        return {"success": False, "error": f"Failed to write SKILL.md: {e}"}
    desc = str(fm.get("description", ""))[:60]
    return {
        "success": True,
        "message": f"Skill '{name}' created.",
        "path": str(skill_dir.relative_to(skills_dir)),
        "description": desc,
        "hint": (
            f"To add references/templates/scripts/assets, use "
            f"skill_manage(action='write_file', name='{name}', "
            f"file_path='references/example.md', file_content='...')"
        ),
    }


def action_edit(name: str, content: str) -> dict[str, Any]:
    if err := schema.validate_content(content):
        return {"success": False, "error": err}
    try:
        fm, _ = schema.validate_frontmatter(content)
    except schema.ValidationError as e:
        return {"success": False, "error": str(e)}
    if fm["name"] != name:
        return {
            "success": False,
            "error": f"Frontmatter name '{fm['name']}' does not match '{name}'.",
        }
    skill_dir = find_skill(name)
    if not skill_dir:
        return {"success": False, "error": _skill_not_found_msg(name)}
    skill_md = skill_dir / "SKILL.md"
    try:
        atomic_io.atomic_write_text(skill_md, content)
    except Exception as e:
        return {"success": False, "error": f"Failed to write SKILL.md: {e}"}
    return {
        "success": True,
        "message": f"Skill '{name}' updated (full rewrite).",
        "path": str(skill_dir),
    }


def action_patch(
    name: str,
    old: str,
    new: str,
    file_path: Optional[str] = None,
    replace_all: bool = False,
) -> dict[str, Any]:
    if not old:
        return {"success": False, "error": "old_string is required for 'patch'."}
    if new is None:
        return {
            "success": False,
            "error": "new_string is required for 'patch'. Use empty string to delete matched text.",
        }
    skill_dir = find_skill(name)
    if not skill_dir:
        return {"success": False, "error": _skill_not_found_msg(name)}
    if file_path:
        if err := schema.validate_file_path(file_path):
            return {"success": False, "error": err}
        target, err = _resolve_skill_target(skill_dir, file_path)
        if err:
            return {"success": False, "error": err}
    else:
        target = skill_dir / "SKILL.md"
    if not target.exists():
        return {
            "success": False,
            "error": f"File not found: {target.relative_to(skill_dir)}",
        }
    try:
        content = target.read_text(encoding="utf-8")
    except OSError as e:
        return {"success": False, "error": f"Failed to read: {e}"}
    result = fuzzy_match.fuzzy_find_and_replace(content, old, new, replace_all)
    if result.error and result.match_count == 0:
        preview = content[:500] + ("..." if len(content) > 500 else "")
        return {
            "success": False,
            "error": result.error,
            "file_preview": preview,
        }
    if not file_path and not target.name.startswith("."):
        if target.name == "SKILL.md":
            try:
                schema.validate_frontmatter(result.new_content)
            except schema.ValidationError as e:
                return {
                    "success": False,
                    "error": f"Patch would break SKILL.md structure: {e}",
                }
    if err := schema.validate_content(result.new_content, label=target.name):
        return {"success": False, "error": err}
    try:
        atomic_io.atomic_write_text(target, result.new_content)
    except Exception as e:
        return {"success": False, "error": f"Failed to write: {e}"}
    _, usage = _require_config()
    usage.bump(name, kind="patch")
    return {
        "success": True,
        "message": (
            f"Patched {target.relative_to(skill_dir)} in '{name}' "
            f"({result.match_count} match{'es' if result.match_count != 1 else ''}, "
            f"strategy={result.strategy})."
        ),
    }


def action_delete(name: str, absorbed_into: Optional[str] = None) -> dict[str, Any]:
    skills_dir, usage = _require_config()
    if err := _pinned_guard(name):
        return {"success": False, "error": err}
    skill_dir = find_skill(name)
    if not skill_dir:
        return {"success": False, "error": _skill_not_found_msg(name)}
    if err := _validate_delete_target(skill_dir):
        return {"success": False, "error": err}
    archive_root = skills_dir / ".archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    dest = archive_root / skill_dir.name
    if dest.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        dest = archive_root / f"{skill_dir.name}-{stamp}"
    try:
        shutil.move(str(skill_dir), str(dest))
    except Exception as e:
        return {"success": False, "error": f"Failed to archive: {e}"}
    if absorbed_into is not None and absorbed_into != "":
        usage.set_absorbed_into(name, absorbed_into)
    usage.set_state(name, "archived")
    parent = skill_dir.parent
    if parent.exists() and parent != skills_dir and not any(parent.iterdir()):
        try:
            parent.rmdir()
        except OSError:
            pass
    msg = f"Skill '{name}' archived to {dest}."
    if absorbed_into:
        msg += f" Content absorbed_into='{absorbed_into}'."
    return {
        "success": True,
        "message": msg,
        "archived": True,
        "archive_path": str(dest),
    }


def action_write_file(name: str, file_path: str, file_content: str) -> dict[str, Any]:
    if err := schema.validate_file_path(file_path):
        return {"success": False, "error": err}
    if file_content is None:
        return {"success": False, "error": "file_content is required."}
    if len(file_content.encode("utf-8")) > schema.MAX_FILE_BYTES:
        return {
            "success": False,
            "error": (
                f"File content is {len(file_content.encode('utf-8')):,} bytes "
                f"(limit: {schema.MAX_FILE_BYTES:,} / 1 MiB). Consider splitting."
            ),
        }
    if err := schema.validate_content(file_content, label=file_path):
        return {"success": False, "error": err}
    skill_dir = find_skill(name)
    if not skill_dir:
        return {
            "success": False,
            "error": f"Skill '{name}' not found. Create it first with action='create'.",
        }
    target, err = _resolve_skill_target(skill_dir, file_path)
    if err:
        return {"success": False, "error": err}
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_io.atomic_write_text(target, file_content)
    except Exception as e:
        return {"success": False, "error": f"Failed to write: {e}"}
    return {
        "success": True,
        "message": f"File '{file_path}' written to skill '{name}'.",
        "path": str(target),
    }


def action_remove_file(name: str, file_path: str) -> dict[str, Any]:
    if err := schema.validate_file_path(file_path):
        return {"success": False, "error": err}
    skill_dir = find_skill(name)
    if not skill_dir:
        return {"success": False, "error": _skill_not_found_msg(name)}
    target, err = _resolve_skill_target(skill_dir, file_path)
    if err:
        return {"success": False, "error": err}
    if not target.exists():
        available: list[str] = []
        for sub in schema.ALLOWED_SUBDIRS:
            d = skill_dir / sub
            if d.exists():
                for f in d.rglob("*"):
                    if f.is_file():
                        available.append(str(f.relative_to(skill_dir)))
        return {
            "success": False,
            "error": f"File '{file_path}' not found in skill '{name}'.",
            "available_files": available or None,
        }
    target.unlink()
    parent = target.parent
    if parent != skill_dir and parent.exists() and not any(parent.iterdir()):
        try:
            parent.rmdir()
        except OSError:
            pass
    return {"success": True, "message": f"File '{file_path}' removed from '{name}'."}


# ---------- Dispatcher ----------

ACTIONS = {
    "create": action_create,
    "edit": action_edit,
    "patch": action_patch,
    "delete": action_delete,
    "write_file": action_write_file,
    "remove_file": action_remove_file,
}


def skill_manage(
    action: str,
    name: str,
    content: Optional[str] = None,
    category: Optional[str] = None,
    file_path: Optional[str] = None,
    file_content: Optional[str] = None,
    old_string: Optional[str] = None,
    new_string: Optional[str] = None,
    replace_all: bool = False,
    absorbed_into: Optional[str] = None,
) -> dict[str, Any]:
    """Dispatch a skill_manage call. Returns a dict result (NOT json string).

    The CLI entry point in bin/skill-manage converts to JSON.
    """
    if action not in ACTIONS:
        return {
            "success": False,
            "error": (
                f"Unknown action '{action}'. Use: "
                f"{', '.join(sorted(ACTIONS))}"
            ),
        }
    if action == "create":
        if not content:
            return {"success": False, "error": "content is required for 'create'."}
        result = action_create(name, content, category)
    elif action == "edit":
        if not content:
            return {"success": False, "error": "content is required for 'edit'."}
        result = action_edit(name, content)
    elif action == "patch":
        result = action_patch(name, old_string or "", new_string, file_path, replace_all)
    elif action == "delete":
        result = action_delete(name, absorbed_into)
    elif action == "write_file":
        if not file_path:
            return {"success": False, "error": "file_path is required for 'write_file'."}
        result = action_write_file(name, file_path, file_content or "")
    elif action == "remove_file":
        if not file_path:
            return {"success": False, "error": "file_path is required for 'remove_file'."}
        result = action_remove_file(name, file_path)
    else:
        result = {"success": False, "error": "unreachable"}
    if result.get("success") and action in ("create", "write_file", "remove_file"):
        _, usage = _require_config()
        usage.bump(name, kind="use")
    return result


# ---------- Restore ----------

def action_restore(name: str) -> dict[str, Any]:
    skills_dir, usage = _require_config()
    archive_root = skills_dir / ".archive"
    if not archive_root.exists():
        return {"success": False, "error": "No archive directory."}
    candidates = [p for p in archive_root.rglob("*") if p.is_dir() and p.name == name]
    if not candidates:
        prefix = f"{name}-"
        candidates = sorted(
            [
                p for p in archive_root.rglob("*")
                if p.is_dir()
                and p.name.startswith(prefix)
                and len(p.name) - len(prefix) == 14
                and p.name[len(prefix):].isdigit()
            ],
            reverse=True,
        )
    if not candidates:
        return {"success": False, "error": f"Skill '{name}' not found in archive."}
    src = candidates[0]
    dest = skills_dir / name
    if dest.exists():
        return {"success": False, "error": f"Destination already exists: {dest}"}
    try:
        src.rename(dest)
    except OSError:
        try:
            shutil.move(str(src), str(dest))
        except Exception as e:
            return {"success": False, "error": f"Failed to restore: {e}"}
    usage.set_state(name, "active")
    return {"success": True, "message": f"Skill '{name}' restored from {src}."}
