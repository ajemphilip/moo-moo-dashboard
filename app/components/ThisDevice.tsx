"use client";

// When this phone or laptop is Moo's AI, keep the engine running on every page
// of the Moo website and show a small status chip at the bottom.

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  canRunHere,
  crashedWhileLoading,
  startThisDevice,
  subscribe,
  thisDeviceWanted,
  type EngineState,
  type Quality,
} from "@/lib/lendEngine";
import { api } from "@/lib/api";

export function deviceName(): string {
  if (typeof navigator === "undefined") return "A browser";
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  if (/Android/.test(ua)) return "Android phone";
  if (/Mac/.test(ua)) return "Mac";
  if (/Windows/.test(ua)) return "Windows computer";
  return "A browser";
}

const LABEL: Record<string, string> = {
  chat: "writing",
  moderate: "checking",
  transcribe: "listening",
  speak: "speaking",
};

export function ThisDeviceEngine() {
  const [s, setS] = useState<EngineState | null>(null);

  useEffect(() => subscribe(setS), []);

  // Start again after a reload if this device was chosen and the Moo uses it.
  useEffect(() => {
    // If the browser died while loading last time, do not load again by
    // itself: that would close the page over and over. The AI engine screen
    // says what happened and offers smaller models.
    if (!thisDeviceWanted() || canRunHere() || crashedWhileLoading()) return;
    api
      .aiGet()
      .then((ai) => {
        if (ai.mode === "browser") startThisDevice(deviceName(), (ai.browser_quality as Quality) ?? "auto");
      })
      .catch(() => {});
  }, []);

  if (!s || s.status === "off") return null;
  const text =
    s.status === "loading"
      ? `Loading Moo's AI… ${Object.values(s.progress).filter((p) => p.done).length}/3`
      : s.status === "working"
        ? `This device is ${LABEL[s.current?.kind ?? ""] ?? "working"} for Moo`
        : s.status === "ready"
          ? `This device is Moo's AI · ${s.jobsDone} jobs`
          : s.note ?? "Moo's AI stopped";
  return (
    <Link
      href="/ai"
      className={`fixed bottom-3 left-1/2 -translate-x-1/2 z-50 rounded-full px-4 py-2 text-sm shadow-md ${
        s.status === "error" ? "bg-red-100 text-red-800" : "bg-moo-ink text-white"
      }`}
    >
      {s.status === "working" || s.status === "loading" ? "● " : ""}
      {text}
    </Link>
  );
}
