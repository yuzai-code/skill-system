"""Curator — inactivity-triggered skill maintenance.

Two phases:
  1. apply_automatic_transitions() — pure function, no LLM.
     Walks .usage.json, marks stale (>30d inactive) or archived (>90d).
     Skips pinned, use_count=0 grace floor.
  2. run_consolidation() — opt-in LLM pass, off by default.
     Stub: would call main LLM with umbrella-building prompt; we skip by default.

Trigger:
  - Inactivity-triggered: gate checks last_run_at + interval (7d default).
  - First run after install: deferred (seed state, no real pass).
  - Manual: `skill-curator run` bypasses gates.

Reports: written to logs/curator/{stamp}/run.json + REPORT.md
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .skill_usage import STATE_ACTIVE, STATE_ARCHIVED, STATE_STALE, UsageStore

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_HOURS = 24 * 7
DEFAULT_STALE_AFTER_DAYS = 30
DEFAULT_ARCHIVE_AFTER_DAYS = 90
DEFAULT_CONSOLIDATE = False


def _state_file(skills_dir: Path) -> Path:
    return skills_dir / ".curator_state"


def _default_state() -> dict[str, Any]:
    return {
        "last_run_at": None,
        "last_run_summary": None,
        "paused": False,
        "run_count": 0,
    }


def load_curator_state(skills_dir: Path) -> dict[str, Any]:
    path = _state_file(skills_dir)
    if not path.exists():
        return _default_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            base = _default_state()
            base.update(data)
            return base
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("Failed to read curator state: %s", e)
    return _default_state()


def save_curator_state(skills_dir: Path, data: dict[str, Any]) -> None:
    path = _state_file(skills_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(path.parent), prefix=".curator_state_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.debug("Failed to save curator state: %s", e)


def should_run_now(skills_dir: Path, now: Optional[datetime] = None) -> bool:
    """Gate: only run if interval has elapsed since last_run_at.

    First-run deferral: when last_run_at is None, seed to now and return False.
    """
    state = load_curator_state(skills_dir)
    if state.get("paused"):
        return False
    last = state.get("last_run_at")
    if now is None:
        now = datetime.now(timezone.utc)
    if not last:
        state["last_run_at"] = now.isoformat()
        state["last_run_summary"] = (
            "deferred first run — curator seeded, will run after one interval"
        )
        save_curator_state(skills_dir, state)
        return False
    try:
        last_dt = datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return True
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    return (now - last_dt) >= timedelta(hours=DEFAULT_INTERVAL_HOURS)


def apply_automatic_transitions(skills_dir: Path, now: Optional[datetime] = None) -> dict[str, int]:
    """Pure state transition walk. No LLM. No file mutation outside .usage.json."""
    if now is None:
        now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=DEFAULT_STALE_AFTER_DAYS)
    archive_cutoff = now - timedelta(days=DEFAULT_ARCHIVE_AFTER_DAYS)

    usage = UsageStore(skills_dir)
    counts = {"checked": 0, "marked_stale": 0, "archived": 0, "reactivated": 0, "skipped": 0}
    if not skills_dir.exists():
        return counts

    data = usage.load()
    for name, rec in data.items():
        counts["checked"] += 1
        if rec.get("pinned"):
            counts["skipped"] += 1
            continue
        if rec.get("state") == STATE_ARCHIVED:
            continue
        latest_str = usage.latest_activity_at(rec)
        anchor_str = latest_str or rec.get("created_at")
        if not anchor_str:
            counts["skipped"] += 1
            continue
        try:
            anchor = datetime.fromisoformat(anchor_str)
        except (TypeError, ValueError):
            counts["skipped"] += 1
            continue
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)

        use_count = int(rec.get("use_count", 0) or 0)
        current = rec.get("state", STATE_ACTIVE)

        if use_count == 0 and anchor > stale_cutoff:
            if current == STATE_STALE:
                usage.set_state(name, STATE_ACTIVE)
                counts["reactivated"] += 1
            continue

        if anchor <= archive_cutoff and current != STATE_ARCHIVED:
            if archive_skill_dir(skills_dir, name):
                usage.set_state(name, STATE_ARCHIVED)
                counts["archived"] += 1
        elif anchor <= stale_cutoff and current == STATE_ACTIVE:
            usage.set_state(name, STATE_STALE)
            counts["marked_stale"] += 1
        elif anchor > stale_cutoff and current == STATE_STALE:
            usage.set_state(name, STATE_ACTIVE)
            counts["reactivated"] += 1
    return counts


def archive_skill_dir(skills_dir: Path, name: str) -> bool:
    """Move skill dir to .archive/. Recoverable."""
    skill_dir = skills_dir / name
    if not skill_dir.exists() or not skill_dir.is_dir():
        return False
    archive_root = skills_dir / ".archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    dest = archive_root / skill_dir.name
    if dest.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        dest = archive_root / f"{skill_dir.name}-{stamp}"
    import shutil
    try:
        shutil.move(str(skill_dir), str(dest))
        return True
    except OSError as e:
        logger.debug("Failed to archive %s: %s", name, e)
        return False


def run_consolidation(skills_dir: Path) -> dict[str, Any]:
    """LLM consolidation pass — opt-in, off by default.

    Stub. Real impl would call the main LLM with the umbrella-building
    prompt and 3-source classification.
    """
    if not DEFAULT_CONSOLIDATE and not os.environ.get("SKILL_CONSOLIDATE"):
        return {
            "ran": False,
            "reason": "consolidation is off by default. Set SKILL_CONSOLIDATE=1 to enable.",
        }
    return {
        "ran": False,
        "reason": "LLM consolidation not yet implemented; use umbrella prompt.",
    }


def main(argv: list[str]) -> int:
    """CLI entry. argv[0] is the program name."""
    skills_dir_env = os.environ.get("SKILLS_DIR")
    if not skills_dir_env:
        print("error: SKILLS_DIR env var required", file=sys.stderr)
        return 2
    skills_dir = Path(skills_dir_env)
    args = argv[1:]
    if "--maybe-run" in args:
        if should_run_now(skills_dir):
            counts = apply_automatic_transitions(skills_dir)
            state = load_curator_state(skills_dir)
            state["last_run_at"] = datetime.now(timezone.utc).isoformat()
            state["last_run_summary"] = (
                f"checked={counts['checked']} "
                f"stale={counts['marked_stale']} "
                f"archived={counts['archived']} "
                f"reactivated={counts['reactivated']} "
                f"skipped={counts['skipped']}"
            )
            state["run_count"] = state.get("run_count", 0) + 1
            save_curator_state(skills_dir, state)
            print(json.dumps(counts))
        return 0
    if "--run-once" in args:
        counts = apply_automatic_transitions(skills_dir)
        state = load_curator_state(skills_dir)
        state["last_run_at"] = datetime.now(timezone.utc).isoformat()
        state["run_count"] = state.get("run_count", 0) + 1
        save_curator_state(skills_dir, state)
        print(json.dumps(counts, indent=2))
        return 0
    if "--consolidate" in args:
        result = run_consolidation(skills_dir)
        print(json.dumps(result, indent=2))
        return 0
    if "--status" in args:
        state = load_curator_state(skills_dir)
        print(json.dumps(state, indent=2))
        return 0
    if "--pause" in args:
        state = load_curator_state(skills_dir)
        state["paused"] = True
        save_curator_state(skills_dir, state)
        print("curator paused")
        return 0
    if "--resume" in args:
        state = load_curator_state(skills_dir)
        state["paused"] = False
        save_curator_state(skills_dir, state)
        print("curator resumed")
        return 0
    print(__doc__, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
