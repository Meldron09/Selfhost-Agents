"""Upload/download: the HTTP app backing every Attachment/Output Key.

docs/adr/0004-upload-download-http-app-route-contract.md. Not wired into any
server yet — mounting into `aegra` is `aegra-host/http_app_adapter.py`'s job,
configured via `AEGRA_HTTP_APP_TARGET=agent/files/app.py:create_app`.
"""
from agent.files.app import app, create_app
from agent.files.store import load, save

__all__ = [
    "app",
    "create_app",
    "load",
    "save",
]
