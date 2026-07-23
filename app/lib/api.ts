/**
 * Fetch wrapper for the Pi setup service. Always sends X-Moo-Setup: 1.
 */

import { bluetoothRequest } from "./bluetooth";
import { readCachedBase } from "./pi";
import { getTransportKind } from "./transport";

const HEADERS_BASE: Record<string, string> = {
  "Content-Type": "application/json",
  "X-Moo-Setup": "1",
};

export class PiApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "PiApiError";
  }
}

function requireBase(): string {
  // The PWA is served from the Pi itself, so the page's own origin IS the
  // Pi — use it directly. (The next dev server on :3000 has no API, so in
  // that one case fall back to the discovered/cached base.)
  if (typeof window !== "undefined" && process.env.NEXT_PUBLIC_GITHUB_PAGES !== "true") {
    const origin = window.location.origin;
    if (origin.startsWith("http") && !origin.includes(":3000")) {
      return origin;
    }
  }
  const base = readCachedBase();
  if (!base) throw new PiApiError(0, "No Pi connection. Please check your wifi.");
  return base;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (getTransportKind() === "bluetooth") {
    try {
      return await bluetoothRequest<T>(path, init);
    } catch (error) {
      const status = error instanceof Error && "status" in error
        ? Number((error as Error & { status: number }).status)
        : 0;
      throw new PiApiError(status, error instanceof Error ? error.message : "Bluetooth request failed.");
    }
  }
  const base = requireBase();
  let res: Response;
  try {
    res = await fetch(`${base}${path}`, {
      ...init,
      headers: { ...HEADERS_BASE, ...(init?.headers || {}) },
      cache: "no-store",
    });
  } catch {
    throw new PiApiError(0, "Could not reach the Moo. Check your connection.");
  }
  if (!res.ok) {
    const detail = await res.json().then((b) => b?.detail || res.statusText).catch(() => res.statusText);
    throw new PiApiError(res.status, String(detail));
  }
  return (await res.json()) as T;
}

export interface Status {
  configured: boolean;
  wifi_connected: boolean;
  wifi_ssid: string | null;
  assistant_running: boolean;
  device_id: string;
  version: string;
  uptime_seconds: number;
}

export interface WifiNetwork {
  ssid: string;
  signal: number;
  secured: boolean;
}

export interface AudioDevice {
  index: number;
  name: string;
  max_input_channels: number;
  max_output_channels: number;
}

export interface AudioDevices {
  devices: AudioDevice[];
  suggested_input: number | null;
  suggested_output: number | null;
}

export interface MicTestResult {
  peak_level: number;
  heard: boolean;
}

export interface RedactedConfig {
  openai_api_key?: string;
  wifi?: { ssid?: string; configured?: boolean };
  preferences?: { tts_voice?: string; age_range?: string; volume?: number; language?: string };
  audio?: { input_index?: number; output_index?: number };
  device?: { id?: string; version?: string };
  configured_at?: string;
}

export interface BrailleTestResult {
  status: "ok" | "dry_run";
  motors_tested: number[];
  duration_seconds: number;
}

export interface CalibrateStart {
  status: string;
  dry_run: boolean;
  motor_pins: number[][];
  button_pins: number[];
}

export interface LetterState {
  active: boolean;
  dry_run: boolean;
  letter: string | null;
  dots: number[];
  phase: "idle" | "showing" | "holding" | "lowering";
  repeats: number;
}

export interface StoryState {
  active: boolean;
  dry_run: boolean;
  phase:
    | "idle"
    | "greeting"
    | "listening"
    | "thinking"
    | "telling"
    | "quiz"
    | "spelling"
    | "ready"
    | "done"
    | "error";
  message: string | null;
  story: string | null;
  parts: number;
  spelling_word: string | null;
  spell_letter: string | null;
  spell_index: number | null;
  spell_total: number | null;
  spell_dots_left: number | null;
  spell_awaiting: boolean;
  error: string | null;
}

export const api = {
  status: () => request<Status>("/status"),
  scanWifi: () => request<{ networks: WifiNetwork[] }>("/wifi/scan"),
  connectWifi: (ssid: string, password: string) =>
    request<{ status: string }>("/wifi/connect", {
      method: "POST",
      body: JSON.stringify({ ssid, password }),
    }),
  getConfig: () => request<RedactedConfig>("/config"),
  postConfig: (partial: object) =>
    request<RedactedConfig>("/config", { method: "POST", body: JSON.stringify(partial) }),
  audioDevices: () => request<AudioDevices>("/audio/devices"),
  testSpeaker: (outputIndex: number | null) =>
    request<{ status: string }>("/audio/test/speaker", {
      method: "POST",
      body: JSON.stringify({ output_index: outputIndex }),
    }),
  testMic: (inputIndex: number | null, outputIndex: number | null) =>
    request<MicTestResult>("/audio/test/mic", {
      method: "POST",
      body: JSON.stringify({
        input_index: inputIndex,
        output_index: outputIndex,
        playback: true,
      }),
    }),
  restart: () => request<{ status: string }>("/restart", { method: "POST" }),
  reset: () => request<{ status: string }>("/reset", { method: "POST" }),
  logs: (service: "setup" | "assistant", lines = 200) =>
    request<{ service: string; lines: string[] }>(`/logs?service=${service}&lines=${lines}`),
  testBraille: (motorIndex: number | null = null) =>
    request<BrailleTestResult>("/braille/test", {
      method: "POST",
      body: JSON.stringify({ motor_index: motorIndex }),
    }),
  calibrateStart: () =>
    request<CalibrateStart>("/braille/calibrate/start", { method: "POST", body: "{}" }),
  calibratePulse: (
    motorIndex: number,
    direction: "cycle" | "up" | "down" = "cycle"
  ) =>
    request<{ status: string }>("/braille/calibrate/pulse", {
      method: "POST",
      body: JSON.stringify({ motor_index: motorIndex, direction }),
    }),
  calibrateButton: () =>
    request<{ button: number | null }>("/braille/calibrate/button"),
  calibrateSave: (motorPins: number[][], buttonPins: number[]) =>
    request<{ status: string }>("/braille/calibrate/save", {
      method: "POST",
      body: JSON.stringify({ motor_pins: motorPins, button_pins: buttonPins }),
    }),
  calibrateStop: () =>
    request<{ status: string }>("/braille/calibrate/stop", { method: "POST", body: "{}" }),

  // Letter-practice exercise
  lettersStart: () =>
    request<LetterState>("/letters/start", { method: "POST", body: "{}" }),
  lettersShow: (letter: string) =>
    request<LetterState>("/letters/show", {
      method: "POST",
      body: JSON.stringify({ letter }),
    }),
  lettersState: () => request<LetterState>("/letters/state"),
  lettersStop: () =>
    request<{ status: string }>("/letters/stop", { method: "POST", body: "{}" }),

  // Storytelling session
  storyStart: () =>
    request<StoryState>("/story/start", { method: "POST", body: "{}" }),
  storyContinue: () =>
    request<StoryState>("/story/continue", { method: "POST", body: "{}" }),
  storyState: () => request<StoryState>("/story/state"),
  storyStop: () =>
    request<{ status: string }>("/story/stop", { method: "POST", body: "{}" }),
};
