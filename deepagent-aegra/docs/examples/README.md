# Sample Skills for testing

Ready-to-upload zips are in `zips/`; sample files to feed them are in `inputs/`.
Rebuild after editing a folder: `.venv/bin/python docs/examples/build-zips.py` (from `deepagent-aegra/`).

| Zip | Tests | Run it with |
| --- | --- | --- |
| `hello-note` | built-in screen (no `ui/`) | `inputs/notes.txt` + any text |
| `summarise-text` | custom UI, one file slot + a field, writes an Output | `inputs/notes.txt`, style `bullets` |
| `two-file-skill` (`two-file-reconcile`) | custom UI, two labelled file slots | `inputs/ledger.xlsx` + `inputs/bank.xlsx` (3 deliberate mismatches) |
| `style-guide-rewrite` | bundled reference file read by the agent | `inputs/meeting.md` |
| `slow-multi-step` | queue, Cancel and History: submit 2-3 Runs quickly | `inputs/notes.txt` |
| `hello-note-v2` | Replace `hello-note`, then check the Run marked "updated" in History (replies in ALL CAPS) | |

Also try: upload `inputs/photo.png` to any Skill (refused as an unsupported type), delete a Skill that has Runs (shows under Removed Skills), upload `hello-note` twice (409 already exists).

`zips/refused/` must each be rejected on install, with the rule named: `no-skill-md`, `missing-description`, `bad-name`, `ui-without-index`, `stray-png`, `path-traversal`, `not-a-zip`. `replace-wrong-name` is a valid Skill: it installs, but Replace on `hello-note` refuses it ("not 'hello-note'").
