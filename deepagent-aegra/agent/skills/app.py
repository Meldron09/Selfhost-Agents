"""The `/skills` routes behind agent-chat-ui's Skills page.

A `FastAPI` router that `agent/files/app.py:create_app()` includes next to
`/files` and `/mcp/connections` (aegra allows one `http.app`). Issues #43, #44, #46; spec
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

`PUT /skills/{name}` — the same multipart zip, deliberately replacing the
installed Skill `name`. `200` with the new entry. Same validation and refusals
as install, except no 409; also `404` if `name` is not installed, and `422` if
the zip's own `name` differs. A refusal leaves the old Skill untouched.

`GET /skills/{name}/ui/{path}` -> the file at `path` in the Skill's `ui/` folder (the bare
`ui/` is `index.html`), with its MIME type, `Access-Control-Allow-Origin: *` and a CSP that
blocks `fetch`/forms (ADR-0010). `404` for a missing file, a folder, a Skill with no `ui/`
or anything outside `ui/`.

`DELETE /skills/{name}` -> `204`, the Skill is gone from the library and the
list; `404` if there is no such Skill. Confirming is the UI's job.

Run history (issue #51; written by `SkillRunMiddleware`, see agent/skills/history.py)
--------------------------------------------------------------------------------
`GET /skill-runs?skill=name` -> `200`, the recorded Skill Runs, newest first (all Skills'
without `skill`; at most 50 per Skill are kept). Each is `{id, skill, startedAt, installedAt,
fields, files, status, finalMessage, outputs, skillState}`: `files` maps a field name to its
`{key, filename}` Attachments, `outputs` is `[{key, filename}]`, `status` is `running`, `done`,
`failed` or `cancelled`, and `skillState` is `null`, `"removed"` (the Skill is deleted) or
`"updated"` (it was replaced since the Run). Works for a Skill that no longer exists.

`GET /skill-runs/{id}` -> `200`, one such record; `404` if there is none (id is the Run's thread id).
"""
from __future__ import annotations

import mimetypes
from typing import Any

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse, Response

from agent.skills import history, library
from agent.skills.library import SkillError

router = APIRouter(prefix="/skills")
runs_router = APIRouter(prefix="/skill-runs")

# A sandboxed frame has an opaque origin, so its own module scripts and assets load
# cross-origin (needs the ACAO header), and the sandbox attribute alone leaves the API
# reachable (needs the CSP). ADR-0010.
_UI_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Content-Security-Policy": "default-src 'self'; connect-src 'none'; form-action 'none'; img-src 'self' data:",
}


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


@router.get("/{name}/ui/{path:path}")
def get_skill_ui(name: str, path: str) -> Response:
    try:
        file = library.ui_file(name, path)
    except SkillError as exc:
        return _refusal(exc)
    return Response(
        file.read_bytes(),
        media_type=mimetypes.guess_type(file.name)[0] or "application/octet-stream",
        headers=_UI_HEADERS,
    )


@router.post("", status_code=201)
def install_skill(file: UploadFile = File(...)) -> Any:
    try:
        return library.install(file.file.read())
    except SkillError as exc:
        return _refusal(exc)


@router.put("/{name}")
def replace_skill(name: str, file: UploadFile = File(...)) -> Any:
    try:
        return library.replace(name, file.file.read())
    except SkillError as exc:
        return _refusal(exc)


@router.delete("/{name}", status_code=204, response_class=Response)
def delete_skill(name: str) -> Response:
    try:
        library.delete(name)
    except SkillError as exc:
        return _refusal(exc)
    return Response(status_code=204)


@runs_router.get("")
def list_skill_runs(skill: str | None = None) -> list[dict[str, Any]]:
    return history.list_runs(skill)


@runs_router.get("/{run_id}")
def get_skill_run(run_id: str) -> Any:
    record = history.get(run_id)
    if record is None:
        return JSONResponse({"error": f"No Skill Run with id {run_id!r}"}, status_code=404)
    return record
