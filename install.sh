#!/usr/bin/env bash
# Run on the Pi:   bash /boot/firmware/pi-dashboard/install.sh
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$HOME/pi-dashboard"

echo "==> System packages"
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip python3-numpy python3-pil fonts-dejavu-core

echo "==> Enabling SPI + I2C (the Inky needs both)"
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_i2c 0

echo "==> Freeing the SPI chip-select pin the Inky driver needs"
CFG=/boot/firmware/config.txt
grep -q '^dtoverlay=spi0-0cs' "$CFG" || echo "dtoverlay=spi0-0cs" | sudo tee -a "$CFG" >/dev/null

echo "==> Copying app to $APP"
mkdir -p "$APP"
[ "$SRC" != "$APP" ] && cp -r "$SRC"/. "$APP"/
if [ ! -f "$APP/config.yaml" ]; then
  cp "$APP/config.example.yaml" "$APP/config.yaml"
  echo "!! No config.yaml found. Created one from the example; edit $APP/config.yaml"
fi
chmod 600 "$APP/config.yaml"
[ -f "$APP/token.json" ] && chmod 600 "$APP/token.json"

echo "==> Python environment (a few minutes on a Zero 2 W)"
python3 -m venv --system-site-packages "$APP/.venv"
"$APP/.venv/bin/pip" install -r "$APP/requirements.txt" "inky[rpi]>=2.0"

echo "==> Hourly systemd timer"
sed -e "s|__USER__|$USER|g" -e "s|__APP__|$APP|g" "$APP/systemd/pi-dashboard.service" \
  | sudo tee /etc/systemd/system/pi-dashboard.service >/dev/null
sudo cp "$APP/systemd/pi-dashboard.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pi-dashboard.timer

echo
echo "Done. Reboot once so SPI/I2C take effect:   sudo reboot"
echo "Manual refresh:  $APP/.venv/bin/python $APP/dashboard.py"
echo "Logs:            journalctl -u pi-dashboard.service -n 50"
echo
echo "Enjoying it? Buy me a coffee: https://buymeacoffee.com/breyeslo"
