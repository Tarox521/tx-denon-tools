# TX Denon Tools

Web mixer for the Denon graphic EQ. A small Flask app sends the band values to the receiver's own HTTPS API and serves the fader UI on port 80, so the browser needs no port in the address.

Named presets are stored on the server in `/var/lib/tx-denon-tools/presets.json`, so every browser sees the same list. The last fader positions stay in that browser.

`POST /set_eq` turns the nine bands (63 Hz through 16 kHz, -20 dB to +6 dB) into Denon raw values (dB times 10) and calls `/ajax/audio/set_config?type=10`. The receiver certificate is not verified.

## Configure

`/etc/denon-eq.env`:

```
DENON_HOST=
DENON_PORT=10443
LISTEN_HOST=0.0.0.0
LISTEN_PORT=80
```

After an edit: `systemctl restart denon-eq`.

## Proxmox LXC

Run on the Proxmox VE node, as root, from a checkout of this repo. The script creates an unprivileged Debian LXC (1 core, 512 MB, 4 GB), installs the app, and prints the root password and the UI address.

```bash
bash ct/tx-denon-tools.sh
```

Useful overrides, all optional:

| Variable | Default | Meaning |
| --- | --- | --- |
| `CTID` | next free id | Container id |
| `var_hostname` | `tx-denon-tools` | Hostname |
| `var_bridge` | `vmbr0` | Bridge |
| `var_net` | `dhcp` | `dhcp` or a CIDR address |
| `var_gw` | empty | Gateway, required for a static address |
| `var_container_storage` | `local-lvm`, else first rootdir storage | Disk pool |
| `var_template_storage` | `local` | Storage that holds appliance templates |
| `DENON_HOST` | empty, must be entered | Receiver address |
| `DENON_PORT` | `10443` | Receiver HTTPS port |
| `NONINTERACTIVE` | `0` | Set to `1` to skip prompts |

Update an existing container from this checkout:

```bash
bash ct/tx-denon-tools.sh update 120
```

Once the repo is on GitHub, the same script can be piped from raw content. Set `GITHUB_REPO` to the clone URL so the container can `git clone` it:

```bash
export GITHUB_REPO=https://github.com/Tarox521/tx-denon-tools.git
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Tarox521/tx-denon-tools/main/ct/tx-denon-tools.sh)"
```

## Debian or Ubuntu without Proxmox

```bash
git clone https://github.com/Tarox521/tx-denon-tools.git
cd tx-denon-tools
DENON_HOST=192.0.2.10 bash deploy/install.sh
```

The service runs as the system user `denon` and is allowed to bind port 80. UI: `http://<host>/`. Health check: `http://<host>/health`.

```bash
journalctl -u denon-eq -f
```
