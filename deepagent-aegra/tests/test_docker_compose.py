"""`docker-compose.yml` must actually point `FILE_STORE_DIR` at the dedicated
volume's mount path — regression coverage for a bug a code review caught:
`agent/config.py` resolves a relative `FILE_STORE_DIR` against this
process's cwd, which inside the `aegra-host` container is `/app/aegra-host`
(the Dockerfile's final `WORKDIR`), never `/app` — so a relative value there
would silently miss the volume declared in
docs/adr/0002-dedicated-volume-for-file-store.md entirely.

A plain string/regex check, not a YAML parse: no dependency on `pyyaml`
(not a declared requirement here, only an incidental transitive one).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_MOUNT = re.compile(r"deepagent-aegra-files:(/\S+)")
_FILE_STORE_DIR_ENV = re.compile(r"FILE_STORE_DIR:\s*(\S+)")


def _read() -> str:
    return (REPO_ROOT / "docker-compose.yml").read_text()


def test_file_store_dir_is_set_and_absolute():
    compose = _read()
    match = _FILE_STORE_DIR_ENV.search(compose)
    assert match, "docker-compose.yml must set FILE_STORE_DIR explicitly"
    value = match.group(1)
    assert value.startswith("/"), (
        f"FILE_STORE_DIR={value!r} is not absolute — a relative value resolves "
        "against the container's cwd (/app/aegra-host), not /app"
    )


def test_file_store_dir_matches_the_dedicated_volumes_mount_path():
    compose = _read()
    env_value = _FILE_STORE_DIR_ENV.search(compose).group(1)
    mount_path = _MOUNT.search(compose).group(1)
    assert env_value == mount_path, (
        f"FILE_STORE_DIR ({env_value!r}) must match where the "
        f"deepagent-aegra-files volume is actually mounted ({mount_path!r})"
    )
