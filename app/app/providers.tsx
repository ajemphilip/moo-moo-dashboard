"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ThisDeviceEngine } from "@/components/ThisDevice";

// After an update the page's old code files are gone. A page the browser kept from before then loads nothing
// on the next screen and stays blank, as Safari on an iPhone did on GitHub Pages (22 November 2026). When a
// code file fails to load, the page reloads itself once, which fetches the new version.
const RELOADED_KEY = "moo_reloaded_for_update";

function isMissingCode(reason: unknown): boolean {
  const text = String((reason as { message?: string; name?: string })?.message ?? reason ?? "");
  const name = String((reason as { name?: string })?.name ?? "");
  return name === "ChunkLoadError" || /Loading (CSS )?chunk|ChunkLoadError|Failed to fetch dynamically imported module|Importing a module script failed/i.test(text);
}

function useReloadAfterUpdate() {
  useEffect(() => {
    const reload = () => {
      try {
        const last = Number(sessionStorage.getItem(RELOADED_KEY) || 0);
        if (Date.now() - last < 60000) return; // once a minute at most, so a real fault cannot loop
        sessionStorage.setItem(RELOADED_KEY, String(Date.now()));
      } catch {
        /* reload all the same */
      }
      window.location.reload();
    };
    const onError = (e: ErrorEvent) => {
      const target = e.target as HTMLElement | null;
      if (isMissingCode(e.error ?? e.message) || (target && (target.tagName === "SCRIPT" || target.tagName === "LINK"))) reload();
    };
    const onRejection = (e: PromiseRejectionEvent) => {
      if (isMissingCode(e.reason)) reload();
    };
    window.addEventListener("error", onError, true);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError, true);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);
}

export function Providers({ children }: { children: React.ReactNode }) {
  useReloadAfterUpdate();
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: 1, refetchOnWindowFocus: false },
        },
      })
  );
  return (
    <QueryClientProvider client={queryClient}>
      {children}
      <ThisDeviceEngine />
    </QueryClientProvider>
  );
}
