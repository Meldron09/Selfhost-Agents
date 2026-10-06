"""Throwaway: the Skill-UI static route (shape of the real one, ADR-0010).
Plain `def` handler + mimetypes, like agent/files/app.py. CORS header is
selected by URL variant so verify.mjs can run negative controls."""
import mimetypes
from pathlib import Path
from fastapi import FastAPI
from starlette.responses import Response

ROOT = Path(__file__).parent / "skill-ui" / "dist"
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

CSP = "default-src 'self'; connect-src 'none'; form-action 'none'; img-src 'self' data:"

def serve(path: str, variant: str) -> Response:
    target = (ROOT / (path or "index.html")).resolve()
    if ROOT.resolve() not in target.parents or not target.is_file():
        return Response(status_code=404)
    # variants: "demo" = ACAO + CSP (proposed); "nocors" = neither; "nocsp" = ACAO only
    headers = {}
    if variant != "nocors":
        headers["Access-Control-Allow-Origin"] = "*"
    if variant == "demo":
        headers["Content-Security-Policy"] = CSP
    return Response(target.read_bytes(), media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream", headers=headers)

app.add_api_route("/skills/{variant}/ui/{path:path}", serve, methods=["GET"])
