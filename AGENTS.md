# FreeCAD pipeline — brief for a Vibe Code session

Save this file as `AGENTS.md` in the root of the repository you create in Vibe Code. Vibe Code reads `AGENTS.md` automatically and this brief is self-contained; the design conversation that produced it lives in a Vibe Work session that Code cannot read.

## 1. What this project is

A paper-to-print pipeline for 3D-printed parts: the user sketches a part on paper with a fixed notation, a Vibe Work session converts the photograph into a structured part specification, and that specification is built into a fully constrained FreeCAD model, exported as FCStd and STL, and verified against the annotated dimensions. This repository builds the server-side infrastructure that runs on the user's TrueNAS SCALE NAS (Docker Engine on Linux, version 24.10 or later).

Scope split: Vibe Work owns sketch interpretation, verification review, and 3D preview. Vibe Code owns this repository: the container image, the CI, the Compose deployment, and the build script. Builds are triggered over SSH from the desktop (`ssh <nas> "sudo docker exec freecad-builder freecadcmd /models/build_part.py"`); the script prints a verification table that is read back and checked against the paper sketch.

## 2. What already exists (do not rebuild)

- Desktop: FreeCAD with the spkane Robust MCP Bridge add-on, driven interactively from Vibe Code over a local stdio MCP server.
- The notation and verification contract: all dimensions in millimetres with a decimal point (never a decimal comma), Z is the extrusion axis, the datum is the bottom-left corner of the FRONT view, each dimension is annotated exactly once, and every build must verify its actual constraint values against the specification or exit non-zero.
- A demo L-bracket proved the STL preview pattern in Vibe Work.

## 3. Deliverables

### 3.1 Headless FreeCAD build container

- `Dockerfile`: Debian 13 (trixie) base, FreeCAD installed (1.0.2 minimum, 1.1.4 or newer recommended), `freecadcmd` available on PATH, non-root user, `/models` as the working volume.
- The container is long-running (`sleep infinity` entrypoint): builds are invoked with `docker exec`, so the container needs no network listeners at all. Publish no ports.
- `build_part.py` is executed with `freecadcmd /models/build_part.py`, so no `sys.path` boilerplate is needed; however, if PartDesign objects load as generic `GeoFeature` types, append the FreeCAD `Mod/` paths to `sys.path` in the script (known headless caveat, documented on the FreeCAD wiki page "Headless FreeCAD").

### 3.2 CI

- GitHub Actions workflow: build and push the image to GHCR for linux/amd64 and linux/arm64, tagged `latest` plus the Git SHA. The user's GitHub account is connected, so the workflow should be ready to run on push.

### 3.3 TrueNAS deployment

- `compose.yaml` written for TrueNAS SCALE 24.10+ "Apps > Discover > Install via YAML": image from GHCR, `restart: unless-stopped`, one host-path volume `/mnt/<pool>/apps/freecad/models` mapped to `/models`, no ports section.
- A short `DEPLOY.md`: create the dataset with the Apps preset (apps user 568), paste the YAML, then run a sample build from the TrueNAS shell.

### 3.4 The build script contract

- `build_part.py` reads `spec.json` (committed next to it) and builds exactly that part. The sample spec is the L-bracket: L profile with a 60 x 3 mm foot strip and a 40 mm high wall (3 mm wide), extruded 3 mm; bounding box 60 x 40 x 3 mm; expected volume 873 mm^3; all sketch constraints explicit, sketch fully constrained, PartDesign Body + Pad.
- The script constructs the sketch with the Sketcher API with dimensional constraints (not baked coordinates), pads it, recomputes, and then verifies: `Shape.isValid()`, `isClosed()`, the bounding box, the volume, and every named constraint value against the spec.
- Outputs: `/models/<name>.FCStd`, `/models/<name>.stl` (binary), `/models/<name>.verification.json`, and the same verification table printed to stdout as JSON. Any mismatch or failed check exits non-zero.
- `spec.json` schema: part name, datum note, profile description, constraints, thickness, expected checks. Keep it human-readable, since Vibe Work writes it.

## 4. Constraints and security

- LAN only. No published ports, no reverse proxy, nothing reachable from the internet.
- The MCP bridge stays on the desktop host. It is loopback-only and pairing-gated; ports 9875/9876 must never be exposed, and the spkane container image expects FreeCAD on the host and does not work with Docker Engine on Linux. Do not attempt a containerized or remote bridge; that path is deferred until upstream ships protected remote transport.
- No secrets in the repository. The container runs as a non-root user.

## 5. Acceptance criteria

1. `docker build` succeeds locally and CI publishes to GHCR.
2. The Compose file deploys on TrueNAS via Install via YAML.
3. `docker exec freecad-builder freecadcmd /models/build_part.py` produces the FCStd, the binary STL, and an all-green verification table for the L-bracket: bbox 60 x 40 x 3 mm, volume 873 mm^3, solid and closed.
4. The STL is watertight (verify with the FreeCAD Mesh module or an equivalent checker in the script).
5. Re-running the build is bit-for-bit reproducible from the same spec file.

## 6. Revision: remote MCP access (supersedes part of section 4)

The user later required remote access from Vibe Work while away from the LAN.
The pipeline now ships `mcp_server.py` (Streamable HTTP MCP, bearer-token
auth, tools: `build_part`, `list_parts`, `get_stl`, `get_verification`),
run by a second compose service `freecad-mcp` from the same image. The
`freecad-builder` container itself remains listener-free. The MCP service
publishes only to the NAS loopback (`127.0.0.1:8410`) and is reachable from
outside the LAN solely through an encrypted, access-controlled tunnel
(Tailscale Funnel or Cloudflare Tunnel) plus the token — never a raw
published port or router port-forward. The MCP bridge restriction in section 4
(spkane bridge, ports 9875/9876) still applies unchanged.
