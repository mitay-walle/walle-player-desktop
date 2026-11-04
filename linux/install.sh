#!/usr/bin/env bash
# Walle Player library server for Linux (a home server / mini PC): installs or updates the service.
#
#   sudo ./install.sh                                         # install / update the program, (re)start the service
#   sudo MUSIC_DIR=/mnt/media/Music/mp3 ./install.sh          # where the music is (written into settings.json)
#   sudo IMPORT_DIR=/mnt/media/WallePlayer-data ./install.sh  # take over the data folder of the Windows app
#   sudo WITH_AUTOTAG=1 ./install.sh                          # the mood of new tracks is tagged here too (heavy)
#   sudo WITH_AUTOTAG=1 WITH_CLAP=1 ./install.sh              # ... + torch (CPU) for a model with CLAP features
#
# Program: /opt/walle-server (its own Python venv).  Data: ~/.local/share/WallePlayer of SERVICE_USER (default: the
# user who ran sudo).  Service: walle-server (systemd), starts at boot without anyone logging in.
# Debian 12 / Ubuntu 22.04+ (Python 3.11+).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX=/opt/walle-server
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-}}"
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
[ -n "$SERVICE_USER" ] && [ "$SERVICE_USER" != root ] || { echo "set SERVICE_USER=<login> (not root)"; exit 1; }
HOME_DIR="$(getent passwd "$SERVICE_USER" | cut -d: -f6)"
DATA="$HOME_DIR/.local/share/WallePlayer"

echo "== packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip openssh-client libsndfile1 >/dev/null
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "Python 3.11+ needed"; exit 1; }

echo "== program -> $PREFIX"
systemctl stop walle-server 2>/dev/null || true
mkdir -p "$PREFIX"
rm -rf "$PREFIX/musicplayer"
cp -r "$HERE/musicplayer" "$PREFIX/"
cp "$HERE/requirements-server.txt" "$HERE/requirements-autotag.txt" "$HERE/VERSION" "$PREFIX/"
[ -d "$PREFIX/venv" ] || python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q -r "$PREFIX/requirements-server.txt"
ARGS="--service"
if [ "${WITH_AUTOTAG:-0}" = 1 ]; then
  "$PREFIX/venv/bin/pip" install -q -r "$PREFIX/requirements-autotag.txt"
  ARGS="$ARGS --autotag"
fi
if [ "${WITH_AUTOTAG:-0}" = 1 ] && [ "${WITH_CLAP:-0}" = 1 ]; then
  "$PREFIX/venv/bin/pip" install -q torch --index-url https://download.pytorch.org/whl/cpu
  "$PREFIX/venv/bin/pip" install -q transformers
fi

echo "== data -> $DATA"
sudo -u "$SERVICE_USER" mkdir -p "$DATA"
if [ -n "${IMPORT_DIR:-}" ]; then
  [ -f "$IMPORT_DIR/library.db" ] || { echo "no library.db in $IMPORT_DIR"; exit 1; }
  if [ -f "$DATA/library.db" ]; then
    mv "$DATA" "$DATA.before-$(date +%Y%m%d-%H%M%S)"
    sudo -u "$SERVICE_USER" mkdir -p "$DATA"
  fi
  cp -r "$IMPORT_DIR"/. "$DATA"/
  chown -R "$SERVICE_USER": "$DATA"
  echo "   imported from $IMPORT_DIR"
fi
if [ -n "${MUSIC_DIR:-}" ]; then
  [ -d "$MUSIC_DIR" ] || { echo "no folder $MUSIC_DIR"; exit 1; }
  sudo -u "$SERVICE_USER" "$PREFIX/venv/bin/python" - "$DATA/settings.json" "$MUSIC_DIR" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
s = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
s["library_root"] = sys.argv[2]
p.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
PY
  echo "   music folder: $MUSIC_DIR"
fi
[ -f "$DATA/relay_key" ] && chmod 600 "$DATA/relay_key" && chown "$SERVICE_USER": "$DATA/relay_key"

echo "== service"
MOUNT=""
[ -n "${MUSIC_DIR:-}" ] && MOUNT="RequiresMountsFor=$MUSIC_DIR"
cat > /etc/systemd/system/walle-server.service <<UNIT
[Unit]
Description=Walle Player library server
After=network-online.target
Wants=network-online.target
$MOUNT

[Service]
User=$SERVICE_USER
WorkingDirectory=$PREFIX
Environment=PYTHONUNBUFFERED=1
ExecStart=$PREFIX/venv/bin/python -m musicplayer.server $ARGS
Restart=on-failure
RestartSec=10
Nice=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now walle-server
if command -v ufw >/dev/null && ufw status | grep -q active; then
  ufw allow 8780/tcp >/dev/null; ufw allow 8780/udp >/dev/null; echo "   ufw: 8780 tcp/udp allowed"
fi
sleep 3
systemctl --no-pager --lines=8 status walle-server || true
echo "== done: $(cat "$PREFIX/VERSION").  Log: journalctl -u walle-server -f  (and $DATA/server.log)"
