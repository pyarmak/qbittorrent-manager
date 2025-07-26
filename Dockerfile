FROM ghcr.io/linuxserver/unrar:latest AS unrar

FROM python:3.13-alpine3.21 AS python_base

FROM ghcr.io/linuxserver/baseimage-alpine:edge

# environment settings
ENV HOME="/config" \
XDG_CONFIG_HOME="/config" \
XDG_DATA_HOME="/config"

# Copy the python binaries from the python_base
COPY --from=python_base /usr/local /usr/local

# --- VENV SETUP ---
# Set the path for the virtual environment
ENV VENV_PATH="/opt/venv"
# Add the virtual environment's bin directory to the system's PATH.
# This "activates" the venv for all subsequent RUN, CMD, and ENTRYPOINT instructions.
ENV PATH="$VENV_PATH/bin:$PATH"

ENV LD_LIBRARY_PATH="/usr/lib:/usr/local/lib:$LD_LIBRARY_PATH"

# Install runtime packages (excluding python as it's copied)
RUN \
    echo "**** install system packages ****" && \
    apk add --no-cache \
        grep \
        zlib \
        p7zip && \
    \
    echo "**** create python virtual environment ****" && \
    python3 -m venv $VENV_PATH && \
    \
    echo "**** install pip packages into venv ****" && \
    # Upgrade pip within the venv first
    pip install --no-cache-dir --upgrade pip setuptools wheel && \
    # Install your application's dependencies
    pip install --no-cache-dir qbittorrent-api requests aiohttp && \
    echo "**** cleanup ****" && \
    rm -rf \
        /root/.cache \
        /tmp/*

# add local files
COPY root/ /
COPY src/ /app

# add unrar
COPY --from=unrar /usr/bin/unrar-alpine /usr/bin/unrar

# ports and volumes
EXPOSE 8081

VOLUME /config