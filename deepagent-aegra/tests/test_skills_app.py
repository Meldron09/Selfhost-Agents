"""The `/skills` HTTP routes: `TestClient` against `create_app()`.

Real zips, real folders under `tmp_path`; nothing leaves the machine. Contract:
the module docstring of agent/skills/app.py.
"""
from __future__ import annotations

import io
import zipfile

import pytest
from starlette.testclient import TestClient

from agent.files import create_app
from agent.skills import library

SKILL_MD = "---\nname: reconcile\ndescription: Reconcile two spreadsheets\n---\nCompare them.\n"


@pytest.fixture
def skills_dir(tmp_path, monkeypatch):
    path = tmp_path / "skills"
    monkeypatch.setenv("SKILL_LIBRARY_DIR", str(path))
    return path


@pytest.fixture
def client(skills_dir):
    return TestClient(create_app())


def make_zip(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


def install(client, data: bytes):
    return client.post("/skills", files={"file": ("skill.zip", data, "application/zip")})


def error(res) -> str:
    return res.json()["error"]


# --- install + list ---------------------------------------------------------


def test_an_empty_library_lists_nothing(client):
    res = client.get("/skills")
    assert res.status_code == 200
    assert res.json() == []


def test_a_valid_skill_installs_and_is_listed(client, skills_dir):
    res = install(client, make_zip({"SKILL.md": SKILL_MD, "ui/index.html": "<p>hi</p>", "rules.md": "r"}))

    assert res.status_code == 201
    assert res.json() == {"name": "reconcile", "description": "Reconcile two spreadsheets", "hasUi": True}
    assert (skills_dir / "reconcile" / "ui" / "index.html").read_text() == "<p>hi</p>"
    assert (skills_dir / "reconcile" / "rules.md").read_text() == "r"
    assert client.get("/skills").json() == [res.json()]
    assert client.get("/skills/reconcile").json() == res.json()


def test_a_skill_without_ui_is_accepted(client):
    res = install(client, make_zip({"SKILL.md": SKILL_MD}))
    assert res.status_code == 201
    assert res.json()["hasUi"] is False


def test_a_zip_of_a_folder_with_finder_junk_installs(client, skills_dir):
    res = install(
        client,
        make_zip({"reconcile/SKILL.md": SKILL_MD, "reconcile/ref.pdf": "x", "__MACOSX/._SKILL.md": "junk", ".DS_Store": "j"}),
    )
    assert res.status_code == 201
    assert (skills_dir / "reconcile" / "SKILL.md").is_file()
    assert (skills_dir / "reconcile" / "ref.pdf").is_file()
    assert not (skills_dir / "reconcile" / "reconcile").exists()


def test_the_list_is_sorted_by_name(client):
    install(client, make_zip({"SKILL.md": SKILL_MD.replace("reconcile", "zeta")}))
    install(client, make_zip({"SKILL.md": SKILL_MD.replace("reconcile", "alpha")}))
    assert [s["name"] for s in client.get("/skills").json()] == ["alpha", "zeta"]


def test_an_unknown_skill_is_404(client):
    assert client.get("/skills/nope").status_code == 404
    assert client.get("/skills/..").status_code == 404


def test_the_list_ignores_non_skill_folders(client, skills_dir):
    (skills_dir / "stray").mkdir(parents=True)
    (skills_dir / ".installing-x").mkdir()
    assert client.get("/skills").json() == []


# --- each validation failure names its rule and writes nothing --------------


def refused(client, skills_dir, data: bytes, status: int, *fragments: str):
    res = install(client, data)
    assert res.status_code == status
    for fragment in fragments:
        assert fragment in error(res)
    assert not skills_dir.exists() or list(skills_dir.iterdir()) == []


def test_not_a_zip(client, skills_dir):
    refused(client, skills_dir, b"definitely not a zip", 422, "not a valid zip")


def test_skill_md_missing(client, skills_dir):
    refused(client, skills_dir, make_zip({"README.md": "x"}), 422, "SKILL.md is missing")


@pytest.mark.parametrize(
    "skill_md",
    [
        "no frontmatter at all",
        "---\nname: [unclosed\n---\n",
        "---\ndescription: only a description\n---\n",
        "---\nname: only-a-name\n---\n",
        "---\nname: 42\ndescription: not a string name\n---\n",
        "---\nname: Bad Name!\ndescription: spaces and caps\n---\n",
        "---\nname: ../escape\ndescription: path in name\n---\n",
    ],
)
def test_frontmatter_missing_or_invalid(client, skills_dir, skill_md):
    refused(client, skills_dir, make_zip({"SKILL.md": skill_md}), 422, "frontmatter")


def test_ui_folder_without_its_entry(client, skills_dir):
    refused(client, skills_dir, make_zip({"SKILL.md": SKILL_MD, "ui/app.js": "x"}), 422, "ui/index.html")


def test_reference_file_of_an_unsupported_type(client, skills_dir):
    refused(client, skills_dir, make_zip({"SKILL.md": SKILL_MD, "logo.png": "x"}), 422, "logo.png", "pdf")


def test_ui_assets_may_be_any_type(client):
    res = install(client, make_zip({"SKILL.md": SKILL_MD, "ui/index.html": "x", "ui/logo.png": "x", "ui/a.js": "x"}))
    assert res.status_code == 201


@pytest.mark.parametrize("path", ["../evil.md", "ui/../../evil.md", "/abs/evil.md", "C:/evil.md", "a\\..\\evil.md"])
def test_unsafe_paths_are_refused_and_nothing_escapes(client, skills_dir, tmp_path, path):
    refused(client, skills_dir, make_zip({"SKILL.md": SKILL_MD, path: "x"}), 422, "Unsafe path")
    assert not (tmp_path / "evil.md").exists()


def test_symlinks_are_refused(client, skills_dir):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SKILL.md", SKILL_MD)
        link = zipfile.ZipInfo("link.md")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        z.writestr(link, "/etc/passwd")
    refused(client, skills_dir, buf.getvalue(), 422, "Unsafe path")


def test_oversized_zips_are_refused(client, skills_dir, monkeypatch):
    monkeypatch.setattr(library, "MAX_SIZE", 1000)
    refused(client, skills_dir, make_zip({"SKILL.md": SKILL_MD, "big.txt": "a" * 5000}), 413, "too large")


def test_name_clash_is_refused_and_leaves_the_original(client, skills_dir):
    install(client, make_zip({"SKILL.md": SKILL_MD, "keep.md": "original"}))

    res = install(client, make_zip({"SKILL.md": SKILL_MD, "other.md": "new"}))

    assert res.status_code == 409
    assert "already exists" in error(res)
    assert (skills_dir / "reconcile" / "keep.md").read_text() == "original"
    assert not (skills_dir / "reconcile" / "other.md").exists()
    assert [p.name for p in skills_dir.iterdir()] == ["reconcile"]


def test_a_missing_upload_is_refused(client):
    assert client.post("/skills").status_code == 422


# --- malformed zips are refused with a message, never a 500 -----------------


def test_a_corrupt_entry_is_refused_and_writes_nothing(client, skills_dir):
    data = make_zip({"SKILL.md": SKILL_MD, "ref.md": "unmistakable-content"})
    data = data.replace(b"unmistakable-content", b"unmistakable-contenX")  # CRC no longer matches
    refused(client, skills_dir, data, 422, "corrupt")


def test_conflicting_paths_are_refused_and_write_nothing(client, skills_dir):
    refused(client, skills_dir, make_zip({"SKILL.md": SKILL_MD, "a.md": "x", "a.md/b.md": "y"}), 422, "conflict")


# --- replace ----------------------------------------------------------------


def replace(client, name: str, data: bytes):
    return client.put(f"/skills/{name}", files={"file": ("skill.zip", data, "application/zip")})


def test_replace_swaps_in_the_new_version(client, skills_dir):
    install(client, make_zip({"SKILL.md": SKILL_MD, "old.md": "old"}))

    res = replace(client, "reconcile", make_zip({"SKILL.md": SKILL_MD.replace("two spreadsheets", "N sheets"), "new.md": "new"}))

    assert res.status_code == 200
    assert res.json() == {"name": "reconcile", "description": "Reconcile N sheets", "hasUi": False}
    assert (skills_dir / "reconcile" / "new.md").read_text() == "new"
    assert not (skills_dir / "reconcile" / "old.md").exists()
    assert [p.name for p in skills_dir.iterdir()] == ["reconcile"]


def test_an_invalid_replacement_leaves_the_old_skill_untouched(client, skills_dir):
    install(client, make_zip({"SKILL.md": SKILL_MD, "keep.md": "original"}))

    res = replace(client, "reconcile", make_zip({"SKILL.md": "no frontmatter", "other.md": "new"}))

    assert res.status_code == 422
    assert "frontmatter" in error(res)
    assert (skills_dir / "reconcile" / "keep.md").read_text() == "original"
    assert [p.name for p in skills_dir.iterdir()] == ["reconcile"]


def test_a_corrupt_replacement_leaves_the_old_skill_untouched(client, skills_dir):
    install(client, make_zip({"SKILL.md": SKILL_MD, "keep.md": "original"}))
    data = make_zip({"SKILL.md": SKILL_MD, "ref.md": "unmistakable-content"})

    res = replace(client, "reconcile", data.replace(b"unmistakable-content", b"unmistakable-contenX"))

    assert res.status_code == 422
    assert (skills_dir / "reconcile" / "keep.md").read_text() == "original"
    assert [p.name for p in skills_dir.iterdir()] == ["reconcile"]


def test_replace_refuses_a_zip_for_a_different_skill(client, skills_dir):
    install(client, make_zip({"SKILL.md": SKILL_MD, "keep.md": "original"}))

    res = replace(client, "reconcile", make_zip({"SKILL.md": SKILL_MD.replace("reconcile", "other")}))

    assert res.status_code == 422
    assert "other" in error(res) and "reconcile" in error(res)
    assert [p.name for p in skills_dir.iterdir()] == ["reconcile"]
    assert (skills_dir / "reconcile" / "keep.md").read_text() == "original"


def test_replace_of_an_unknown_skill_is_404_and_installs_nothing(client, skills_dir):
    res = replace(client, "reconcile", make_zip({"SKILL.md": SKILL_MD}))
    assert res.status_code == 404
    assert not skills_dir.exists() or list(skills_dir.iterdir()) == []


# --- delete -----------------------------------------------------------------


def test_delete_removes_the_skill_from_the_library_and_the_list(client, skills_dir):
    install(client, make_zip({"SKILL.md": SKILL_MD, "ui/index.html": "x"}))

    assert client.delete("/skills/reconcile").status_code == 204

    assert client.get("/skills").json() == []
    assert client.get("/skills/reconcile").status_code == 404
    assert list(skills_dir.iterdir()) == []


def test_delete_of_an_unknown_skill_is_404(client):
    assert client.delete("/skills/nope").status_code == 404
    assert client.delete("/skills/..").status_code == 404


def test_a_deleted_name_can_be_installed_again(client):
    install(client, make_zip({"SKILL.md": SKILL_MD}))
    client.delete("/skills/reconcile")
    assert install(client, make_zip({"SKILL.md": SKILL_MD})).status_code == 201


def test_a_skill_with_a_broken_skill_md_can_still_be_deleted(client, skills_dir):
    (skills_dir / "reconcile").mkdir(parents=True)
    (skills_dir / "reconcile" / "SKILL.md").write_text("not frontmatter")

    assert client.delete("/skills/reconcile").status_code == 204
    assert install(client, make_zip({"SKILL.md": SKILL_MD})).status_code == 201


def test_a_failed_swap_restores_the_old_skill(client, skills_dir, monkeypatch):
    install(client, make_zip({"SKILL.md": SKILL_MD, "keep.md": "original"}))
    real_rename = library.Path.rename

    def flaky(self, target):
        if self.name.startswith(".installing-"):
            raise OSError("disk says no")
        return real_rename(self, target)

    monkeypatch.setattr(library.Path, "rename", flaky)
    with pytest.raises(OSError):
        replace(client, "reconcile", make_zip({"SKILL.md": SKILL_MD, "new.md": "new"}))
    monkeypatch.undo()

    assert (skills_dir / "reconcile" / "keep.md").read_text() == "original"
    assert [p.name for p in skills_dir.iterdir()] == ["reconcile"]
