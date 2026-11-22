// The Moo's own controls, the same calls the Moo website makes. The stories
// and games run on the Moo; the app starts them, follows them and stops them.

const HEADERS = { "Content-Type": "application/json", "X-Moo-Setup": "1" };

export class MooError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(base: string, path: string, body?: unknown): Promise<T> {
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
  useThisPhone: () => call<unknown>(base, "/ai", { mode: "browser" }),
  aiGet: () => call<AiSettings>(base, "/ai"),
  computers: () => call<{ engines: Computer[] }>(base, "/ai/discover"),
  useComputer: (c: Computer) => call<AiSettings>(base, "/ai", { mode: "local", local_url: c.url, local_name: c.name }),
  useCloud: () => call<AiSettings>(base, "/ai", { mode: "cloud" }),
  lent: () => call<{ devices: LentDevice[]; current?: string | null }>(base, "/engine/status"),
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
