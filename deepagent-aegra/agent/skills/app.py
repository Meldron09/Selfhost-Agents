"""The `/skills` routes behind agent-chat-ui's Skills page.

A `FastAPI` router that `agent/files/app.py:create_app()` includes next to
`/files` and `/mcp/connections` (aegra allows one `http.app`). Issue #43; spec
in #41. Every handler is a plain `def` (see agent/files/app.py for why) and the
router adds no middleware. No new auth: it inherits the deployment's (none).

API contract
------------
Errors are always `{"error": "<message naming the rule that failed>"}`.

`GET /skills` -> `200`, `[{"name": str, "description": str, "hasUi": bool}]`
sorted by name; derived from the folders in the Skill Library.

`GET /skills/{name}` -> `200`, one such entry; `404` if there is no such Skill.

`POST /skills` — multipart, one `file` field holding the Skill as a zip
(`SKILL.md` at the root, or inside a single top-level folder). `201` with the
new entry. Refusals, each writing nothing:

  422  not a zip / unsafe or absolute path or symlink / `SKILL.md` missing /
       frontmatter missing or invalid (needs `name` — lowercase letters,
       digits, hyphens — and `description`) / `ui/` without `ui/index.html` /
       a reference file outside the types `file-reader` supports
  413  total size over the cap (25 MB)
  409  a Skill with that name already exists
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse

from agent.skills import library
from agent.skills.library import SkillError

router = APIRouter(prefix="/skills")


def _refusal(exc: SkillError) -> JSONResponse:
    return JSONResponse({"error": exc.message}, status_code=exc.status)


@router.get("")
def list_skills() -> list[dict[str, Any]]:
    return library.list_skills()


@router.get("/{name}")
def get_skill(name: str) -> Any:
    try:
        return library.get_skill(name)
    except SkillError as exc:
        return _refusal(exc)


@router.post("", status_code=201)
def install_skill(file: UploadFile = File(...)) -> Any:
    try:
        return library.install(file.file.read())
    except SkillError as exc:
        return _refusal(exc)
