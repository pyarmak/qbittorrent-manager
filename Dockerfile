FROM ghcr.io/linuxserver/unrar:latest AS unrar

FROM ghcr.io/linuxserver/baseimage-alpine:edge

# environment settings
ENV HOME="/config" \
XDG_CONFIG_HOME="/config" \
XDG_DATA_HOME="/config"

# install runtime packages and qbitorrent-cli
RUN \
  echo "**** install packages ****" && \
  apk add --no-cache \
    grep \
    p7zip \
    py3-psutil \
    python3 && \
  if [ ! -e /usr/bin/python ]; then ln -sf python3 /usr/bin/python ; fi && \
  echo "**** install pip ****" && \
  rm /usr/lib/python3.12/EXTERNALLY-MANAGED && \
  python -m ensurepip && \
  rm -r /usr/lib/python*/ensurepip && \
  if [ ! -e /usr/bin/pip ]; then ln -s pip3 /usr/bin/pip ; fi && \
  echo "**** install pip packages ****" && \
  pip install --no-cache-dir --upgrade pip setuptools wheel qbittorrent-api requests aiohttp && \
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