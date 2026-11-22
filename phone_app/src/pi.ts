// The Moo's own controls, the same calls the Moo website makes. The stories
// and games run on the Moo; the app starts them, follows them and stops them.

import { bleRequest } from "./ble";

const HEADERS = { "Content-Type": "application/json", "X-Moo-Setup": "1" };

export class MooError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** The Moo reached over Bluetooth instead of an address on the wifi. */
export const BLUETOOTH = "bluetooth";

async function call<T>(base: string, path: string, body?: unknown): Promise<T> {
  if (base === BLUETOOTH) {
    try {
      return await bleRequest<T>(body === undefined ? "GET" : "POST", path, body);
    } catch (e: any) {
      throw new MooError(e?.status ?? 0, String(e?.message ?? e));
    }
  }
  let r: Response;
  try {
    r = await fetch(`${base}${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: HEADERS,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new MooError(0, "Cannot reach the Moo. Is the phone on the same wifi?");
  }
  if (!r.ok) {
    const detail = await r.json().then((j) => j?.detail).catch(() => null);
    throw new MooError(r.status, String(detail ?? r.statusText));
  }
  return (await r.json()) as T;
}

export interface Status {
  configured: boolean;
  address?: string | null; // the Moo's address on the home wifi
  wifi_connected: boolean;
  wifi_ssid?: string | null;
  device_id: string;
  version: string;
}

export interface StoryState {
  active: boolean;
  phase: string; // idle | greeting | listening | thinking | telling | quiz | spelling | ready | done | error
  message?: string | null;
  story?: string | null;
  parts: number;
  spelling_word?: string | null;
  spell_letter?: string | null;
  game_mode?: string | null;
  error?: string | null;
}

export interface GameState {
  active: boolean;
  game?: string | null;
  phase: string;
  message?: string | null;
  rounds: number;
  spelling_word?: string | null;
  spell_letter?: string | null;
  game_mode?: string | null;
  error?: string | null;
}

export interface LetterState {
  active: boolean;
  letter?: string | null;
  dots: number[];
  phase: string;
}

export type Game = "mix" | "spell" | "name" | "build";

export interface LentDevice {
  name: string;
  jobs_done: number;
  current?: boolean;
}
/** A device that has worked for this Moo, remembered by the Moo. */
export interface KnownDevice {
  name: string;
  kind: "lent" | "computer";
  url?: string | null;
  last_seen?: number | null; // seconds since 1970
}
export interface Computer {
  name: string;
  url: string;
  reachable: boolean;
  ready: boolean;
}
export interface AiSettings {
  mode: "cloud" | "local" | "browser";
  local_url?: string | null;
  local_name?: string | null;
  has_openai_key?: boolean;
}

export const moo = (base: string) => ({
  status: () => call<Status>(base, "/status"),
  wifiScan: () => call<{ networks: { ssid: string; signal: number; secured: boolean }[] }>(base, "/wifi/scan"),
  wifiConnect: (ssid: string, password: string) => call<{ ok?: boolean; message?: string }>(base, "/wifi/connect", { ssid, password }),
  useThisPhone: () => call<unknown>(base, "/ai", { mode: "browser" }),
  aiGet: () => call<AiSettings>(base, "/ai"),
  computers: () => call<{ engines: Computer[] }>(base, "/ai/discover"),
  useComputer: (c: Computer) => call<AiSettings>(base, "/ai", { mode: "local", local_url: c.url, local_name: c.name }),
  useCloud: () => call<AiSettings>(base, "/ai", { mode: "cloud" }),
  lent: () => call<{ devices: LentDevice[]; current?: string | null; known?: KnownDevice[] }>(base, "/engine/status"),
  forget: (name: string) => call<unknown>(base, "/engine/forget", { name }),
  useLent: (name: string) =>
    call<unknown>(base, "/engine/use", { name }).then(() => call<unknown>(base, "/ai", { mode: "browser" })),
  testVoice: () => call<{ line: string; story_seconds: number; voice_seconds: number }>(base, "/ai/test", {}),

  storyStart: () => call<StoryState>(base, "/story/start", {}),
  storyContinue: () => call<StoryState>(base, "/story/continue", {}),
  storyState: () => call<StoryState>(base, "/story/state"),
  storyStop: () => call<unknown>(base, "/story/stop", {}),

  gamesStart: (game: Game) => call<GameState>(base, "/games/start", { game }),
  gamesState: () => call<GameState>(base, "/games/state"),
  gamesStop: () => call<unknown>(base, "/games/stop", {}),

  lettersStart: () => call<LetterState>(base, "/letters/start", {}),
  lettersShow: (letter: string) => call<LetterState>(base, "/letters/show", { letter }),
  lettersState: () => call<LetterState>(base, "/letters/state"),
  lettersStop: () => call<unknown>(base, "/letters/stop", {}),
});

/** Find the Moo: the address used last, then its usual names. */
export async function findMoo(saved: string): Promise<string | null> {
  const tries = [saved, "http://moo.local:8000", "http://10.41.0.1:8000"].filter(Boolean);
  for (const base of [...new Set(tries)]) {
    try {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), 3000);
      const r = await fetch(`${base}/status`, { signal: ctl.signal });
      clearTimeout(t);
      if (r.ok) return base;
    } catch {
      /* try the next one */
    }
  }
  return null;
}
