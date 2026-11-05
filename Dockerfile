# Headless FreeCAD build container.
# Debian 13 (trixie) base with FreeCAD 1.1.4 (official AppImage, extracted),
# freecadcmd on PATH, non-root user, /models as the working volume.
# Long-running via `sleep infinity`; builds are invoked with `docker exec`.
# No ports are published anywhere (image, Compose, or CI).

FROM debian:trixie-slim

ARG FREECAD_VERSION=1.1.4
ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive
ENV FREECAD_HOME=/opt/freecad/usr

RUN set -eux; \
    case "${TARGETARCH}" in \
        amd64) FREECAD_ARCH=x86_64 ;; \
        arm64) FREECAD_ARCH=aarch64 ;; \
        *) echo "unsupported TARGETARCH: ${TARGETARCH}"; exit 1 ;; \
    esac; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl xz-utils libfuse2 \
        libgl1 libglu1-mesa libxkbcommon0 libxi6 libxrender1 \
        libsm6 libice6 libfontconfig1 libdbus-1-3 libxext6; \
    appimage="FreeCAD_${FREECAD_VERSION}-Linux-${FREECAD_ARCH}-py311.AppImage"; \
    curl -fsSL -o "/tmp/${appimage}" \
        "https://github.com/FreeCAD/FreeCAD/releases/download/${FREECAD_VERSION}/${appimage}"; \
    chmod +x "/tmp/${appimage}"; \
    "/tmp/${appimage}" --appimage-extract >/dev/null; \
    mv squashfs-root /opt/freecad; \
    rm -f "/tmp/${appimage}"; \
    apt-get purge -y libfuse2; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*

# freecadcmd wrapper on PATH pointing into the extracted AppImage.
RUN printf '#!/bin/sh\nexec /opt/freecad/usr/bin/freecadcmd "$@"\n' \
        > /usr/local/bin/freecadcmd \
    && chmod 0755 /usr/local/bin/freecadcmd

# Non-root user; /models is the working volume for specs and outputs.
RUN useradd --create-home --uid 1000 --shell /bin/bash builder \
    && mkdir -p /models /opt/freecad-pipeline \
    && chown builder:builder /models /opt/freecad-pipeline

COPY --chown=builder:builder build_part.py spec.json /opt/freecad-pipeline/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

USER builder
WORKDIR /models

VOLUME ["/models"]
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
