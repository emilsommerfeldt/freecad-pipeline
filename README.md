# FreeCAD pipeline

Server-side infrastructure for a paper-to-print 3D-printing pipeline: a
headless FreeCAD build container that turns a structured part specification
(`spec.json`) into a fully constrained, verified FreeCAD model (FCStd + binary
STL). Runs on TrueNAS SCALE 24.10+ via Docker Engine; builds are triggered
over SSH with `docker exec`. No ports are published anywhere.

See `AGENTS.md` for the full project brief and `DEPLOY.md` for TrueNAS
installation.

## Layout

| Path | Purpose |
| --- | --- |
| `Dockerfile` | Debian 13 (trixie), FreeCAD 1.1.4 (official AppImage, extracted), `freecadcmd` on PATH, non-root user |
| `entrypoint.sh` | Seeds `/models` with the build script and spec, then `sleep infinity` |
| `build_part.py` | Builds the spec, verifies it, exports FCStd/STL/verification JSON |
| `mcp_server.py` | MCP endpoint (Streamable HTTP + bearer token) wrapping the build CLI for remote Vibe Work sessions |
| `entrypoint.sh` | Seeds `/models` with the build script and spec, then `sleep infinity` |
| `spec.json` | Sample spec: the L-bracket |
| `compose.yaml` | TrueNAS SCALE 24.10+ Install-via-YAML Compose file (builder + MCP services) |
| `.github/workflows/docker-image.yml` | CI: smoke test, then multi-arch (amd64/arm64) push to GHCR |

## Build script contract

`freecadcmd /models/build_part.py`:

- reads `spec.json` next to the script,
- builds the profile with the Sketcher API using explicit named dimensional
  constraints (never baked coordinates) in a PartDesign Body + Pad,
- verifies `Shape.isValid()`, closed shells, bounding box, volume, every
  named constraint value, and STL watertightness against the spec,
- writes `<name>.FCStd`, `<name>.stl` (binary), `<name>.verification.json`,
  and prints the same verification table to stdout as JSON between
  `VERIFICATION_JSON_BEGIN`/`VERIFICATION_JSON_END` markers,
- exits non-zero on any mismatch or failed check,
- produces bit-for-bit identical FCStd/STL outputs for the same spec
  (timestamps inside the FCStd are normalized).

## Remote access (Vibe Work)

The `freecad-mcp` service exposes the pipeline as an MCP server
(Streamable HTTP at `/mcp`, bearer-token auth) so remote Vibe Work sessions
can build parts and fetch STLs. It publishes only to the NAS loopback;
reach it through an encrypted, access-controlled tunnel (Tailscale Funnel or
Cloudflare Tunnel — see `DEPLOY.md`), then register it as a custom MCP
connector in the Studio. Tools: `build_part`, `list_parts`, `get_stl`,
`get_verification`.

## Local test

```sh
docker build -t freecad-builder .
mkdir -p /tmp/models && chmod 777 /tmp/models
docker run -d --name freecad-builder -v /tmp/models:/models freecad-builder
docker exec freecad-builder freecadcmd /models/build_part.py
```
