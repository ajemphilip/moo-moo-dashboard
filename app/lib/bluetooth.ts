import { selectTransport } from "./transport";

export const MOO_BLE_SERVICE_UUID = "6d6f6f00-7c5a-4f1b-9e3d-2a6c8b0d1000";
const REQUEST_UUID = "6d6f6f00-7c5a-4f1b-9e3d-2a6c8b0d1001";
const RESPONSE_UUID = "6d6f6f00-7c5a-4f1b-9e3d-2a6c8b0d1002";

// Twenty-byte frames work with the minimum BLE ATT MTU (23 bytes).
const FRAME_BYTES = 20;
const HEADER_BYTES = 6;
const PAYLOAD_BYTES = FRAME_BYTES - HEADER_BYTES;
const REQUEST_TIMEOUT_MS = 45000;
const DEVICE_ID_KEY = "moo_ble_device_id";
const PIN_KEY = "moo_ble_pin";

type PendingRequest = {
  chunks: Array<Uint8Array | undefined>;
  total: number;
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
  timer: ReturnType<typeof setTimeout>;
};

type BleEnvelope = {
  ok: boolean;
  status: number;
  data?: unknown;
  detail?: string;
};

let device: BluetoothDevice | null = null;
let requestCharacteristic: BluetoothRemoteGATTCharacteristic | null = null;
let responseCharacteristic: BluetoothRemoteGATTCharacteristic | null = null;
let connectPromise: Promise<string> | null = null;
let nextRequestId = 1;
let writeQueue: Promise<void> = Promise.resolve();
const pending = new Map<number, PendingRequest>();

export function bluetoothSupported(): boolean {
  return typeof navigator !== "undefined" && !!navigator.bluetooth;
}

export function bluetoothDeviceName(): string | null {
  return device?.name ?? null;
}

export function bluetoothConnected(): boolean {
  return !!device?.gatt?.connected && !!requestCharacteristic && !!responseCharacteristic;
}

export function setBluetoothPin(pin: string): void {
  if (typeof window === "undefined") return;
  const normalized = pin.replace(/\D/g, "").slice(0, 6);
  if (normalized) window.localStorage.setItem(PIN_KEY, normalized);
  else window.localStorage.removeItem(PIN_KEY);
}

export async function connectBluetooth(): Promise<string> {
  if (!navigator.bluetooth) {
    throw new Error("Bluetooth setup requires Chrome or Edge with Bluetooth enabled.");
  }
  const selected = await navigator.bluetooth.requestDevice({
    filters: [{ namePrefix: "Moo-", services: [MOO_BLE_SERVICE_UUID] }],
    optionalServices: [MOO_BLE_SERVICE_UUID],
  });
  return finishConnection(selected);
}

export async function restoreBluetoothConnection(): Promise<string | null> {
  try {
    if (!navigator.bluetooth?.getDevices) return null;
    if (bluetoothConnected()) return device?.name ?? "Moo";
    const rememberedId = window.localStorage.getItem(DEVICE_ID_KEY);
    const devices = await navigator.bluetooth.getDevices();
    const remembered = devices.find((candidate) => candidate.id === rememberedId)
      ?? devices.find((candidate) => candidate.name?.startsWith("Moo-"));
    if (!remembered) return null;
    return await finishConnection(remembered);
  } catch {
    return null;
  }
}

async function finishConnection(selected: BluetoothDevice): Promise<string> {
  if (connectPromise && device?.id === selected.id) return connectPromise;
  device = selected;
  connectPromise = (async () => {
    if (!selected.gatt) throw new Error("The selected device does not expose Bluetooth controls.");
    const server = selected.gatt.connected
      ? selected.gatt
      : await runGattStep("connect to Moo", () => selected.gatt!.connect());
    const service = await runGattStep(
      "open Moo's Bluetooth service",
      () => server.getPrimaryService(MOO_BLE_SERVICE_UUID),
    );
    requestCharacteristic = await runGattStep(
      "open Moo's request channel",
      () => service.getCharacteristic(REQUEST_UUID),
    );
    responseCharacteristic = await runGattStep(
      "open Moo's response channel",
      () => service.getCharacteristic(RESPONSE_UUID),
    );
    await runGattStep(
      "enable Moo's Bluetooth responses",
      () => responseCharacteristic!.startNotifications(),
    );
    responseCharacteristic.removeEventListener("characteristicvaluechanged", onResponseFrame);
    responseCharacteristic.addEventListener("characteristicvaluechanged", onResponseFrame);
    selected.removeEventListener("gattserverdisconnected", onDisconnected);
    selected.addEventListener("gattserverdisconnected", onDisconnected);
    window.localStorage.setItem(DEVICE_ID_KEY, selected.id);
    selectTransport("bluetooth");
    return selected.name ?? "Moo";
  })();
  try {
    return await connectPromise;
  } finally {
    connectPromise = null;
  }
}

