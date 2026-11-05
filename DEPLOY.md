# Deploying the pipeline

Two supported targets. The **Proxmox VM** is the recommended one: the build
service and its tunnel live inside a throwaway VM, so your NAS and the rest
of the LAN are never part of the attack surface, and you can snapshot the
VM before any change. The TrueNAS option is kept for completeness.

## Option A — Proxmox VM (recommended)

### 1. Create the VM

On Proxmox, create a VM (not a container, for the strongest isolation):

- Ubuntu Server 22.04 LTS (the `ubuntu-22.04.5-live-server-amd64.iso`
  works as-is) or Debian 13 netinst — either is fine, because FreeCAD,
  Python, and the MCP server all run inside the container image; the VM
  only provides Docker
- 2 vCPU, 4 GB RAM, 20+ GB disk
- No special roles; a basic server install with SSH

Take a Proxmox snapshot once the base install works — you can always roll
back to it.

### 2. Install Docker

```sh
curl -fsSL https://get.docker.com | sh
```

### 3. Run the stack

```sh
git clone https://github.com/emilsommerfeldt/freecad-pipeline.git
cd freecad-pipeline
mkdir -p models
sed -i "s/CHANGE_ME/$(openssl rand -hex 32)/" compose.yaml
docker compose up -d
docker compose logs freecad-mcp | head
```

Keep the generated token — you need it for the connector.

### 4. Expose the MCP endpoint through Tailscale

Install Tailscale **inside the VM** (not on the Proxmox host, so the tunnel
terminates inside the sandbox):

```sh
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up
tailscale funnel http://localhost:8410
```

Tailscale prints a public HTTPS URL like `https://freecad-vm-xxxx.ts.net/`.
Your MCP endpoint is `https://<that-host>/mcp`.

Funnel publishes **only** the proxied port 8410 — nothing else in the VM or
on your LAN is reachable through it — and every request still needs the
bearer token. Never port-forward 8410 on a router instead.

(Alternative: run `cloudflared` in the VM and put a Cloudflare Access
policy in front of the hostname for identity auth in addition to the
token. The server side is identical.)

### 5. Register the connector in Vibe Work

In the Mistral Studio, add a **custom MCP connector**:

- Server URL: `https://<tunnel-host>/mcp`
- Authentication: Bearer token, value = the `FREECAD_MCP_TOKEN` you
  generated (or the header `Authorization: Bearer <token>` if configured
  manually)

Once connected, sessions expose four tools:

- `build_part(spec)` — builds from a spec and returns the verification table
  (all checks, STL sha256, exit code)
- `list_parts()` — parts and artifacts currently in `/models`
- `get_stl(name)` — the binary STL, base64-encoded (for 3D preview)
- `get_verification(name)` — the stored verification report for a part

### 6. Day-to-day use

Local builds still work over SSH:

```sh
ssh <vm> "docker exec freecad-builder freecadcmd /models/build_part.py"
```

(remote builds go through the MCP connector instead). Expected result for
the committed L-bracket spec:

- `models/l-bracket.FCStd`
- `models/l-bracket.stl` (binary, watertight)
- `models/l-bracket.verification.json`
- a JSON verification table on stdout (between `VERIFICATION_JSON_BEGIN` /
  `VERIFICATION_JSON_END` markers, since freecadcmd also prints a banner
  and progress output) with `all_pass: true`
  (bbox 60 x 40 x 3 mm, volume 873 mm^3, solid, closed, watertight)

Any failed check exits non-zero. To build a new part, replace `spec.json` in
the models directory (or send it as the `spec` argument of `build_part`);
outputs are named after the spec's `name`.

## Option B — TrueNAS SCALE 24.10+

1. Datasets > Add Dataset: name `freecad` under your apps dataset with the
   **Apps** preset (apps user 568), then create the models directory:

   ```sh
   midclt call filesystem.mkdir /mnt/<pool>/apps/freecad/models '{"options": {"permissions": "0777"}}'
   ```

2. Apps > Discover > **Install via YAML**, using `compose.yaml` with
   `./models:/models` replaced by
   `/mnt/<pool>/apps/freecad/models:/models` and `CHANGE_ME` replaced by
   your generated token.

   The `freecad-builder` service has no listeners (builds run via
   `docker exec` and seed `/models` on first start); `freecad-mcp` binds to
   `127.0.0.1:8410` on the NAS only.

3. If you expose the MCP endpoint from the NAS, do it through Tailscale
   Funnel or a Cloudflare Tunnel as in Option A — never a raw published
   port. The VM option above keeps the tunnel off the NAS entirely, which
   is why it is recommended.
