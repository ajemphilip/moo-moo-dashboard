#!/usr/bin/env bash
# Provision a fresh Raspberry Pi OS Lite (Bookworm/Trixie 64-bit) for Moo.
# Run once as the maker user with sudo available. Idempotent.
#
# Prerequisite: comitup must already be installed (`apt install comitup`)
# before running this script.
set -euo pipefail

MOO_USER="${MOO_USER:-philipn1}"
MOO_HOME="/opt/moo"
PI_SRC_DIR="$MOO_HOME/pi"
VENV="$MOO_HOME/venv"
THIS_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Installing system packages"
sudo apt-get update
sudo apt-get install -y \
  python3-venv python3-dev build-essential swig \
  portaudio19-dev libopenblas-dev liblgpio-dev \
  ffmpeg mpg123 alsa-utils \
  avahi-daemon avahi-utils libnss-mdns \
  bluez rfkill iw i2c-tools

echo "==> Hostname → moo (mDNS will publish moo.local)"
sudo hostnamectl set-hostname moo

echo "==> Adding $MOO_USER to dialout, audio, gpio, i2c groups"
sudo usermod -aG dialout,audio,gpio,i2c "$MOO_USER"

echo "==> Enabling I2C (MCP23017 + MPR121)"
sudo raspi-config nonint do_i2c 0

echo "==> Enabling UART (optional, for ESP32 fallback)"
sudo raspi-config nonint do_serial_hw 0
sudo raspi-config nonint do_serial_cons 1

echo "==> Keeping onboard Bluetooth enabled"
# `dtoverlay=disable-bt` gives PL011 to GPIO serial0, but it also removes the
# Pi's Bluetooth controller. Moo now uses BLE for setup/control, so Bluetooth
# takes priority and the optional ESP32 UART fallback uses the remaining UART.
sudo sed -i '/^dtoverlay=disable-bt$/d' /boot/firmware/config.txt

echo "==> Setting the USB port to HOST mode (for the USB soundcard: mic + speaker)"
# The Pi Zero 2W has a single USB data port. The Moo HOSTs a USB audio device
# on it, so dwc2 must be in host mode. (A peripheral/gadget mode was used only
# as a temporary dev tether during bring-up — it is not part of the product.)
sudo sed -i '/^dtoverlay=dwc2/d' /boot/firmware/config.txt
echo 'dtoverlay=dwc2,dr_mode=host' | sudo tee -a /boot/firmware/config.txt > /dev/null
# Drop any leftover gadget module-load from the dev tether.
sudo sed -i 's/ modules-load=dwc2,g_ncm//; s/ modules-load=dwc2,g_ether//; s/ modules-load=dwc2//' /boot/firmware/cmdline.txt

echo "==> Creating /etc/moo"
sudo install -d -m 0700 -o "$MOO_USER" -g "$MOO_USER" /etc/moo

echo "==> Creating $MOO_HOME"
sudo install -d -o "$MOO_USER" -g "$MOO_USER" "$MOO_HOME"

echo "==> Copying Pi sources to $PI_SRC_DIR"
sudo rm -rf "$PI_SRC_DIR"
sudo install -d -o "$MOO_USER" -g "$MOO_USER" "$PI_SRC_DIR"
sudo cp -r "$THIS_DIR"/* "$PI_SRC_DIR"/
sudo chown -R "$MOO_USER":"$MOO_USER" "$PI_SRC_DIR"

echo "==> Building venv at $VENV"
sudo -u "$MOO_USER" python3 -m venv "$VENV"
sudo -u "$MOO_USER" "$VENV/bin/pip" install --upgrade pip
sudo -u "$MOO_USER" "$VENV/bin/pip" install -r "$PI_SRC_DIR/requirements.txt"

echo "==> Installing moo-setup and moo-status CLI wrappers"
sudo tee /usr/local/bin/moo-setup > /dev/null <<EOF
#!/usr/bin/env bash
exec $VENV/bin/python3 $PI_SRC_DIR/moo-setup "\$@"
EOF
sudo tee /usr/local/bin/moo-status > /dev/null <<EOF
#!/usr/bin/env bash
exec $VENV/bin/python3 $PI_SRC_DIR/moo-status "\$@"
EOF
sudo chmod +x /usr/local/bin/moo-setup /usr/local/bin/moo-status

echo "==> Installing systemd units"
sudo install -m 0644 "$THIS_DIR/systemd/moo-setup.service" /etc/systemd/system/
sudo install -m 0644 "$THIS_DIR/systemd/moo-assistant.service" /etc/systemd/system/
sudo install -m 0644 "$THIS_DIR/systemd/moo-bluetooth.service" /etc/systemd/system/

echo "==> Installing moo-offline (lets a computer stay on Moo's own wifi without internet)"
sudo install -m 0755 -o root -g root "$THIS_DIR/moo-offline" /usr/local/sbin/moo-offline

echo "==> Installing sudoers fragment"
sudo install -m 0440 -o root -g root "$THIS_DIR/sudoers.d/moo-setup" /etc/sudoers.d/moo-setup
sudo visudo -c -f /etc/sudoers.d/moo-setup

echo "==> Installing comitup config"
sudo install -m 0644 "$THIS_DIR/comitup.conf" /etc/comitup.conf

echo "==> Reloading systemd"
sudo systemctl daemon-reload
sudo systemctl enable moo-setup.service
sudo systemctl enable moo-assistant.service
sudo systemctl enable bluetooth.service
sudo systemctl enable moo-bluetooth.service

echo
echo "==> DONE. Reboot to apply hostname, serial, and dtoverlay changes:"
echo "    sudo reboot"
