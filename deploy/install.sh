#!/usr/bin/env bash
# Install or update TX Denon Tools on Debian or Ubuntu.
# Run as root inside the target machine (LXC, VM, or bare metal).
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "install.sh must run as root" >&2
  exit 1
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR=/opt/tx-denon-tools
ENV_FILE=/etc/denon-eq.env

export DEBIAN_FRONTEND=noninteractive
apt_ok=0
for _ in $(seq 1 30); do
  if apt-get update; then
    apt_ok=1
    break
  fi
  sleep 2
done
if [[ "$apt_ok" -ne 1 ]]; then
  echo "apt-get update failed" >&2
  exit 1
fi
apt-get install -y python3 python3-venv python3-pip ca-certificates

if [[ "$SRC" != "$APP_DIR" ]]; then
  mkdir -p "$APP_DIR"
  cp "$SRC/denon_eq.py" "$SRC/requirements.txt" "$APP_DIR/"
  mkdir -p "$APP_DIR/deploy"
  cp "$SRC/deploy/denon-eq.service" "$SRC/deploy/denon-eq.env.example" "$SRC/deploy/install.sh" "$APP_DIR/deploy/"
fi

if ! id denon >/dev/null 2>&1; then
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin denon
fi

chown -R denon:denon "$APP_DIR"

if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
  runuser -u denon -- python3 -m venv "$APP_DIR/venv"
fi
runuser -u denon -- "$APP_DIR/venv/bin/pip" install --disable-pip-version-check -r "$APP_DIR/requirements.txt"

install -m 0644 "$APP_DIR/deploy/denon-eq.service" /etc/systemd/system/denon-eq.service

if [[ ! -f "$ENV_FILE" ]]; then
  install -m 0644 "$APP_DIR/deploy/denon-eq.env.example" "$ENV_FILE"
fi

set_env() {
  local key="$1"
  local value="$2"
  [[ -n "$value" ]] || return 0
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
}

set_env DENON_HOST "${DENON_HOST:-}"
set_env DENON_PORT "${DENON_PORT:-}"
set_env LISTEN_HOST "${LISTEN_HOST:-}"
set_env LISTEN_PORT "${LISTEN_PORT:-}"

systemctl daemon-reload
systemctl enable denon-eq.service
systemctl restart denon-eq.service
systemctl --no-pager --full status denon-eq.service
