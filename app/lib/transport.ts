export type TransportKind = "wifi" | "bluetooth";

const STORAGE_KEY = "moo_transport";

export function getTransportKind(): TransportKind {
  if (typeof window === "undefined") return "wifi";
  return window.localStorage.getItem(STORAGE_KEY) === "bluetooth" ? "bluetooth" : "wifi";
}

export function selectTransport(kind: TransportKind): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, kind);
  window.dispatchEvent(new CustomEvent("moo-transport-change", { detail: kind }));
}

export function clearTransport(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(STORAGE_KEY);
}
