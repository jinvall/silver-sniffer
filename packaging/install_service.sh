#!/usr/bin/env bash
set -euo pipefail

# packaging/install_service.sh
# Create and optionally install a systemd unit for Silver Sniffer.
# Usage:
#   ./packaging/install_service.sh                # create temp unit for current user/workdir
#   ./packaging/install_service.sh --user jason --workdir /home/jason/silver-sniffer
#   ./packaging/install_service.sh --install      # create then copy+enable+start (requires sudo)

USER_ARG="${USER:-jason}"
WORKDIR_ARG="/home/${USER_ARG}/silver-sniffer"
SERVICE_NAME="silver-sniffer"
INSTALL=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user|-u)
      USER_ARG="$2"; shift 2;;
    --workdir|-w)
      WORKDIR_ARG="$2"; shift 2;;
    --service|-s)
      SERVICE_NAME="$2"; shift 2;;
    --install|-i)
      INSTALL=true; shift;;
    --help|-h)
      sed -n '1,120p' "$0"; exit 0;;
    *)
      echo "Unknown arg: $1"; exit 1;;
  esac
done

SRC="packaging/${SERVICE_NAME}.service"
if [[ ! -f "$SRC" ]]; then
  echo "ERROR: template $SRC not found" >&2
  exit 2
fi

TMP="/tmp/${SERVICE_NAME}-${USER_ARG}.service"
# replace placeholders in the template
sed \
  -e "s|User=youruser|User=${USER_ARG}|g" \
  -e "s|WorkingDirectory=/home/youruser/silver-sniffer|WorkingDirectory=${WORKDIR_ARG}|g" \
  "$SRC" > "$TMP"

chmod 644 "$TMP"

echo "Prepared systemd unit: $TMP"

echo
echo "To install manually run (requires sudo):"
echo "  sudo cp $TMP /etc/systemd/system/${SERVICE_NAME}.service"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable --now ${SERVICE_NAME}.service"

echo
if [ "$INSTALL" = true ]; then
  echo "Installing now... (you will be prompted for sudo)"
  sudo cp "$TMP" /etc/systemd/system/${SERVICE_NAME}.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now "${SERVICE_NAME}.service"
  echo "Service ${SERVICE_NAME} installed and started. Check status: sudo systemctl status ${SERVICE_NAME}.service"
fi
