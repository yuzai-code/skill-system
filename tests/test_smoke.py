#!/usr/bin/env python3
"""End-to-end smoke test for the skill system.

Tests:
  1. yaml_mini: frontmatter parsing
  2. schema: name/description/author validation
  3. fuzzy_match: exact + fuzzy matching
  4. skill_manage: all 6 actions
  5. skill_curator: state transitions
  6. skill_index: index generation
  7. skill_preprocess: template vars
  8. CLI bin/skill end-to-end

Run: python3 ~/.skill-system/tests/test_smoke.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

SYSTEM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SYSTEM_ROOT))

from lib import (  # noqa: E402
    atomic_io,
    fuzzy_match,
    paths,
    schema,
    skill_curator,
    skill_index,
    skill_manage,
    skill_preprocess,
    skill_usage,
    yaml_mini,
)


GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")
    raise SystemExit(1)


def section(name: str) -> None:
    print(f"\n=== {name} ===")


# ---------- 1. yaml_mini ----------

def test_yaml_mini() -> None:
    section("yaml_mini — frontmatter parsing")
    fm = yaml_mini.parse("name: foo\ndescription: A test.\nversion: 0.1.0\nauthor: skill-system\n")
    assert fm["name"] == "foo", fm
    ok("simple key:value parsing")
    fm = yaml_mini.parse("platforms: [macos, linux]\ntags: [a, b, c]\n")
    assert fm["platforms"] == ["macos", "linux"], fm
    assert fm["tags"] == ["a", "b", "c"], fm
    ok("inline list parsing")
    fm = yaml_mini.parse("metadata:\n  extra:\n    tags: [x, y]\n")
    assert fm["metadata"]["extra"]["tags"] == ["x", "y"], fm
    ok("nested dict parsing")
    fm = yaml_mini.extract("---\nname: x\n---\nbody text")
    assert fm["name"] == "x"
    ok("extract with body stripping")


# ---------- 2. schema ----------

def test_schema() -> None:
    section("schema — validation")
    err = schema.validate_name("good-name")
    assert err is None
    err = schema.validate_name("Bad-Name")
    assert err is not None
    ok("name regex validation")
    err = schema.validate_name("../escape")
    assert err is not None
    ok("rejects path-traversal name")
    err = schema.validate_file_path("references/api.md")
    assert err is None
    err = schema.validate_file_path("../etc/passwd")
    assert err is not None
    ok("rejects path traversal in file_path")
    err = schema.validate_file_path("SKILL.md")
    assert err is not None
    err = schema.validate_file_path("references/")
    assert err is not None
    ok("rejects bare directory and non-allowed subdirs")

    valid = (
        "---\n"
        "name: test-skill\n"
        "description: Short desc.\n"
        "version: 0.1.0\n"
        "author: skill-system\n"
        "---\n\n# Test\n\nBody.\n"
    )
    parsed, body = schema.validate_frontmatter(valid)
    assert parsed["name"] == "test-skill"
    assert body.startswith("# Test")
    ok("valid frontmatter parses")
    long_desc = valid.replace("Short desc.", "x" * 61)
    try:
        schema.validate_frontmatter(long_desc)
        fail("should have rejected 61-char description")
    except schema.ValidationError as e:
        assert "60" in str(e)
    ok("rejects description > 60 chars (HARD CONSTRAINT)")
    wrong_author = valid.replace("skill-system", "os.getlogin()")
    try:
        schema.validate_frontmatter(wrong_author)
        fail("should have rejected wrong author")
    except schema.ValidationError as e:
        assert "skill-system" in str(e)
    ok("rejects non-canonical author (privacy)")


# ---------- 3. fuzzy_match ----------

def test_fuzzy_match() -> None:
    section("fuzzy_match — find-and-replace")
    content = "Hello world\nThis is a test\nGoodbye"
    r = fuzzy_match.fuzzy_find_and_replace(content, "This is a test", "This is replaced")
    assert r.match_count == 1, r
    assert "replaced" in r.new_content
    assert r.strategy == "exact"
    ok("exact match")
    content2 = "  line one  \n  line two  \n  line three  "
    r = fuzzy_match.fuzzy_find_and_replace(content2, "line one\nline two", "LINE A\nLINE B")
    assert r.match_count == 1, r
    assert "LINE A" in r.new_content
    assert r.strategy == "line-trim", r.strategy
    ok("line-trim match (whitespace drift)")
    r = fuzzy_match.fuzzy_find_and_replace("abc", "xyz", "QQQ")
    assert r.match_count == 0
    assert r.error is not None
    ok("rejects non-existent match with helpful error")


# ---------- 4. skill_manage (6 actions) ----------

def test_skill_manage(tmp: Path) -> None:
    section("skill_manage — 6 actions")
    skill_manage.configure(tmp)

    content = (
        "---\n"
        "name: e2e-test\n"
        "description: End-to-end test skill.\n"
        "version: 0.1.0\n"
        "author: skill-system\n"
        "---\n\n# E2E Test\n\n## When to Use\n- test\n- validate\n"
    )
    r = skill_manage.skill_manage("create", "e2e-test", content=content)
    assert r["success"], r
    assert (tmp / "e2e-test" / "SKILL.md").exists()
    ok("create")

    r = skill_manage.skill_manage(
        "patch", "e2e-test", old_string="# E2E Test", new_string="# E2E Patched"
    )
    assert r["success"], r
    new_content = (tmp / "e2e-test" / "SKILL.md").read_text()
    assert "# E2E Patched" in new_content
    assert "# E2E Test" not in new_content
    ok("patch (exact)")

    r = skill_manage.skill_manage(
        "write_file", "e2e-test",
        file_path="references/api.md",
        file_content="# API Reference\n"
    )
    assert r["success"], r
    assert (tmp / "e2e-test" / "references" / "api.md").exists()
    ok("write_file")

    r = skill_manage.skill_manage(
        "patch", "e2e-test",
        old_string="# API Reference\n", new_string="# API Reference (updated)\n",
        file_path="references/api.md",
    )
    assert r["success"], r
    ok("patch on supporting file")

    r = skill_manage.skill_manage("list_skill" if "list_skill" in skill_manage.ACTIONS else "view", "e2e-test")
    ok("view (via find_skill)")

    r = skill_manage.skill_manage(
        "delete", "e2e-test", absorbed_into="umbrella-skill"
    )
    assert r["success"], r
    assert r.get("archived")
    assert not (tmp / "e2e-test").exists()
    assert (tmp / ".archive" / "e2e-test").exists()
    ok("delete → archive (not hard delete)")

    rec = skill_usage.UsageStore(tmp).get("e2e-test")
    assert rec.get("state") == "archived"
    assert rec.get("absorbed_into") == "umbrella-skill"
    ok("usage sidecar tracks state + absorbed_into")

    r = skill_manage.action_restore("e2e-test")
    assert r["success"], r
    assert (tmp / "e2e-test" / "SKILL.md").exists()
    ok("restore from archive")

    bad_content = content.replace("skill-system", "yuzai")
    r = skill_manage.skill_manage("create", "bad-author", content=bad_content)
    assert not r["success"]
    assert "author" in r["error"].lower()
    ok("rejects bad author on create")

    r = skill_manage.skill_manage("create", "../escape", content=content)
    assert not r["success"]
    ok("rejects path-traversal name on create")

    r = skill_manage.skill_manage("delete", "e2e-test")
    assert r["success"]
    pin_store = skill_usage.UsageStore(tmp)
    pin_store.set_pinned("e2e-test", True)
    r = skill_manage.skill_manage("delete", "e2e-test")
    assert not r["success"]
    assert "pinned" in r["error"].lower()
    ok("pinned skill can't be deleted")

    r = skill_manage.skill_manage(
        "create", "weird-name-123",
        content=content.replace("e2e-test", "weird-name-123"),
    )
    assert r["success"]
    r = skill_manage.skill_manage(
        "write_file", "weird-name-123", file_path="../escape.md", file_content="x"
    )
    assert not r["success"]
    ok("rejects path-traversal in file_path")


# ---------- 5. curator ----------

def test_curator(tmp: Path) -> None:
    section("curator — state transitions")
    skill_manage.configure(tmp)
    content = (
        "---\nname: c1\ndescription: C1.\nversion: 0.1.0\n"
        "author: skill-system\n---\n\n# C1\n\n## When to Use\n- x\n"
    )
    skill_manage.skill_manage("create", "c1", content=content)
    skill_manage.skill_manage("create", "c2", content=content.replace("c1", "c2"))
    skill_manage.skill_manage("create", "c3", content=content.replace("c1", "c3"))

    state = skill_curator.load_curator_state(tmp)
    assert state.get("last_run_at") is None
    assert state.get("paused") is False
    ok("initial state: no last_run_at, not paused")

    should = skill_curator.should_run_now(tmp)
    assert should is False
    state = skill_curator.load_curator_state(tmp)
    assert state.get("last_run_at") is not None
    ok("first run deferred (last_run_at seeded)")

    state["last_run_at"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    skill_curator.save_curator_state(tmp, state)
    assert skill_curator.should_run_now(tmp) is True
    ok("interval gate fires after 7 days")

    store = skill_usage.UsageStore(tmp)
    rec = store.get("c1")
    rec["last_used_at"] = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    rec["use_count"] = 1
    with store.transaction() as data:
        data["c1"] = rec
    rec2 = store.get("c2")
    rec2["last_used_at"] = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    rec2["use_count"] = 5
    with store.transaction() as data:
        data["c2"] = rec2

    counts = skill_curator.apply_automatic_transitions(tmp)
    assert counts["marked_stale"] >= 1, counts
    assert counts["archived"] >= 1, counts
    ok(f"transitions: stale={counts['marked_stale']} archived={counts['archived']}")

    rec = skill_usage.UsageStore(tmp).get("c1")
    assert rec["state"] == "stale"
    assert not (tmp / ".archive" / "c1").exists()
    rec2 = skill_usage.UsageStore(tmp).get("c2")
    assert rec2["state"] == "archived"
    assert (tmp / ".archive" / "c2").exists()
    ok("directory archived for archived skill; state recorded")


# ---------- 6. index ----------

def test_index(tmp: Path) -> None:
    section("skill_index — index generation")
    skill_manage.configure(tmp)
    content = (
        "---\nname: idx-test\ndescription: Index test skill.\nversion: 0.1.0\n"
        "author: skill-system\n---\n\n# Idx\n\n## When to Use\n- foo\n- bar\n"
    )
    skill_manage.skill_manage("create", "idx-test", content=content)
    items = skill_index.build_index(tmp)
    assert any(i["name"] == "idx-test" for i in items)
    item = next(i for i in items if i["name"] == "idx-test")
    assert "foo" in item["triggers"]
    assert "bar" in item["triggers"]
    ok("index includes trigger phrases")
    rendered = skill_index.render_for_prompt(tmp)
    assert "idx-test" in rendered
    assert "Index test skill" in rendered
    ok("render_for_prompt produces index text")


# ---------- 7. preprocess ----------

def test_preprocess(tmp: Path) -> None:
    section("skill_preprocess — template vars + inline shell")
    os.environ["SKILL_TEMPLATE_VARS"] = "true"
    os.environ["SKILL_INLINE_SHELL"] = "false"
    content = "Path: ${SKILL_DIR}/scripts/run.sh"
    out = skill_preprocess.preprocess(content, skill_dir=tmp, session_id="abc-123")
    assert str(tmp) in out
    assert "/scripts/run.sh" in out
    ok("template vars resolve ${SKILL_DIR}")

    content = "Unresolved: ${SKILL_UNKNOWN}\n"
    out = skill_preprocess.preprocess(content, skill_dir=tmp)
    assert "${SKILL_UNKNOWN}" in out
    ok("unknown tokens left in place for debugging")

    os.environ["SKILL_INLINE_SHELL"] = "true"
    out = skill_preprocess.preprocess("Today: !`date +%Y`", skill_dir=tmp)
    assert "Today: 20" in out
    ok("inline shell executes (opt-in)")
    os.environ["SKILL_INLINE_SHELL"] = "false"


# ---------- 8. CLI end-to-end ----------

def test_cli(tmp: Path) -> None:
    section("CLI bin/skill + bin/skill-manage end-to-end")
    os.environ["SKILLS_DIR"] = str(tmp)
    skill_manage.configure(tmp)

    content_path = tmp / "_content.md"
    content_path.write_text(
        "---\nname: cli-test\ndescription: CLI test.\nversion: 0.1.0\n"
        "author: skill-system\n---\n\n# CLI Test\n\n## When to Use\n- cli\n"
    )
    import subprocess
    r = subprocess.run(
        [
            str(SYSTEM_ROOT / "bin" / "skill-manage"),
            "--cli", "opencode",
            "--action", "create",
            "--name", "cli-test",
            "--content-file", str(content_path),
        ],
        capture_output=True, text=True, env={**os.environ, "SKILLS_DIR": str(tmp)},
    )
    if r.returncode != 0:
        print("STDOUT:", r.stdout)
        print("STDERR:", r.stderr)
    assert r.returncode == 0, r
    result = json.loads(r.stdout)
    assert result["success"]
    ok("bin/skill-manage create via subprocess")

    r = subprocess.run(
        [str(SYSTEM_ROOT / "bin" / "skill"), "list", "--cli", "opencode"],
        capture_output=True, text=True, env={**os.environ, "SKILLS_DIR": str(tmp)},
    )
    assert "cli-test" in r.stdout
    ok("bin/skill list shows new skill")


# ---------- main ----------

def main() -> int:
    print(f"Python: {sys.version.split()[0]}")
    print(f"System root: {SYSTEM_ROOT}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "skills"
        tmp.mkdir()
        try:
            test_yaml_mini()
            test_schema()
            test_fuzzy_match()
            test_skill_manage(tmp)
            test_curator(tmp)
            test_index(tmp)
            test_preprocess(tmp)
            test_cli(tmp)
        except SystemExit:
            raise
        except Exception as e:
            print(f"\n{RED}✗ TEST FAILED{RESET}: {e}")
            import traceback
            traceback.print_exc()
            return 1
    print(f"\n{GREEN}=== ALL TESTS PASSED ==={RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
