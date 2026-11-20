# Moo Bluetooth and wifi setup

Moo supports two transports through the same setup and dashboard UI:

- **Bluetooth:** the hosted GitHub Pages app connects to the Pi's BLE GATT
  service. The laptop remains on its current wifi throughout setup.
- **Wifi:** the existing `moo-####` hotspot and `10.41.0.1:8000` flow remains
  available. The Pi-served build continues to use HTTP directly.

The browser cannot scan for Bluetooth devices silently. A user must press
**Find Moo with Bluetooth** and select the device in Chrome or Edge's chooser.
Wifi discovery remains automatic, so the first completed connection becomes
the active transport.

## Pi installation

Run the existing installer after copying this version of `pi/`:

```bash
cd /path/to/pi
sudo ./install.sh
sudo reboot
```

The installer adds BlueZ, the `bless` Python GATT server, and enables
`moo-bluetooth.service`. Useful checks on the Pi:

```bash
systemctl status moo-bluetooth.service
journalctl -u moo-bluetooth.service -n 100 --no-pager
```

### Optional per-device setup code

BLE writes require an encrypted Bluetooth link. For an additional application
check, place a six-digit code in `/etc/moo/ble-pin` and print that code on the
device or its setup card:

```bash
printf '%s\n' '123456' | sudo tee /etc/moo/ble-pin
sudo chmod 600 /etc/moo/ble-pin
sudo systemctl restart moo-bluetooth.service
```

Use a different randomly generated code for every production device. If the
file is absent, the setup-code field is optional and the service relies on BLE
link encryption only.

## GitHub Pages

The workflow at `.github/workflows/deploy-pages.yml` builds the static app from
`app/` and infers the repository base path. In GitHub repository settings:

1. Open **Settings → Pages**.
2. Choose **GitHub Actions** as the source.
3. Push to `main` or run the workflow manually.

GitHub Pages is HTTPS, as required by Web Bluetooth. The hosted build disables
the service worker so its repository base path cannot conflict with the Pi's
root-scoped PWA. The Pi build still uses the existing PWA.

## BLE protocol

The service uses these UUIDs:

- Service: `6d6f6f00-7c5a-4f1b-9e3d-2a6c8b0d1000`
- Request: `6d6f6f00-7c5a-4f1b-9e3d-2a6c8b0d1001`
- Response notifications: `6d6f6f00-7c5a-4f1b-9e3d-2a6c8b0d1002`

Messages use 20-byte frames so they work with the minimum BLE ATT MTU. The
bridge accepts only the API routes used by the Moo UI and proxies them to the
local setup service at `127.0.0.1:8000`. Audio stays on the Moo: Bluetooth
commands start speaker/microphone tests but do not stream audio to the laptop.
