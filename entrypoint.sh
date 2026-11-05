#!/bin/sh
# Seed /models with the build script and sample spec on first start
# (never overwrite: the dataset may hold newer specs from Vibe Work),
# then keep the container alive. Builds run via `docker exec`.
set -e

for f in build_part.py spec.json; do
    if [ ! -e "/models/${f}" ]; then
        cp "/opt/freecad-pipeline/${f}" "/models/${f}"
    fi
done

exec sleep infinity
