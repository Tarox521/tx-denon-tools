#!/usr/bin/env bash
# Create or update an unprivileged Proxmox LXC running TX Denon Tools.
# Run this on the Proxmox VE node, as root.
#
#   bash ct/tx-denon-tools.sh
#   bash ct/tx-denon-tools.sh update 120
#
# From GitHub:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/Tarox521/tx-denon-tools/main/ct/tx-denon-tools.sh)"
set -euo pipefail

APP="TX Denon Tools"
var_hostname="${var_hostname:-tx-denon-tools}"
var_cpu="${var_cpu:-1}"
var_ram="${var_ram:-512}"
var_disk="${var_disk:-4}"
var_bridge="${var_bridge:-vmbr0}"
var_net="${var_net:-dhcp}"
var_gw="${var_gw:-}"
var_unprivileged="${var_unprivileged:-1}"
var_template_storage="${var_template_storage:-local}"
var_container_storage="${var_container_storage:-}"
var_tags="${var_tags:-audio;denon}"
DENON_HOST="${DENON_HOST:-172.16.2.121}"
DENON_PORT="${DENON_PORT:-10443}"
# Public clone URL. Export GITHUB_REPO to override.
GITHUB_REPO="${GITHUB_REPO:-https://github.com/Tarox521/tx-denon-tools.git}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this script as root on the Proxmox node." >&2
  exit 1
fi
if ! command -v pct >/dev/null 2>&1 || ! command -v pveam >/dev/null 2>&1; then
  echo "pct/pveam not found. This script runs on a Proxmox VE node, not inside a guest." >&2
  exit 1
fi

say() {
  printf '\n== %s\n' "$*"
}

prompt() {
  local varname="$1"
  local label="$2"
  local current="$3"
  if [[ "${NONINTERACTIVE:-0}" == "1" || ! -r /dev/tty ]]; then
    printf -v "$varname" '%s' "$current"
    return
  fi
  local input
  read -rp "$label [$current]: " input </dev/tty || true
  printf -v "$varname" '%s' "${input:-$current}"
}

detect_rootfs_storage() {
  if [[ -n "$var_container_storage" ]]; then
    return
  fi
  local stores
  stores="$(pvesm status -content rootdir | awk 'NR>1 {print $1}')"
  if grep -qx 'local-lvm' <<<"$stores"; then
    var_container_storage=local-lvm
  elif grep -qx 'local-zfs' <<<"$stores"; then
    var_container_storage=local-zfs
  else
    var_container_storage="$(awk 'NR==1 {print $1}' <<<"$stores")"
  fi
  if [[ -z "$var_container_storage" ]]; then
    echo "No storage with content type 'rootdir' found." >&2
    exit 1
  fi
}

ensure_template() {
  say "Refreshing appliance templates"
  pveam update
  TEMPLATE="$(pveam available --section system | awk '/debian-[0-9]+-standard_/ {print $2}' | sort -V | tail -n1)"
  if [[ -z "${TEMPLATE:-}" ]]; then
    echo "No debian-*-standard template in the Proxmox appliance list." >&2
    exit 1
  fi
  if ! pveam list "$var_template_storage" | awk '{print $1}' | grep -q "/${TEMPLATE}$"; then
    say "Downloading $TEMPLATE"
    pveam download "$var_template_storage" "$TEMPLATE"
  fi
}

stage_app() {
  local ctid="$1"
  pct exec "$ctid" -- mkdir -p /opt/tx-denon-tools
  if [[ -n "${REPO_ROOT:-}" ]]; then
    local archive
    archive="$(mktemp /tmp/tx-denon-tools.XXXXXX.tgz)"
    tar -C "$REPO_ROOT" -czf "$archive" denon_eq.py requirements.txt deploy
    pct push "$ctid" "$archive" /tmp/tx-denon-tools.tgz
    rm -f "$archive"
    pct exec "$ctid" -- tar -xzf /tmp/tx-denon-tools.tgz -C /opt/tx-denon-tools
    pct exec "$ctid" -- rm -f /tmp/tx-denon-tools.tgz
    return
  fi
  if [[ -z "$GITHUB_REPO" ]]; then
    echo "GITHUB_REPO is empty. Export GITHUB_REPO=https://github.com/Tarox521/tx-denon-tools.git or run the script from a checkout." >&2
    exit 1
  fi
  pct exec "$ctid" -- bash -lc "export DEBIAN_FRONTEND=noninteractive; apt-get update; apt-get install -y git ca-certificates; rm -rf /opt/tx-denon-tools; git clone --depth 1 $(printf '%q' "$GITHUB_REPO") /opt/tx-denon-tools"
}

