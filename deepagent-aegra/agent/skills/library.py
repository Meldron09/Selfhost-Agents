"""Install and list Skills. The Skill Library is plain folders under
`SKILL_LIBRARY_DIR` (its own volume, never under the served `FILE_STORE_DIR`);
the list is derived from those folders, there is no index to keep in sync.

`install` validates the whole zip in memory first, then unpacks to a hidden
temp folder and renames it into place, so a refused or failed install leaves
nothing behind.
"""
from __future__ import annotations

import io
import re
import shutil
import uuid
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from agent.config import skill_library_dir

MAX_SIZE = 25 * 1024 * 1024
"""Total uncompressed bytes a Skill may hold."""

# The types `file-reader` dispatches on (agent/file_reader.py).
REFERENCE_EXTENSIONS = (".pdf", ".xlsx", ".xls", ".docx", ".pptx", ".txt", ".md")

# The name is also the folder name, so it is restricted to a path-safe slug.
_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_JUNK_DIR = "__MACOSX"
_JUNK_FILE = ".DS_Store"


class SkillError(Exception):
    """A refused install or lookup; `message` names the rule that failed."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def parse_frontmatter(text: str) -> dict[str, str]:
    """`{name, description}` from a `SKILL.md`, or `SkillError` naming what is wrong."""
    match = _FRONTMATTER.match(text)
    if not match:
        raise SkillError(422, "SKILL.md has no frontmatter (a --- block with name and description)")
    try:
        meta: Any = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        raise SkillError(422, "SKILL.md frontmatter is not valid YAML") from None
    if not isinstance(meta, dict):
        raise SkillError(422, "SKILL.md frontmatter must be key: value pairs")
    name, description = meta.get("name"), meta.get("description")
    if not isinstance(name, str) or not _NAME.fullmatch(name) or len(name) > 64:
        raise SkillError(
            422,
            "SKILL.md frontmatter needs a name of lowercase letters, digits and hyphens (max 64 characters)",
        )
    if not isinstance(description, str) or not description.strip():
        raise SkillError(422, "SKILL.md frontmatter needs a description")
    return {"name": name, "description": description.strip()}


def _unsafe(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        "\\" in name
        or "\0" in name
        or path.is_absolute()
        or re.match(r"^[A-Za-z]:", name) is not None
        or ".." in path.parts
    )


_CORRUPT = (zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error, EOFError)
"""What `zipfile` raises for a bad CRC, a lying header, encryption or an unknown codec."""


def _plan(data: bytes) -> tuple[zipfile.ZipFile, dict[str, zipfile.ZipInfo], dict[str, str]]:
    """Validate `data`; returns the open zip, `{relative path: entry}` to unpack, and the metadata."""
    if len(data) > MAX_SIZE:
        raise SkillError(413, f"The zip is too large (limit {MAX_SIZE // 1024 // 1024} MB)")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SkillError(422, "The upload is not a valid zip file") from None

    entries = archive.infolist()
    for info in entries:
        is_symlink = (info.external_attr >> 16) & 0o170000 == 0o120000
        if _unsafe(info.filename) or is_symlink:
            raise SkillError(422, f"Unsafe path in zip: {info.filename!r}")
    if sum(i.file_size for i in entries) > MAX_SIZE:
        raise SkillError(413, f"The Skill is too large (limit {MAX_SIZE // 1024 // 1024} MB unpacked)")

    files = {
        i.filename: i
        for i in entries
        if not i.is_dir()
        and PurePosixPath(i.filename).parts[0] != _JUNK_DIR
        and PurePosixPath(i.filename).name != _JUNK_FILE
    }
    # A zipped folder nests everything under one top-level folder.
    tops = {PurePosixPath(n).parts[0] for n in files}
    if "SKILL.md" not in files and len(tops) == 1 and all(len(PurePosixPath(n).parts) > 1 for n in files):
        top = tops.pop()
        files = {str(PurePosixPath(n).relative_to(top)): i for n, i in files.items()}

    if "SKILL.md" not in files:
        raise SkillError(422, "SKILL.md is missing from the zip")
    meta = parse_frontmatter(archive.read(files["SKILL.md"]).decode("utf-8", "replace"))

    ui = [n for n in files if PurePosixPath(n).parts[0] == "ui"]
    if ui and "ui/index.html" not in files:
        raise SkillError(422, "The ui/ folder is present but ui/index.html is missing")
    for n in files:
        if n != "SKILL.md" and n not in ui and not n.lower().endswith(REFERENCE_EXTENSIONS):
            raise SkillError(
                422,
                f"{n} is not a supported reference file (allowed: {', '.join(REFERENCE_EXTENSIONS)})",
            )
    return archive, files, meta


def describe(folder: Path) -> dict[str, Any] | None:
    """The list entry for one Skill folder, or `None` if it isn't a valid Skill."""
    try:
        meta = parse_frontmatter((folder / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
    except (SkillError, OSError):
        return None
    return {**meta, "hasUi": (folder / "ui" / "index.html").is_file()}


def list_skills() -> list[dict[str, Any]]:
    root = skill_library_dir()
    if not root.is_dir():
        return []
    found = (describe(d) for d in root.iterdir() if d.is_dir() and not d.name.startswith("."))
    return sorted((s for s in found if s), key=lambda s: s["name"])


def get_skill(name: str) -> dict[str, Any]:
    entry = describe(skill_library_dir() / name) if _NAME.fullmatch(name) else None
    if entry is None:
        raise SkillError(404, f"No Skill named {name!r}")
    return entry


def install(data: bytes) -> dict[str, Any]:
    try:
        return _install(data)
    except _CORRUPT:
        raise SkillError(422, "The zip is corrupt or uses an unsupported feature (such as a password)") from None


def _install(data: bytes) -> dict[str, Any]:
    archive, files, meta = _plan(data)
    root = skill_library_dir()
    dest = root / meta["name"]
    if dest.exists():
        raise SkillError(409, f"A Skill named {meta['name']!r} already exists")

    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".installing-{uuid.uuid4().hex}"
    try:
        for rel, info in files.items():
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
        try:
            tmp.rename(dest)
        except OSError:
            if not dest.exists():
                raise
            raise SkillError(409, f"A Skill named {meta['name']!r} already exists") from None
    except (FileExistsError, NotADirectoryError):
        raise SkillError(422, "The zip has conflicting paths (a file and a folder share a name)") from None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return get_skill(meta["name"])
