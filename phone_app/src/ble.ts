// The Moo over Bluetooth: the same requests the Moo website sends, through the Moo's Bluetooth bridge
// (moo-bluetooth on the Pi, see BLUETOOTH_SETUP.md). The bridge passes each request on to the setup app
// on the Pi and sends the answer back, so the app works the same over Bluetooth as over wifi for the
// controls. The AI work, with its sound files, stays on wifi: Bluetooth is far too slow for them.
//
// Frames are 20 bytes, the smallest a Bluetooth link carries: 6 bytes of header (request id, piece number,
// number of pieces, each a little-endian 16 bit number) and 14 bytes of the JSON message.

import { PermissionsAndroid, Platform } from "react-native";
import { BleManager, type Device, type Subscription } from "react-native-ble-plx";

const SERVICE = "6d6f6f00-7c5a-4f1b-9e3d-2a6c8b0d1000";
const REQUEST = "6d6f6f00-7c5a-4f1b-9e3d-2a6c8b0d1001";
const RESPONSE = "6d6f6f00-7c5a-4f1b-9e3d-2a6c8b0d1002";
const PAYLOAD = 14;
const TIMEOUT_MS = 45000;

let manager: BleManager | null = null;
let device: Device | null = null;
let monitor: Subscription | null = null;
let nextId = 1;
let writing: Promise<void> = Promise.resolve();
const pending = new Map<number, { parts: (Uint8Array | undefined)[]; total: number; resolve: (v: any) => void; reject: (e: Error) => void; timer: ReturnType<typeof setTimeout> }>();

const ble = () => (manager ??= new BleManager());

const toB64 = (b: Uint8Array) => {
  let s = "";
  for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
  return btoa(s);
};
const fromB64 = (s: string) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

/** UTF-8 bytes to text, without TextDecoder, which the app's JavaScript engine may not have. */
function utf8(b: Uint8Array): string {
  return decodeURIComponent(Array.from(b, (x) => "%" + x.toString(16).padStart(2, "0")).join(""));
}

export const bluetoothName = () => device?.name ?? null;
export const bluetoothConnected = () => !!device;

/** Look for Moos nearby for a few seconds. Each one found is reported once. */
export async function findMoos(seconds: number, found: (d: { id: string; name: string }) => void): Promise<void> {
  if (Platform.OS === "android" && Number(Platform.Version) >= 31) {
    const r = await PermissionsAndroid.requestMultiple([
      PermissionsAndroid.PERMISSIONS.BLUETOOTH_SCAN,
      PermissionsAndroid.PERMISSIONS.BLUETOOTH_CONNECT,
    ]);
    if (Object.values(r).some((v) => v !== PermissionsAndroid.RESULTS.GRANTED)) {
      throw new Error("The app may not use Bluetooth. Allow Nearby devices for Moo in the phone's Settings.");
    }
  }
  const m = ble();
  const state = await m.state();
  if (state !== "PoweredOn") {
    throw new Error(state === "Unauthorized" ? "The app may not use Bluetooth. Allow it in the phone's Settings." : "Bluetooth is off on this phone.");
  }
  const seen = new Set<string>();
  await new Promise<void>((resolve, reject) => {
    m.startDeviceScan([SERVICE], null, (err, d) => {
      if (err) {
        m.stopDeviceScan();
        reject(new Error(err.message));
        return;
      }
      if (d && !seen.has(d.id)) {
        seen.add(d.id);
        found({ id: d.id, name: d.localName ?? d.name ?? "Moo" });
      }
    });
    setTimeout(() => {
      m.stopDeviceScan();
      resolve();
    }, seconds * 1000);
  });
}

/** Connect to a Moo found by findMoos. */
export async function connectMoo(id: string): Promise<string> {
  disconnectMoo();
  const d = await ble().connectToDevice(id, { timeout: 15000 });
  await d.discoverAllServicesAndCharacteristics();
  monitor = d.monitorCharacteristicForService(SERVICE, RESPONSE, (err, c) => {
    if (err || !c?.value) return;
    received(fromB64(c.value));
  });
  d.onDisconnected(() => {
    device = null;
    for (const [rid, p] of pending) {
      clearTimeout(p.timer);
      p.reject(new Error("The Bluetooth link to the Moo was lost."));
      pending.delete(rid);
    }
  });
  device = d;
  return d.name ?? "Moo";
}

export function disconnectMoo() {
  monitor?.remove();
  monitor = null;
  device?.cancelConnection().catch(() => {});
  device = null;
}

function received(frame: Uint8Array) {
  if (frame.length < 6) return;
  const v = new DataView(frame.buffer, frame.byteOffset, frame.byteLength);
  const id = v.getUint16(0, true);
  const index = v.getUint16(2, true);
  const total = v.getUint16(4, true);
  const p = pending.get(id);
  if (!p || total === 0 || index >= total) return;
  if (p.total === 0) p.total = total;
  p.parts[index] = frame.slice(6);
  if (p.parts.filter(Boolean).length < p.total) return;
  clearTimeout(p.timer);
  pending.delete(id);
  const size = p.parts.reduce((n, x) => n + (x?.length ?? 0), 0);
  const all = new Uint8Array(size);
  let at = 0;
  for (const x of p.parts) {
    all.set(x!, at);
    at += x!.length;
  }
  try {
    p.resolve(JSON.parse(utf8(all)));
  } catch {
    p.reject(new Error("The Moo's Bluetooth answer could not be read."));
  }
}

/** One request to the Moo over Bluetooth, as over wifi: the answer's data, or an error. */
export async function bleRequest<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const d = device;
  if (!d) throw new Error("The Moo is not connected over Bluetooth.");
  const id = nextId;
  nextId = nextId >= 65535 ? 1 : nextId + 1;
  const bytes = new TextEncoder().encode(JSON.stringify({ method, path, body }));
  const total = Math.max(1, Math.ceil(bytes.length / PAYLOAD));
  const answer = new Promise<any>((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error("The Moo did not answer over Bluetooth in time."));
    }, TIMEOUT_MS);
    pending.set(id, { parts: [], total: 0, resolve, reject, timer });
  });
  // One request is written at a time, piece by piece.
  writing = writing.catch(() => {}).then(async () => {
    for (let i = 0; i < total; i++) {
      const piece = bytes.subarray(i * PAYLOAD, (i + 1) * PAYLOAD);
      const frame = new Uint8Array(6 + piece.length);
      const v = new DataView(frame.buffer);
      v.setUint16(0, id, true);
      v.setUint16(2, i, true);
      v.setUint16(4, total, true);
      frame.set(piece, 6);
      await d.writeCharacteristicWithResponseForService(SERVICE, REQUEST, toB64(frame));
    }
  });
  await writing;
  const envelope = await answer;
  if (!envelope?.ok) {
    const err: any = new Error(envelope?.detail || `The Moo answered ${envelope?.status}`);
    err.status = envelope?.status ?? 0;
    throw err;
  }
  return envelope.data as T;
}
