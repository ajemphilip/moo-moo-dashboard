#!/usr/bin/env bash
# prepare_image.sh — wipe per-device state on the gold-master Pi just
# before snapshotting the SD card.
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "Must run as root (use sudo)." >&2
  exit 1
fi

systemctl stop moo-assistant.service || true
systemctl stop moo-setup.service || true
rm -f /etc/moo/config.json
comitup-cli d || true
rm -f /etc/wpa_supplicant/wpa_supplicant-wlan0.conf
rm -f /var/lib/comitup/comitup.json
rm -f /etc/ssh/ssh_host_*
cat > /etc/systemd/system/regenerate-ssh-host-keys.service <<'EOF'
[Unit]
Description=Regenerate SSH host keys on first boot
ConditionPathExistsGlob=!/etc/ssh/ssh_host_*_key
Before=ssh.service

[Service]
Type=oneshot
ExecStart=/usr/bin/ssh-keygen -A
ExecStartPost=/bin/systemctl disable regenerate-ssh-host-keys.service

[Install]
WantedBy=multi-user.target
EOF
systemctl enable regenerate-ssh-host-keys.service
truncate -s 0 /etc/machine-id
rm -f /var/lib/dbus/machine-id || true
ln -sf /etc/machine-id /var/lib/dbus/machine-id
journalctl --rotate || true
journalctl --vacuum-time=1s || true
rm -f /home/philipn1/.bash_history /root/.bash_history
fstrim -v / || true
echo
echo "==> DONE. Power off NOW: sudo poweroff"
