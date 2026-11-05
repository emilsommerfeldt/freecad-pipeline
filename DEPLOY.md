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
(replacing `<pool>` with your pool name), and click Install. The container:

- pulls the image from GHCR,
- runs as non-root,
- publishes **no ports** (builds run via `docker exec`),
- seeds `/models` with `build_part.py` and `spec.json` on first start
  (existing files are never overwritten).

## 3. Run a sample build

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

## 4. Building a new part

Replace `spec.json` in the models dataset with the new spec written by Vibe
Work, then re-run the same `docker exec` command. If the spec name changes,
the outputs are named after it.
