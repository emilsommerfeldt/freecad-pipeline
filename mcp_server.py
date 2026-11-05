"""MCP server exposing the FreeCAD build pipeline over Streamable HTTP.

Runs in the freecad-mcp compose service (same image as the builder) with the
AppImage Python. Every request must carry `Authorization: Bearer <token>`
(FREECAD_MCP_TOKEN). Bind to localhost or a private interface; the only
intended exposure is an encrypted, access-controlled tunnel (Tailscale Funnel
or Cloudflare Tunnel) — never a raw published port.

Tools:
- build_part(spec): write the spec, run freecadcmd, return the verification
  table (JSON) with all checks and the STL sha256.
- list_parts(): built parts in /models.
- get_stl(name): binary STL as base64 (for 3D preview artifacts).
- get_verification(name): the stored verification JSON for a part.
"""

import base64
import hmac
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.environ.get("PYDEPS", "/opt/pydeps"))

from fastmcp import FastMCP  # noqa: E402

MODELS_DIR = os.environ.get("MODELS_DIR", "/models")
BUILD_SCRIPT = os.environ.get("BUILD_SCRIPT", os.path.join(MODELS_DIR, "build_part.py"))
FREECADCMD = os.environ.get("FREECADCMD", "/opt/freecad/usr/bin/freecadcmd")
HOST = os.environ.get("FREECAD_MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("FREECAD_MCP_PORT", "8410"))
TOKEN = os.environ.get("FREECAD_MCP_TOKEN", "")
BUILD_TIMEOUT = int(os.environ.get("BUILD_TIMEOUT", "600"))
JSON_MARKER_RE = re.compile(
    r"VERIFICATION_JSON_BEGIN\n(.*?)\nVERIFICATION_JSON_END", re.S
)

mcp = FastMCP("freecad-pipeline")


def _safe_name(name):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError("invalid part name: %r" % name)
    return name


def _run_build():
    proc = subprocess.run(
        [FREECADCMD, BUILD_SCRIPT],
        cwd=MODELS_DIR,
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT,
    )
    stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
    report = None
    match = JSON_MARKER_RE.search(stdout)
    if match:
        try:
            report = json.loads(match.group(1))
        except ValueError:
            pass
    return code, report, stdout, stderr


@mcp.tool
def build_part(spec):
    """Build a part from a spec (the spec.json object, or its JSON string).

    Writes the spec to /models/spec.json, runs the headless FreeCAD build,
    and returns the verification report: `all_pass`, per-check rows
    (bounding box, volume, named constraints, watertight STL), artifact
    paths, and the STL sha256. A failed build returns `all_pass: false`
    (or `error`) with the exit code and stderr tail.
    """
    if isinstance(spec, str):
        spec = json.loads(spec)
    if not isinstance(spec, dict) or "name" not in spec:
        raise ValueError("spec must be an object with at least a 'name'")
    _safe_name(str(spec["name"]))

    spec_path = os.path.join(MODELS_DIR, "spec.json")
    with open(spec_path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2)
        fh.write("\n")

    try:
        code, report, stdout, stderr = _run_build()
    except subprocess.TimeoutExpired:
        return {"error": "build timed out after %ds" % BUILD_TIMEOUT}

    if report is not None:
        report["exit_code"] = code
        return report
    return {
        "error": "build failed or produced no verification output",
        "exit_code": code,
        "stderr_tail": stderr[-2000:],
        "stdout_tail": stdout[-2000:],
    }


@mcp.tool
def list_parts():
    """List built parts in /models with their artifact sizes and mtimes."""
    suffixes = (".FCStd", ".stl", ".verification.json")
    parts = {}
    for entry in sorted(os.listdir(MODELS_DIR)):
        full = os.path.join(MODELS_DIR, entry)
        if not os.path.isfile(full):
            continue
        for suffix in suffixes:
            if entry.endswith(suffix):
                st = os.stat(full)
                parts.setdefault(entry[: -len(suffix)], {})[suffix] = {
                    "bytes": st.st_size,
                    "mtime": st.st_mtime,
                }
    return parts


@mcp.tool
def get_stl(name):
    """Return the binary STL of a built part, base64-encoded."""
    name = _safe_name(name)
    path = os.path.join(MODELS_DIR, name + ".stl")
    if not os.path.isfile(path):
        raise ValueError("no STL for part %r (build it first)" % name)
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


@mcp.tool
def get_verification(name):
    """Return the stored verification report for a built part."""
    name = _safe_name(name)
    path = os.path.join(MODELS_DIR, name + ".verification.json")
    if not os.path.isfile(path):
        raise ValueError("no verification report for part %r" % name)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class BearerAuthMiddleware:
    def __init__(self, app, token):
        self.app = app
        self.token = token.encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        supplied = headers.get("authorization", "")
        expected = "Bearer " + TOKEN
        ok = hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
        if not ok:
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"www-authenticate", b"Bearer"), (b"content-length", b"0")],
            })
            await send({"type": "http.response.body", "body": b""})
            return
        await self.app(scope, receive, send)


def main():
    if not TOKEN:
        sys.stderr.write("FREECAD_MCP_TOKEN must be set\n")
        sys.exit(2)
    if not os.path.isfile(BUILD_SCRIPT):
        sys.stderr.write("build script not found at %s\n" % BUILD_SCRIPT)
        sys.exit(2)
    import uvicorn

    app = mcp.http_app(path="/mcp")
    uvicorn.run(BearerAuthMiddleware(app, TOKEN), host=HOST, port=PORT, log_level="info")


main()