async function runGattStep<T>(description: string, operation: () => Promise<T>): Promise<T> {
  try {
    return await operation();
  } catch (error) {
    const detail = error instanceof Error ? error.message : "Unknown Bluetooth error";
    throw new Error(`Could not ${description}. ${detail}`);
  }
}

function onDisconnected(): void {
  requestCharacteristic = null;
  responseCharacteristic = null;
  for (const [id, request] of pending) {
    clearTimeout(request.timer);
    request.reject(new Error("Bluetooth connection to Moo was lost."));
    pending.delete(id);
  }
}

export async function bluetoothRequest<T>(path: string, init?: RequestInit): Promise<T> {
  if (!bluetoothConnected()) {
    const restored = await restoreBluetoothConnection();
    if (!restored || !requestCharacteristic) {
      throw new Error("Moo is not connected over Bluetooth. Return to Connect and select it again.");
    }
  }

  const requestId = allocateRequestId();
  const pin = typeof window === "undefined" ? null : window.localStorage.getItem(PIN_KEY);
  const body = typeof init?.body === "string" && init.body.length > 0
    ? JSON.parse(init.body)
    : undefined;
  const bytes = new TextEncoder().encode(JSON.stringify({
    method: init?.method ?? "GET",
    path,
    body,
    ...(pin ? { pin } : {}),
  }));
  const frames = encodeFrames(requestId, bytes);

  const response = new Promise<unknown>((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(requestId);
      reject(new Error("Moo did not answer over Bluetooth in time."));
    }, REQUEST_TIMEOUT_MS);
    pending.set(requestId, { chunks: [], total: 0, resolve, reject, timer });
  });

  const operation = writeQueue.catch(() => {}).then(async () => {
    for (const frame of frames) {
      await requestCharacteristic!.writeValueWithResponse(frame);
    }
  });
  writeQueue = operation;
  try {
    await operation;
  } catch (error) {
    const entry = pending.get(requestId);
    if (entry) {
      clearTimeout(entry.timer);
      pending.delete(requestId);
      const detail = error instanceof Error ? error.message : "Unknown Bluetooth error";
      entry.reject(new Error(`Could not send a secure command to Moo. ${detail}`));
    }
  }

  const envelope = (await response) as BleEnvelope;
  if (!envelope.ok) {
    const error = new Error(envelope.detail || `Moo returned ${envelope.status}`) as Error & { status: number };
    error.status = envelope.status;
    throw error;
  }
  return envelope.data as T;
}

function allocateRequestId(): number {
  for (let attempts = 0; attempts < 0xffff; attempts += 1) {
    const id = nextRequestId;
    nextRequestId = nextRequestId >= 0xffff ? 1 : nextRequestId + 1;
    if (!pending.has(id)) return id;
  }
  throw new Error("Too many Bluetooth requests are pending.");
}

function encodeFrames(requestId: number, bytes: Uint8Array): Uint8Array[] {
  const total = Math.max(1, Math.ceil(bytes.length / PAYLOAD_BYTES));
  if (total > 0xffff) throw new Error("Bluetooth request is too large.");
  const frames: Uint8Array[] = [];
  for (let index = 0; index < total; index += 1) {
    const payload = bytes.slice(index * PAYLOAD_BYTES, (index + 1) * PAYLOAD_BYTES);
    const frame = new Uint8Array(HEADER_BYTES + payload.length);
    const view = new DataView(frame.buffer);
    view.setUint16(0, requestId, true);
    view.setUint16(2, index, true);
    view.setUint16(4, total, true);
    frame.set(payload, HEADER_BYTES);
    frames.push(frame);
  }
  return frames;
}

function onResponseFrame(event: Event): void {
  const characteristic = event.target as BluetoothRemoteGATTCharacteristic;
  const value = characteristic.value;
  if (!value || value.byteLength < HEADER_BYTES) return;
  const requestId = value.getUint16(0, true);
  const index = value.getUint16(2, true);
  const total = value.getUint16(4, true);
  const request = pending.get(requestId);
  if (!request || total === 0 || index >= total) return;
  if (request.total !== 0 && request.total !== total) return;
  request.total = total;
  request.chunks[index] = new Uint8Array(value.buffer.slice(
    value.byteOffset + HEADER_BYTES,
    value.byteOffset + value.byteLength
  ));
  if (request.chunks.filter(Boolean).length !== total) return;

  clearTimeout(request.timer);
  pending.delete(requestId);
  try {
    const length = request.chunks.reduce((sum, chunk) => sum + (chunk?.length ?? 0), 0);
    const joined = new Uint8Array(length);
    let offset = 0;
    for (const chunk of request.chunks) {
      if (!chunk) throw new Error("Bluetooth response was incomplete.");
      joined.set(chunk, offset);
      offset += chunk.length;
    }
    request.resolve(JSON.parse(new TextDecoder().decode(joined)));
  } catch (error) {
    request.reject(error instanceof Error ? error : new Error("Invalid Bluetooth response."));
  }
}
