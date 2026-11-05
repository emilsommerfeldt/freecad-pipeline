# Deploying on TrueNAS SCALE

## 1. Create the dataset

Datasets > Add Dataset:

- Dataset Name: `freecad` under your apps dataset (e.g. `tank/apps`), so the
  path becomes `/mnt/<pool>/apps/freecad`
- Dataset Preset: **Apps** (this creates subdatasets and sets the `apps` user,
  UID 568)

Then create the models directory:

```sh
midclt call filesystem.mkdir /mnt/<pool>/apps/freecad/models '{"options": {"permissions": "0777"}}'
```

(Or `mkdir` it from the shell and `chmod 777` it so the container's non-root
user `builder` (UID 1000) can write outputs.)

## 2. Install via YAML

Apps > Discover > **Install via YAML**, paste the contents of `compose.yaml`
with:

- `<pool>` replaced with your pool name,
- `CHANGE_ME` replaced with a generated token: `openssl rand -hex 32`.

The stack runs two services from the same image:

- `freecad-builder` — long-running, **no listeners**; builds run via
  `docker exec`. It seeds `/models` with `build_part.py` and `spec.json` on
  first start (existing files are never overwritten).
- `freecad-mcp` — the MCP endpoint for remote sessions. It binds to
  `127.0.0.1:8410` on the NAS only and requires the bearer token on every
  request.

## 3. Local builds (SSH from the desktop)

From the TrueNAS shell (or over SSH from the desktop):

```sh
sudo docker exec freecad-builder freecadcmd /models/build_part.py
```

Expected result for the committed L-bracket spec:

- `/mnt/<pool>/apps/freecad/models/l-bracket.FCStd`
- `/mnt/<pool>/apps/freecad/models/l-bracket.stl` (binary, watertight)
- `/mnt/<pool>/apps/freecad/models/l-bracket.verification.json`
- a JSON verification table on stdout (between `VERIFICATION_JSON_BEGIN` /
  `VERIFICATION_JSON_END` markers, since freecadcmd also prints a banner and
  progress output) with `all_pass: true`
  (bbox 60 x 40 x 3 mm, volume 873 mm^3, solid, closed, watertight)

Any failed check exits non-zero, so SSH-triggered builds fail loudly.

## 4. Remote access: expose the MCP endpoint through a tunnel

The MCP server is only reachable via an encrypted, access-controlled tunnel.
Never publish port 8410 directly or port-forward it on your router.

### Option A — Tailscale Funnel (recommended)

1. Install the Tailscale app from the TrueNAS catalog and connect the NAS.
2. Enable Funnel for the host, then proxy the MCP endpoint:

   ```sh
   docker exec tailscale tailscale funnel --setup 8410
   # or, from a shell inside the tailscale app:
   tailscale funnel http://localhost:8410
   ```

3. Tailscale prints a public HTTPS URL like
   `https://nas-<tailnet>.ts.net/`. Your MCP endpoint is
   `https://<that-host>/mcp`.

Funnel traffic terminates at Tailscale's edge with TLS and reaches your NAS
over the tailnet; the bearer token is still required on every request.

### Option B — Cloudflare Tunnel + Access

1. Install `cloudflared` (community app) and create a tunnel with a hostname
   pointing at `http://localhost:8410`.
2. Optionally put a Cloudflare Access policy (email OTP / SSO) in front of
   the hostname for identity-based auth in addition to the token.
3. Your MCP endpoint is `https://<your-hostname>/mcp`.

## 5. Register the connector in Vibe Work

In the Mistral Studio, add a **custom MCP connector**:

- Server URL: `https://<tunnel-host>/mcp`
- Authentication: Bearer token, value = the `FREECAD_MCP_TOKEN` you generated
  (or the header `Authorization: Bearer <token>` if configured manually)

Once connected, sessions expose four tools:

- `build_part(spec)` — builds from a spec and returns the verification table
  (all checks, STL sha256, exit code)
- `list_parts()` — parts and artifacts currently in `/models`
- `get_stl(name)` — the binary STL, base64-encoded (for 3D preview)
- `get_verification(name)` — the stored verification report for a part

## 6. Building a new part

Replace `spec.json` in the models dataset with the new spec written by Vibe
Work (or send it as the `spec` argument of `build_part`), then re-run the
same `docker exec` command, or call the `build_part` MCP tool. If the spec
name changes, the outputs are named after it.
