"""Skill Run history: one JSON record per Run, kept on the Skill Library's volume (ADR-0011).

Records live in the hidden folder `.runs/` of the library (never listed as a Skill, never
touched by deleting one), keyed by the Run's thread id. `SkillRunMiddleware` writes a record
when a Run starts and completes it when the Run ends; the `/skill-runs` routes read them.
No snapshot of the Skill is kept, only its fingerprint, so a Run whose Skill is gone reads
`skillState: "removed"` and one whose Skill was replaced reads `"updated"`.
Trimming drops the record only: the Outputs of a trimmed Run stay in the file store.
ponytail: each list reads every record file (at most 50 per Skill); an index if that ever shows.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.config import skill_library_dir
from agent.skills import library

CAP = 50
"""Runs kept per Skill; starting one more trims the oldest."""

_ID = re.compile(r"^[A-Za-z0-9_-]+$")  # a thread id; keeps the id from ever being a path


def _dir() -> Path:
    return skill_library_dir() / ".runs"


def _write(record: dict[str, Any]) -> None:
    folder = _dir()
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".{uuid.uuid4().hex}.tmp"
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, folder / f"{record['id']}.json")  # a reader never sees half a record


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _all() -> list[dict[str, Any]]:
    records = (_read(p) for p in _dir().glob("*.json")) if _dir().is_dir() else ()
    return sorted((r for r in records if r), key=lambda r: r["startedAt"], reverse=True)


def start(run_id: str, skill_run: dict[str, Any]) -> None:
    """Record a Run as `running`, then trim that Skill's oldest Runs beyond `CAP`."""
    name = skill_run["name"]
    _write(
        {
            "id": run_id,
            "skill": name,
            "startedAt": datetime.now(timezone.utc).isoformat(),
            "installedAt": datetime.fromtimestamp(library.installed_at(name), timezone.utc).isoformat(),
            "fingerprint": library.fingerprint(name),
            "fields": skill_run.get("fields") or {},
            "files": skill_run.get("files") or {},
            "status": "running",
            "finalMessage": None,
            "outputs": [],
        }
    )
    for old in [r for r in _all() if r["skill"] == name][CAP:]:
        (_dir() / f"{old['id']}.json").unlink(missing_ok=True)


def finish(run_id: str, status: str, final_message: str | None = None, outputs: list[dict] | None = None) -> None:
    """Complete a Run's record; a no-op for a Run that was never recorded."""
    record = _load(run_id)
    if record:
        record.update(status=status, finalMessage=final_message, outputs=outputs or [])
        _write(record)


def _load(run_id: str) -> dict[str, Any] | None:
    return _read(_dir() / f"{run_id}.json") if _ID.match(run_id) else None


def get(run_id: str) -> dict[str, Any] | None:
    record = _load(run_id)
    return _marked([record])[0] if record else None


def list_runs(skill: str | None = None) -> list[dict[str, Any]]:
    """Newest first, all Skills' or just `skill`'s."""
    return _marked([r for r in _all() if skill in (None, r["skill"])])


def _marked(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adds `skillState` (`None`, `"removed"` or `"updated"`), hiding the fingerprint."""
    current: dict[str, str | None] = {}
    marked = []
    for record in records:
        name = record["skill"]
        if name not in current:
            current[name] = library.fingerprint(name)
        state = "removed" if current[name] is None else "updated" if current[name] != record["fingerprint"] else None
        marked.append({**{k: v for k, v in record.items() if k != "fingerprint"}, "skillState": state})
    return marked
