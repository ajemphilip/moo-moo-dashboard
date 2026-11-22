import { readCachedBase } from "@/lib/pi";

// Where the Moo is. Served by the Moo itself the links stay on this page's
// own address (so an https page downloads over https).
export function mooBase(): { links: string; http: string } {
  if (typeof window === "undefined") return { links: "", http: "http://moo.local:8000" };
  const fromMoo = process.env.NEXT_PUBLIC_GITHUB_PAGES !== "true" && !window.location.origin.includes(":3000");
  const base = fromMoo ? window.location.origin : readCachedBase() || "http://moo.local:8000";
  const host = base.replace(/^https?:\/\//, "").replace(/:\d+.*$/, "").replace(/\/.*$/, "");
  return { links: fromMoo ? "" : base, http: `http://${host}:8000` };
}

// The installer download that fits a computer, from the system its Moo Local reports.
export function installerFor(system: string | null | undefined): { id: "windows" | "mac" | "linux"; label: string } | null {
  const s = (system || "").toLowerCase();
  if (s.startsWith("windows")) return { id: "windows", label: "Windows" };
  if (s.startsWith("darwin") || s.startsWith("mac")) return { id: "mac", label: "Mac" };
  if (s.startsWith("linux")) return { id: "linux", label: "Linux" };
  return null;
}
