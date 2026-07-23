/**
 * Pi discovery: try the comitup AP IP and the mDNS hostname in parallel,
 * cache the winner in localStorage, fall back to re-probing if it ever
 * stops responding.
 */

import { selectTransport } from "./transport";

const HOTSPOT_BASE = "http://10.41.0.1:8000";
const MDNS_BASE = "http://moo.local:8000";
const STORAGE_KEY = "moo_base";
const PROBE_TIMEOUT_MS = 3000;

const CANDIDATES = [HOTSPOT_BASE, MDNS_BASE];

export type PiBase = string;

export async function discoverPi(): Promise<PiBase | null> {
  // Served from the Pi itself? Then the current origin IS the Pi — use it.
  if (typeof window !== "undefined" && process.env.NEXT_PUBLIC_GITHUB_PAGES !== "true") {
    const origin = window.location.origin;
    if (origin.startsWith("http") && !origin.includes(":3000")) {
      if (await isReachable(origin)) {
        writeCachedBase(origin);
        selectTransport("wifi");
        return origin;
      }
    }
  }
  const cached = readCachedBase();
  if (cached && (await isReachable(cached))) {
    selectTransport("wifi");
    return cached;
  }
  const winner = await Promise.any(
    CANDIDATES.map(async (base) => {
      if (!(await isReachable(base))) throw new Error(`unreachable: ${base}`);
      return base;
    })
  ).catch(() => null);
  if (winner) {
    writeCachedBase(winner);
    selectTransport("wifi");
  }
  return winner;
}

export async function isReachable(base: string): Promise<boolean> {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), PROBE_TIMEOUT_MS);
    const res = await fetch(`${base}/status`, { signal: ctrl.signal, cache: "no-store" });
    clearTimeout(timer);
    return res.ok;
  } catch {
    return false;
  }
}

export function readCachedBase(): PiBase | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(STORAGE_KEY);
}

export function writeCachedBase(base: PiBase): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, base);
}

export function clearCachedBase(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(STORAGE_KEY);
}

export async function waitForPi(timeoutMs: number = 60000): Promise<PiBase | null> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const found = await discoverPi();
    if (found) return found;
    await new Promise((r) => setTimeout(r, 2000));
  }
  return null;
}
