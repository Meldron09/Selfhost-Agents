"""Rebuild the sample zips in zips/ (and the xlsx inputs) from the folders next to this file.
Run from the repo with the project venv: `.venv/bin/python docs/examples/build-zips.py`."""
import zipfile
from pathlib import Path

here = Path(__file__).parent
out = here / "zips"
bad = out / "refused"
bad.mkdir(parents=True, exist_ok=True)

# Every folder with a SKILL.md becomes <folder>.zip, nested under its folder like a Finder "Compress".
for skill in sorted(here.glob("*/SKILL.md")):
    folder = skill.parent
    with zipfile.ZipFile(out / f"{folder.name}.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(folder.rglob("*")):
            if f.is_file():
                z.write(f, f"{folder.name}/{f.relative_to(folder)}")

OK = "---\nname: {}\ndescription: A sample\n---\nDo nothing.\n"
refused = {
    "no-skill-md": {"notes.md": "no SKILL.md here"},
    "missing-description": {"SKILL.md": "---\nname: no-desc\n---\nx\n"},
    "bad-name": {"SKILL.md": "---\nname: Bad_Name\ndescription: x\n---\nx\n"},
    "ui-without-index": {"SKILL.md": OK.format("ui-no-index"), "ui/app.js": "//"},
    "stray-png": {"SKILL.md": OK.format("stray-png"), "logo.png": "x"},
    "path-traversal": {"SKILL.md": OK.format("traversal"), "../evil.md": "x"},
    # a valid Skill: it installs fine, and is refused only as a Replace of hello-note
    "replace-wrong-name": {"SKILL.md": OK.format("some-other-skill")},
}
for name, files in refused.items():
    with zipfile.ZipFile(bad / f"{name}.zip", "w") as z:
        for path, text in files.items():
            z.writestr(path, text)
(bad / "not-a-zip.zip").write_text("this is plain text, not a zip\n")

try:
    from openpyxl import Workbook
except ImportError:
    print("openpyxl missing: skipped the xlsx inputs")
else:
    for fname, rows in {
        "ledger.xlsx": [("2026-01-05", "Acme", 120.0), ("2026-01-09", "Globex", 80.5), ("2026-02-01", "Initech", 300.0)],
        "bank.xlsx": [("2026-01-05", "Acme", 120.0), ("2026-01-09", "Globex", 85.5), ("2026-02-14", "Umbrella", 42.0)],
    }.items():
        wb = Workbook()
        wb.active.append(["Date", "Counterparty", "Amount"])
        for r in rows:
            wb.active.append(r)
        wb.save(here / "inputs" / fname)