install_in_ct() {
  local ctid="$1"
  say "Installing $APP in CT $ctid"
  stage_app "$ctid"
  pct exec "$ctid" -- env \
    DENON_HOST="$DENON_HOST" \
    DENON_PORT="$DENON_PORT" \
    bash /opt/tx-denon-tools/deploy/install.sh
}

wait_for_exec() {
  local ctid="$1"
  local _
  for _ in $(seq 1 30); do
    if pct exec "$ctid" -- true >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "Container $ctid did not accept pct exec in time." >&2
  exit 1
}

guest_ip() {
  local ctid="$1"
  local ip=""
  local _
  for _ in $(seq 1 20); do
    ip="$(pct exec "$ctid" -- bash -lc "hostname -I 2>/dev/null" | awk '{print $1}')"
    if [[ -n "$ip" ]]; then
      printf '%s\n' "$ip"
      return 0
    fi
    sleep 1
  done
  return 0
}

create_ct() {
  detect_rootfs_storage
  local nextid
  nextid="$(pvesh get /cluster/nextid)"
  prompt CTID "Container ID" "${CTID:-$nextid}"
  prompt var_hostname "Hostname" "$var_hostname"
  prompt var_bridge "Bridge" "$var_bridge"
  prompt var_net "IPv4 (dhcp or CIDR)" "$var_net"
  if [[ "$var_net" != "dhcp" ]]; then
    prompt var_gw "Gateway" "$var_gw"
  fi
  prompt var_container_storage "Rootfs storage" "$var_container_storage"
  prompt DENON_HOST "Denon host" "$DENON_HOST"
  prompt DENON_PORT "Denon port" "$DENON_PORT"

  if pct status "$CTID" >/dev/null 2>&1; then
    echo "CT $CTID already exists. Use: $0 update $CTID" >&2
    exit 1
  fi

  ensure_template

  local net0="name=eth0,bridge=${var_bridge},ip=${var_net}"
  if [[ "$var_net" != "dhcp" ]]; then
    if [[ -z "$var_gw" ]]; then
      echo "A static address needs var_gw / a gateway." >&2
      exit 1
    fi
    net0="${net0},gw=${var_gw}"
  fi

  CT_PASSWORD="$(openssl rand -hex 12)"
  say "Creating CT $CTID ($TEMPLATE)"
  pct create "$CTID" "${var_template_storage}:vztmpl/${TEMPLATE}" \
    --hostname "$var_hostname" \
    --cores "$var_cpu" \
    --memory "$var_ram" \
    --swap 256 \
    --rootfs "${var_container_storage}:${var_disk}" \
    --net0 "$net0" \
    --unprivileged "$var_unprivileged" \
    --features nesting=1 \
    --onboot 1 \
    --password "$CT_PASSWORD" \
    --tags "$var_tags"

  pct start "$CTID"
  wait_for_exec "$CTID"
  install_in_ct "$CTID"

  local ip
  ip="$(guest_ip "$CTID")"
  cat <<EOF

$APP is installed.
  CTID:       $CTID
  Hostname:   $var_hostname
  Root pass:  $CT_PASSWORD
  UI:         http://${ip:-<container-ip>}:8088/
  Health:     http://${ip:-<container-ip>}:8088/health
  Receiver:   https://${DENON_HOST}:${DENON_PORT}
  Config:     pct exec $CTID -- editor /etc/denon-eq.env
  Logs:       pct exec $CTID -- journalctl -u denon-eq -f
EOF
}

update_ct() {
  local ctid="${1:-}"
  if [[ -z "$ctid" ]]; then
    echo "Usage: $0 update <CTID>" >&2
    exit 1
  fi
  if ! pct status "$ctid" >/dev/null 2>&1; then
    echo "CT $ctid does not exist." >&2
    exit 1
  fi
  if [[ "$(pct status "$ctid" | awk '{print $2}')" != "running" ]]; then
    pct start "$ctid"
  fi
  wait_for_exec "$ctid"
  install_in_ct "$ctid"
  say "Updated CT $ctid"
}

SCRIPT_PATH="${BASH_SOURCE[0]}"
REPO_ROOT=""
if [[ -f "$SCRIPT_PATH" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
  if [[ -f "$SCRIPT_DIR/../denon_eq.py" && -f "$SCRIPT_DIR/../deploy/install.sh" ]]; then
    REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
  fi
fi

echo "========================================"
echo " $APP"
echo " Proxmox LXC"
echo "========================================"

case "${1:-create}" in
  create)
    create_ct
    ;;
  update)
    update_ct "${2:-}"
    ;;
  *)
    echo "Usage: $0 [create|update <CTID>]" >&2
    exit 1
    ;;
esac
